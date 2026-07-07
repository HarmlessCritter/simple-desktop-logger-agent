from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import pystray
import websockets
import winreg
from PIL import Image, ImageDraw

from activity_store import ActivityStore, serialize_focus, start_of_local_day_ms
from focus_watcher import FocusEventWatcher, FocusInfo, get_foreground_focus
from icon_provider import IconProvider


HOST = "127.0.0.1"
PORT = 17373
DASHBOARD_URL = "http://127.0.0.1:5173/dashboard"
APP_NAME = "Simple Desktop Logger Agent"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
LEGACY_RUN_KEY_NAMES = ("Task Tracker Agent",)
SINGLE_INSTANCE_MUTEX = "Local\\SimpleDesktopLoggerAgentSingleton"
ERROR_ALREADY_EXISTS = 183
AFK_TIMEOUT_MS = 30_000
IDLE_CHECK_INTERVAL_SECONDS = 0.25
FOCUS_SANITY_CHECK_INTERVAL_SECONDS = 2.0
STARTUP_REMINDER_MESSAGE_KO = (
    "Simple Desktop Logger가 기록을 시작합니다.\n"
    "컴퓨터를 켤 때 자동으로 시작하려면 트레이 메뉴에서 설정할 수 있어요."
)
STARTUP_REMINDER_TITLE_KO = "Windows 시작 시 자동 실행이 꺼져 있어요"
MENU_OPEN_DASHBOARD = "활동기록 확인 (Web)"
MENU_INFO = "정보"
MENU_RUN_AT_STARTUP = "Windows 시작 시 자동 실행"
MENU_EXIT = "종료"
INFO_TITLE = "Simple Desktop Logger 정보"
INFO_SOURCE_URL = "https://github.com/HarmlessCritter/simple-desktop-logger-agent"
INFO_MESSAGE = (
    "Simple Desktop Logger 정보\n\n"
    "이 프로그램에는 광고, 무단 데이터 수집, 그리드 등 본연의 기능과 관련 없는 "
    "부가기능이 존재하지 않습니다.\n\n"
    "소스코드는 GitHub에 공개되어 누구나 확인할 수 있습니다.\n"
    "소스코드 저장소 : https://github.com/HarmlessCritter/simple-desktop-logger-agent\n\n"
    "본 프로그램은 별도 허가 없이 자유롭게 공유 및 배포할 수 있습니다.\n"
    "단, 프로그램을 수정하거나 변조하는 행위는 금지합니다."
)

STARTUP_REMINDER_MESSAGE_KO = (
    "Simple Desktop Logger가 기록을 시작합니다.\n"
    "컴퓨터를 켤 때 자동으로 시작하려면 트레이 메뉴에서 설정할 수 있어요."
)
STARTUP_REMINDER_TITLE_KO = "Windows 시작 시 자동 실행이 꺼져 있어요"
MB_OK = 0x00000000
MB_ICONINFORMATION = 0x00000040
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000


kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.GetLastError.restype = ctypes.c_ulong
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_bool
user32 = ctypes.windll.user32
user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
user32.MessageBoxW.restype = ctypes.c_int


class LastInputInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInputInfo)]
user32.GetLastInputInfo.restype = ctypes.c_bool
kernel32.GetTickCount.restype = ctypes.c_ulong


def now_ms() -> int:
    return int(time.time() * 1000)


