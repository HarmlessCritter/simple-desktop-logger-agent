from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from ctypes import wintypes

try:
    import pythoncom
    import psutil
    import win32gui
    import win32process
except ImportError as exc:
    missing = exc.name or "required package"
    print(
        f"Missing dependency: {missing}\n"
        "Install agent dependencies with:\n"
        "  python -m pip install -r agent/requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


@dataclass(frozen=True)
class FocusInfo:
    hwnd: int
    pid: int
    process_name: str
    process_path: str
    window_class: str
    window_title: str
    display_name: str

    @property
    def key(self) -> tuple[int, int, str, str]:
        return (self.hwnd, self.pid, self.window_class, self.window_title)


WINDOWS_OPERATION = "\uc708\ub3c4\uc6b0 \uc870\uc791"
FILE_EXPLORER = WINDOWS_OPERATION
WINDOWS_OPERATION_PROCESSES = {
    "searchapp.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "textinputhost.exe",
    "runtimebroker.exe",
}


def get_explorer_display_name(window_class: str, title: str) -> str:
    if window_class in {"CabinetWClass", "ExploreWClass"}:
        return FILE_EXPLORER

    return WINDOWS_OPERATION


def get_display_name(process_name: str, window_class: str, _title: str = "") -> str:
    normalized_process = process_name.lower()
    if normalized_process == "explorer.exe":
        return get_explorer_display_name(window_class, title)

    if normalized_process in WINDOWS_OPERATION_PROCESSES:
        return WINDOWS_OPERATION

    return process_name


def get_foreground_focus() -> FocusInfo | None:
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None

    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    if not pid:
        return None
    if pid == os.getpid():
        return None

    window_class = win32gui.GetClassName(hwnd)

    try:
        process = psutil.Process(pid)
        process_name = process.name()
        try:
            process_path = process.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process_path = ""
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        process_name = f"pid:{pid}"
        process_path = ""

    display_name = get_display_name(process_name, window_class)

    return FocusInfo(
        hwnd=hwnd,
        pid=pid,
        process_name=process_name,
        process_path=process_path,
        window_class=window_class,
        window_title="",
        display_name=display_name,
    )


def format_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def format_focus_line(focus: FocusInfo) -> str:
    return (
        f"[{format_timestamp()}] "
        f"{focus.display_name} | {focus.process_name} | "
        f"{focus.window_title}"
    )


WinEventProc = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD,
)

user32 = ctypes.windll.user32
user32.SetWinEventHook.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
    WinEventProc,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.UnhookWinEvent.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_NAMECHANGE = 0x800C
OBJID_WINDOW = 0
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002


class FocusEventWatcher:
    def __init__(self, on_focus_changed, on_window_name_changed=None) -> None:
        self.on_focus_changed = on_focus_changed
        self.on_window_name_changed = on_window_name_changed
        self.previous: FocusInfo | None = None
        self.foreground_hook: wintypes.HANDLE | None = None
        self.name_hook: wintypes.HANDLE | None = None
        self._callback = WinEventProc(self._handle_event)

    def run(self, stop_event: threading.Event) -> None:
        self._emit_current_focus()
        self.foreground_hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,
            self._callback,
            0,
            0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )
        self.name_hook = user32.SetWinEventHook(
            EVENT_OBJECT_NAMECHANGE,
            EVENT_OBJECT_NAMECHANGE,
            None,
            self._callback,
            0,
            0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )

        if not self.foreground_hook or not self.name_hook:
            raise ctypes.WinError()

        try:
            while not stop_event.is_set():
                pythoncom.PumpWaitingMessages()
                stop_event.wait(0.05)
        finally:
            if self.foreground_hook:
                user32.UnhookWinEvent(self.foreground_hook)
            if self.name_hook:
                user32.UnhookWinEvent(self.name_hook)
            self.foreground_hook = None
            self.name_hook = None

    def _handle_event(
        self,
        _hook,
        event,
        hwnd,
        id_object,
        _id_child,
        _event_thread,
        _event_time,
    ) -> None:
        if event == EVENT_SYSTEM_FOREGROUND:
            self._emit_current_focus()
        elif event == EVENT_OBJECT_NAMECHANGE and id_object == OBJID_WINDOW:
            foreground_hwnd = user32.GetForegroundWindow()
            if hwnd and hwnd == foreground_hwnd:
                self._emit_window_name_changed()

    def _emit_current_focus(self) -> None:
        current = get_foreground_focus()
        if current and (self.previous is None or current.key != self.previous.key):
            self.on_focus_changed(current)
            self.previous = current

    def _emit_window_name_changed(self) -> None:
        if not self.on_window_name_changed:
            return

        current = get_foreground_focus()
        if not current:
            return

        self.on_window_name_changed(current)
        self.previous = current


def watch_focus() -> None:
    stop_event = threading.Event()

    print("Focus watcher started. Waiting for Windows foreground events. Press Ctrl+C to stop.")

    watcher = FocusEventWatcher(lambda focus: print(format_focus_line(focus), flush=True))
    watcher.run(stop_event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print the active Windows process and window title on focus changes."
    )
    return parser.parse_args()


def main() -> int:
    parse_args()

    try:
        watch_focus()
    except KeyboardInterrupt:
        print("\nFocus watcher stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