def get_idle_ms() -> int:
    info = LastInputInfo()
    info.cbSize = ctypes.sizeof(LastInputInfo)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError()

    return int((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF)


def show_info_dialog() -> None:
    win = tk.Tk()
    win.withdraw()
    win.title(INFO_TITLE)
    win.resizable(False, False)
    win.configure(bg="#ffffff")

    default_font = tkfont.nametofont("TkDefaultFont")
    title_font = default_font.copy()
    title_font.configure(weight="bold")
    link_font = default_font.copy()
    link_font.configure(underline=True)

    body_pad_x = 30 * 2
    icon_column_width = 28 + 17
    source_label_text = "소스코드 저장소 : "
    source_row_width = default_font.measure(source_label_text) + link_font.measure(INFO_SOURCE_URL)
    dialog_width = max(660, body_pad_x + icon_column_width + source_row_width + 40)
    dialog_height = 281
    win.geometry(f"{dialog_width}x{dialog_height}")

    body = tk.Frame(win, bg="#ffffff")
    body.pack(fill="both", expand=True, padx=30, pady=(24, 0))

    icon_canvas = tk.Canvas(body, width=28, height=28, bg="#ffffff", highlightthickness=0)
    icon_canvas.grid(row=0, column=0, rowspan=4, sticky="n", padx=(0, 17), pady=(0, 0))
    icon_canvas.create_oval(1, 1, 27, 27, fill="#0078d4", outline="#0078d4")
    icon_canvas.create_text(14, 14, text="i", fill="white", font=("Segoe UI", 15, "bold"))

    content = tk.Frame(body, bg="#ffffff")
    content.grid(row=0, column=1, sticky="nw")

    tk.Label(content, text="Simple Desktop Logger 정보", bg="#ffffff", fg="#000000", font=title_font).pack(anchor="w")
    tk.Label(
        content,
        text=(
            "이 프로그램에는 광고, 무단 데이터 수집, 그리드 등 본연의 기능과 관련 없는\n"
            "부가기능이 존재하지 않습니다."
        ),
        bg="#ffffff",
        fg="#000000",
        justify="left",
    ).pack(anchor="w", pady=(17, 0))
    tk.Label(
        content,
        text="소스코드는 GitHub에 공개되어 누구나 확인할 수 있습니다.",
        bg="#ffffff",
        fg="#000000",
        justify="left",
    ).pack(anchor="w", pady=(14, 0))

    source_row = tk.Frame(content, bg="#ffffff")
    source_row.pack(anchor="w")
    tk.Label(source_row, text=source_label_text, bg="#ffffff", fg="#000000").pack(side="left")
    link = tk.Label(
        source_row,
        text=INFO_SOURCE_URL,
        bg="#ffffff",
        fg="#005a9e",
        cursor="hand2",
        font=link_font,
    )
    link.pack(side="left")
    link.bind("<Button-1>", lambda _event: webbrowser.open(INFO_SOURCE_URL))

    tk.Label(
        content,
        text=(
            "본 프로그램은 별도 허가 없이 자유롭게 공유 및 배포할 수 있습니다.\n"
            "단, 프로그램을 수정하거나 변조하는 행위는 금지합니다."
        ),
        bg="#ffffff",
        fg="#000000",
        justify="left",
    ).pack(anchor="w", pady=(16, 0))

    button_row = tk.Frame(win, bg="#ffffff")
    button_row.pack(fill="x", side="bottom", padx=16, pady=(0, 12))
    ok_button = tk.Button(button_row, text="확인", width=9, command=win.destroy)
    ok_button.pack(side="right")
    ok_button.focus_set()
    win.bind("<Return>", lambda _event: win.destroy())
    win.bind("<Escape>", lambda _event: win.destroy())

    win.update_idletasks()
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    x = max(0, (screen_width - dialog_width) // 2)
    y = max(0, (screen_height - dialog_height) // 2)
    win.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")
    win.attributes("-topmost", True)
    win.after(250, lambda: win.attributes("-topmost", False))
    win.deiconify()
    win.lift()
    win.focus_force()
    win.mainloop()


def show_startup_reminder_toast() -> None:
    root = tk.Tk()
    root.withdraw()

    toast = tk.Toplevel(root)
    toast.title(STARTUP_REMINDER_TITLE_KO)
    toast.overrideredirect(True)
    toast.attributes("-topmost", True)
    toast.configure(bg="#ffffff")

    title_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
    body_font = tkfont.Font(family="Segoe UI", size=9)

    accent_width = 5
    body_pad_left = 15
    body_pad_right = 36
    content_min_width = 270
    content_max_width = 360
    close_button_size = 28
    close_button_right = 14
    close_button_top = 8

    body_lines = STARTUP_REMINDER_MESSAGE_KO.splitlines() or [STARTUP_REMINDER_MESSAGE_KO]
    body_natural_width = max(body_font.measure(line) for line in body_lines)
    title_natural_width = title_font.measure(STARTUP_REMINDER_TITLE_KO) + close_button_size + 14
    content_width = max(content_min_width, min(content_max_width, max(body_natural_width, title_natural_width)))
    width = accent_width + body_pad_left + content_width + body_pad_right
    margin_x = 18
    margin_y = 54

    container = tk.Frame(toast, bg="#ffffff", highlightthickness=1, highlightbackground="#d0d7de")
    container.pack(fill="both", expand=True)

    accent = tk.Frame(container, bg="#286fa5", width=accent_width)
    accent.pack(side="left", fill="y")

    body = tk.Frame(container, bg="#ffffff")
    body.pack(side="left", fill="both", expand=True, padx=(body_pad_left, body_pad_right), pady=15)

    tk.Label(
        body,
        text=STARTUP_REMINDER_TITLE_KO,
        bg="#ffffff",
        fg="#111827",
        font=title_font,
        anchor="w",
        wraplength=content_width,
    ).pack(fill="x")
    tk.Label(
        body,
        text=STARTUP_REMINDER_MESSAGE_KO,
        bg="#ffffff",
        fg="#374151",
        font=body_font,
        justify="left",
        anchor="w",
        wraplength=content_width,
    ).pack(fill="x", pady=(10, 0))

    close_button = tk.Button(
        container,
        text="X",
        command=root.destroy,
        bg="#ffffff",
        fg="#4b5563",
        activebackground="#e5e7eb",
        activeforeground="#111827",
        cursor="hand2",
        bd=0,
        font=("Segoe UI", 9, "bold"),
        padx=10,
        pady=5,
    )
    close_button.place(
        x=width - close_button_right - close_button_size,
        y=close_button_top,
        width=close_button_size,
        height=close_button_size,
    )

    toast.update_idletasks()
    height = max(120, container.winfo_reqheight())
    screen_width = toast.winfo_screenwidth()
    screen_height = toast.winfo_screenheight()
    x = screen_width - width - margin_x
    y = screen_height - height - margin_y
    toast.geometry(f"{width}x{height}+{x}+{y}")
    toast.lift()
    toast.focus_force()
    root.mainloop()


class SingleInstanceLock:
    def __init__(self, name: str) -> None:
        self.name = name
        self.handle: int | None = None

    def acquire(self) -> bool:
        self.handle = kernel32.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise ctypes.WinError()

        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


class ActivityTracker:
    def __init__(self, store: ActivityStore, icon_provider: IconProvider) -> None:
        self.store = store
        self.icon_provider = icon_provider
        self.current: FocusInfo | None = None
        self.current_started_ms: int | None = None
        self.tracking = True
        self.afk = False
        self.ignored = False
        self.lock = threading.Lock()

    def reset(self) -> None:
        with self.lock:
            self.current = None
            self.current_started_ms = None
            self.afk = False
            self.ignored = False

    def start(self) -> None:
        with self.lock:
            self.tracking = True

    def stop(self) -> None:
        with self.lock:
            self._finalize_current_locked(now_ms())
            self.tracking = False
            self.afk = False
            self.ignored = False
            self.current = None
            self.current_started_ms = None

    def focus_changed(self, focus: FocusInfo) -> dict[str, Any] | None:
        with self.lock:
            if not self.tracking or self.afk:
                return None

            change_ms = now_ms()
            previous = self.current
            previous_elapsed_ms = self._finalize_current_locked(change_ms)

            if self.store.is_activity_ignored(focus):
                self.current = None
                self.current_started_ms = None
                self.ignored = True
                return {
                    "type": "ignored_focus",
                    "at": change_ms,
                    "focus": self._serialize_focus_with_icon_locked(focus),
                    "previous": self._serialize_focus_with_icon_locked(previous) if previous else None,
                    "previousElapsedMs": previous_elapsed_ms,
                    "snapshot": self._snapshot_locked(change_ms),
                }

            self.ignored = False
            self.current = focus
            self.current_started_ms = change_ms

            return {
                "type": "focus_changed",
                "at": change_ms,
                "focus": self._serialize_focus_with_icon_locked(focus),
                "previous": self._serialize_focus_with_icon_locked(previous) if previous else None,
                "previousElapsedMs": previous_elapsed_ms,
                "snapshot": self._snapshot_locked(change_ms),
            }

    def enter_afk(self, idle_ms: int) -> dict[str, Any] | None:
        with self.lock:
            if not self.tracking or self.afk:
                return None

            afk_started_ms = now_ms() - max(0, idle_ms - AFK_TIMEOUT_MS)
            self._finalize_current_locked(afk_started_ms)
            self.current = None
            self.current_started_ms = None
            self.afk = True
            self.ignored = False

            return {
                "type": "afk_started",
                "at": afk_started_ms,
                "idleMs": idle_ms,
                "snapshot": self._snapshot_locked(afk_started_ms),
            }

    def leave_afk(self, focus: FocusInfo | None) -> dict[str, Any] | None:
        with self.lock:
            if not self.tracking or not self.afk:
                return None

            resumed_ms = now_ms()
            self.afk = False
            if focus and self.store.is_activity_ignored(focus):
                self.ignored = True
                self.current = None
                self.current_started_ms = None
            else:
                self.ignored = False
                self.current = focus
                self.current_started_ms = resumed_ms if focus else None

            return {
                "type": "afk_ended",
                "at": resumed_ms,
                "focus": self._serialize_focus_with_icon_locked(focus) if focus else None,
                "snapshot": self._snapshot_locked(resumed_ms),
            }

    def ignore_activity(self, activity_key: str, display_name: str) -> dict[str, Any]:
        with self.lock:
            self.store.ignore_activity(activity_key, display_name)
            timestamp_ms = now_ms()

            if self.current and self.store.get_activity_key(self.current) == activity_key:
                self._finalize_current_locked(timestamp_ms)
                self.current = None
                self.current_started_ms = None
                self.ignored = True

            return {
                "type": "activity_ignored",
                "at": timestamp_ms,
                "activityKey": activity_key,
                "displayName": display_name,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def unignore_activity(self, activity_key: str) -> dict[str, Any]:
        with self.lock:
            self.store.unignore_activity(activity_key)
            timestamp_ms = now_ms()
            focus = get_foreground_focus()

            if (
                self.tracking
                and not self.afk
                and focus
                and self.store.get_activity_key(focus) == activity_key
            ):
                self.ignored = False
                self.current = focus
                self.current_started_ms = timestamp_ms

            return {
                "type": "activity_unignored",
                "at": timestamp_ms,
                "activityKey": activity_key,
                "focus": self._serialize_focus_with_icon_locked(focus) if focus else None,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def delete_activity(self, activity_key: str, start_ms: int, end_ms: int) -> dict[str, Any]:
        with self.lock:
            timestamp_ms = now_ms()
            deleted_current = False

            if self.current and self.store.get_activity_key(self.current) == activity_key:
                self._finalize_current_locked(timestamp_ms)
                if self.current_started_ms is not None and timestamp_ms >= start_ms and timestamp_ms < end_ms:
                    deleted_current = True

            deleted_count = self.store.delete_activity_between(activity_key, start_ms, end_ms)

            if deleted_current:
                self.current_started_ms = timestamp_ms

            return {
                "type": "activity_deleted",
                "at": timestamp_ms,
                "activityKey": activity_key,
                "deletedCount": deleted_count,
                "snapshot": self._snapshot_locked(timestamp_ms, start_ms, end_ms),
            }

    def snapshot(self, start_ms: int | None = None, end_ms: int | None = None) -> dict[str, Any]:
        with self.lock:
            return self._snapshot_locked(now_ms(), start_ms, end_ms)

    def _finalize_current_locked(self, ended_at: int) -> int:
        if not self.current or self.current_started_ms is None:
            return 0

        elapsed_ms = max(0, ended_at - self.current_started_ms)
        self.store.insert_session(self.current, self.current_started_ms, ended_at)
        self.current_started_ms = ended_at
        return elapsed_ms

    def _snapshot_locked(
        self,
        timestamp_ms: int,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> dict[str, Any]:
        period_start_ms = start_ms if start_ms is not None else start_of_local_day_ms()
        totals = self.store.summary_between(period_start_ms, end_ms)
        self._add_icons_to_totals_locked(totals)
        return {
            "type": "snapshot",
            "at": timestamp_ms,
            "periodStartMs": period_start_ms,
            "periodEndMs": end_ms,
            "tracking": self.tracking,
            "afk": self.afk,
            "ignored": self.ignored,
            "current": self._serialize_focus_with_icon_locked(self.current) if self.current else None,
            "currentStartedAt": self.current_started_ms,
            "totals": totals,
            "ignoredActivities": self.store.ignored_activities(),
            "recentSessions": self.store.recent_sessions(),
        }

    def _serialize_focus_with_icon_locked(self, focus: FocusInfo | None) -> dict[str, Any] | None:
        serialized = serialize_focus(focus)
        if serialized is None:
            return None

        self._add_icon_to_focus_locked(serialized)
        return serialized

    def _add_icons_to_totals_locked(self, totals: dict[str, dict[str, Any]]) -> None:
        for item in totals.values():
            focus = item.get("focus")
            if isinstance(focus, dict):
                self._add_icon_to_focus_locked(focus)

    def _add_icon_to_focus_locked(self, focus: dict[str, Any]) -> None:
        icon_data_url = self.icon_provider.get_icon_data_url(
            focus.get("process_path"),
            focus.get("process_name"),
        )
        if icon_data_url:
            focus["icon_data_url"] = icon_data_url


class AgentWebSocketServer:
    def __init__(self) -> None:
        self.store = ActivityStore()
        self.icon_provider = IconProvider()
        self.tracker = ActivityTracker(self.store, self.icon_provider)
        self.clients: set[Any] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.shutdown_event: asyncio.Event | None = None
        self.watch_thread: threading.Thread | None = None
        self.idle_thread: threading.Thread | None = None
        self.focus_sanity_thread: threading.Thread | None = None
        self.last_focus_key: tuple[int, str, str] | None = None
        self.last_focus_lock = threading.Lock()
        self.watch_stop_event = threading.Event()
        self.stopping = False

    async def run(self, host: str, port: int) -> None:
        self.loop = asyncio.get_running_loop()
        self.shutdown_event = asyncio.Event()
        self.start_tracking()
        async with websockets.serve(self.handle_client, host, port):
            print(f"Simple Desktop Logger agent server listening on ws://{host}:{port}")
            await self.shutdown_event.wait()

    def stop_server(self) -> None:
        if self.stopping:
            return

        self.stopping = True
        self.stop_tracking()
        if self.loop and self.shutdown_event:
            try:
                self.loop.call_soon_threadsafe(self.shutdown_event.set)
            except RuntimeError:
                pass

    async def handle_client(self, websocket) -> None:
        self.clients.add(websocket)
        await self.send(websocket, {"type": "agent_ready", "snapshot": self.tracker.snapshot()})

        try:
            async for raw_message in websocket:
                await self.handle_message(websocket, raw_message)
        finally:
            self.clients.discard(websocket)

    async def handle_message(self, websocket, raw_message: str) -> None:
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            await self.send(websocket, {"type": "error", "message": "Invalid JSON message."})
            return

        message_type = message.get("type")
        if message_type == "start_tracking":
            self.start_tracking()
            await self.broadcast({"type": "tracking_started", "snapshot": self.tracker.snapshot()})
        elif message_type == "stop_tracking":
            self.stop_tracking()
            await self.broadcast({"type": "tracking_stopped", "snapshot": self.tracker.snapshot()})
        elif message_type == "get_snapshot":
            start_ms = self._optional_int(message.get("startMs"))
            end_ms = self._optional_int(message.get("endMs"))
            await self.send(websocket, self.tracker.snapshot(start_ms, end_ms))
        elif message_type == "ignore_activity":
            display_name = str(message.get("displayName") or "").strip()
            activity_key = str(message.get("activityKey") or display_name.lower()).strip().lower()
            if not activity_key or not display_name:
                await self.send(websocket, {"type": "error", "message": "Missing activityKey or displayName."})
                return

            await self.broadcast(self.tracker.ignore_activity(activity_key, display_name))
        elif message_type == "unignore_activity":
            activity_key = str(message.get("activityKey") or "").strip().lower()
            if not activity_key:
                await self.send(websocket, {"type": "error", "message": "Missing activityKey."})
                return

            await self.broadcast(self.tracker.unignore_activity(activity_key))
        elif message_type == "delete_activity":
            activity_key = str(message.get("activityKey") or "").strip().lower()
            start_ms = self._optional_int(message.get("startMs"))
            end_ms = self._optional_int(message.get("endMs"))
            if not activity_key or start_ms is None or end_ms is None or end_ms <= start_ms:
                await self.send(websocket, {"type": "error", "message": "Missing or invalid delete range."})
                return

            await self.broadcast(self.tracker.delete_activity(activity_key, start_ms, end_ms))
        else:
            await self.send(websocket, {"type": "error", "message": f"Unknown message type: {message_type}"})

    def _optional_int(self, value: Any) -> int | None:
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def start_tracking(self) -> None:
        if self.stopping:
            return

        if self.watch_thread and self.watch_thread.is_alive():
            self.tracker.start()
            self.start_idle_monitor()
            self.start_focus_sanity_monitor()
            return

        self.tracker.start()
        self.watch_stop_event.clear()
        self.watch_thread = threading.Thread(target=self.run_watcher, daemon=True)
        self.watch_thread.start()
        self.start_idle_monitor()
        self.start_focus_sanity_monitor()

    def stop_tracking(self) -> None:
        self.watch_stop_event.set()
        self.tracker.stop()

    def run_watcher(self) -> None:
        watcher = FocusEventWatcher(self.handle_focus_changed)
        watcher.run(self.watch_stop_event)

    def start_idle_monitor(self) -> None:
        if self.idle_thread and self.idle_thread.is_alive():
            return

        self.idle_thread = threading.Thread(target=self.run_idle_monitor, daemon=True)
        self.idle_thread.start()

    def start_focus_sanity_monitor(self) -> None:
        if self.focus_sanity_thread and self.focus_sanity_thread.is_alive():
            return

        self.focus_sanity_thread = threading.Thread(target=self.run_focus_sanity_monitor, daemon=True)
        self.focus_sanity_thread.start()

    def run_idle_monitor(self) -> None:
        while not self.watch_stop_event.wait(IDLE_CHECK_INTERVAL_SECONDS):
            try:
                idle_ms = get_idle_ms()
            except Exception as exc:
                print(f"Unable to read idle time: {exc}")
                continue

            if idle_ms >= AFK_TIMEOUT_MS:
                self.broadcast_from_thread(self.tracker.enter_afk(idle_ms))
            else:
                focus = get_foreground_focus()
                self.broadcast_from_thread(self.tracker.leave_afk(focus))

    def run_focus_sanity_monitor(self) -> None:
        while not self.watch_stop_event.wait(FOCUS_SANITY_CHECK_INTERVAL_SECONDS):
            focus = get_foreground_focus()
            if not focus:
                continue

            with self.last_focus_lock:
                if focus.key == self.last_focus_key:
                    continue

            self.handle_focus_changed(focus)

    def handle_focus_changed(self, focus: FocusInfo) -> None:
        if self.stopping:
            return

        self._remember_focus(focus)
        self.broadcast_from_thread(self.tracker.focus_changed(focus))

    def _remember_focus(self, focus: FocusInfo | None) -> None:
        if not focus:
            return

        with self.last_focus_lock:
            self.last_focus_key = focus.key

    def broadcast_from_thread(self, event: dict[str, Any] | None) -> None:
        if not event or not self.loop:
            return

        if self.stopping:
            return

        try:
            self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.broadcast(event)))
        except RuntimeError:
            pass

    async def send(self, websocket, payload: dict[str, Any]) -> None:
        await websocket.send(json.dumps(payload))

    async def broadcast(self, payload: dict[str, Any]) -> None:
        if not self.clients:
            return

        encoded = json.dumps(payload)
        disconnected = []
        for client in self.clients:
            try:
                await client.send(encoded)
            except websockets.ConnectionClosed:
                disconnected.append(client)

        for client in disconnected:
            self.clients.discard(client)


def get_startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    script_path = Path(__file__).resolve()
    return f'"{sys.executable}" "{script_path}"'


def is_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value == get_startup_command()
    except FileNotFoundError:
        return False


def cleanup_legacy_startup_entries() -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        for name in LEGACY_RUN_KEY_NAMES:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


def set_startup_enabled(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, get_startup_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def create_tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=(40, 111, 165, 255))
    draw.rectangle((18, 18, 46, 25), fill=(255, 255, 255, 255))
    draw.rectangle((18, 30, 39, 37), fill=(255, 255, 255, 230))
    draw.rectangle((18, 42, 49, 49), fill=(255, 255, 255, 210))
    return image


class TrayAgentApp:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.server = AgentWebSocketServer()
        self.server_thread = threading.Thread(target=self._run_server, daemon=True)
        self.icon = pystray.Icon(
            APP_NAME,
            create_tray_image(),
            APP_NAME,
            self._build_menu(),
        )

    def run(self) -> None:
        self.server_thread.start()
        self.icon.run(setup=self._after_tray_ready)
        os._exit(0)

    def _after_tray_ready(self, icon: pystray.Icon) -> None:
        icon.visible = True
        cleanup_legacy_startup_entries()

        if is_startup_enabled():
            return

        threading.Thread(target=self._show_startup_reminder, daemon=True).start()

    def _show_startup_reminder(self) -> None:
        try:
            show_startup_reminder_toast()
        except Exception as exc:
            print(f"Unable to show startup reminder: {exc}")
            pass

    def _run_server(self) -> None:
        asyncio.run(self.server.run(self.host, self.port))

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(MENU_INFO, self._show_info),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(MENU_OPEN_DASHBOARD, self._open_dashboard),
            pystray.MenuItem(
                MENU_RUN_AT_STARTUP,
                self._toggle_startup,
                checked=lambda _item: is_startup_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(MENU_EXIT, self._exit),
        )

    def _open_dashboard(self) -> None:
        webbrowser.open(DASHBOARD_URL)

    def _show_info(self) -> None:
        try:
            show_info_dialog()
        except Exception:
            user32.MessageBoxW(None, INFO_MESSAGE, INFO_TITLE, MB_OK | MB_ICONINFORMATION)

    def _toggle_startup(self) -> None:
        set_startup_enabled(not is_startup_enabled())
        self.icon.update_menu()

    def _exit(self) -> None:
        threading.Timer(1.5, lambda: os._exit(0)).start()
        self.server.stop_server()
        self.icon.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Simple Desktop Logger WebSocket agent.")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-tray", action="store_true", help="Run in the foreground without a tray icon.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    single_instance_lock = SingleInstanceLock(SINGLE_INSTANCE_MUTEX)
    if not single_instance_lock.acquire():
        return 0

    try:
        if args.no_tray:
            server = AgentWebSocketServer()
            try:
                asyncio.run(server.run(args.host, args.port))
            except KeyboardInterrupt:
                print("\nSimple Desktop Logger agent server stopped.")
                return 0
        else:
            TrayAgentApp(args.host, args.port).run()

        return 0
    finally:
        single_instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
