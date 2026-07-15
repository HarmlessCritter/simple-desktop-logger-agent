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
import traceback
import webbrowser
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pystray
import websockets
import winreg
from PIL import Image, ImageDraw

from activity_store import ActivityStore, serialize_focus, start_of_local_day_ms
from binding_summary import (
    apply_bindings_to_totals,
    binding_target_for_source,
    current_summary_target,
    ensure_current_group_item,
)
from browser_detail_snapshot import current_browser_detail_payload, inject_current_browser_detail
from debug_console import show_debug_console
from diagnostic_events import DiagnosticEventRecorder
from browser_tracking import (
    ChromeAddressBarEventSubscription,
    NORMAL_BROWSER_STATUS,
    browser_detail_from_url,
    browser_detail_key,
    chrome_privacy_mode,
    is_supported_browser,
    read_browser_detail,
    serialize_browser_detail,
    site_display_name,
)
from focus_watcher import FocusEventWatcher, FocusInfo, get_foreground_focus
from i18n import LANGUAGE_LABELS, get_language, set_language, text
from icon_provider import IconProvider
from snapshot_delta import build_snapshot_delta


HOST = "127.0.0.1"
PORT = 17373
DASHBOARD_URL = "http://127.0.0.1:5173/dashboard"
APP_NAME = "Simple Desktop Logger Agent"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
LEGACY_RUN_KEY_NAMES = ("Task Tracker Agent",)
SINGLE_INSTANCE_MUTEX = "Local\\SimpleDesktopLoggerAgentSingleton"
ERROR_ALREADY_EXISTS = 183
AFK_TIMEOUT_MS = 60_000
IDLE_CHECK_INTERVAL_SECONDS = 1.0
FOCUS_SANITY_CHECK_INTERVAL_SECONDS = 2.0
FOCUS_SANITY_MONITOR_ENABLED = True
MAX_TIMELINE_RANGE_MS = 7 * 24 * 60 * 60 * 1000
INFO_SOURCE_URL = "https://github.com/HarmlessCritter/simple-desktop-logger-agent"
MB_OK = 0x00000000
MB_ICONINFORMATION = 0x00000040
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000
KST = timezone(timedelta(hours=9))
PRIVATE_BROWSER_WINDOW_TITLE = ""


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


def format_debug_timestamp(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "-"

    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).astimezone(KST).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def get_idle_ms() -> int:
    info = LastInputInfo()
    info.cbSize = ctypes.sizeof(LastInputInfo)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError()

    return int((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF)


def privacy_safe_focus(
    focus: FocusInfo | None,
    privacy_mode: str | None = None,
) -> FocusInfo | None:
    """Remove private Chrome page metadata before it enters tracker state."""
    if not focus:
        return focus
    focus = replace(focus, window_title="")
    if not is_supported_browser(focus.process_name):
        return focus

    resolved_privacy_mode = privacy_mode or chrome_privacy_mode(focus)
    if resolved_privacy_mode == NORMAL_BROWSER_STATUS:
        return focus

    return replace(focus, window_title=PRIVATE_BROWSER_WINDOW_TITLE)


def build_info_message() -> str:
    return (
        f"{text('info.heading')}\n\n"
        f"{text('info.safety')}\n\n"
        f"{text('info.source_notice')}\n"
        f"{text('info.source_label')}{INFO_SOURCE_URL}\n\n"
        f"{text('info.permission')}"
    )


def wrap_text_by_words(value: str, font: tkfont.Font, max_width: int) -> str:
    wrapped_lines: list[str] = []
    for raw_line in value.splitlines() or [value]:
        words = raw_line.split(" ")
        current = ""
        for word in words:
            if not word:
                continue
            candidate = word if not current else f"{current} {word}"
            if current and font.measure(candidate) > max_width:
                wrapped_lines.append(current)
                current = word
            else:
                current = candidate
        wrapped_lines.append(current)
    return "\n".join(wrapped_lines)


def show_info_dialog() -> None:
    win = tk.Tk()
    win.withdraw()
    win.title(text("info.title"))
    win.resizable(False, False)
    win.configure(bg="#ffffff")

    default_font = tkfont.nametofont("TkDefaultFont")
    title_font = default_font.copy()
    title_font.configure(weight="bold")
    link_font = default_font.copy()
    link_font.configure(underline=True)

    body_pad_x = 30 * 2
    body_pad_y = 24 + 26
    icon_column_width = 28 + 17
    source_label_text = text("info.source_label")
    source_row_width = default_font.measure(source_label_text) + link_font.measure(INFO_SOURCE_URL)
    body_texts = [text("info.heading"), text("info.safety"), text("info.source_notice"), text("info.permission")]
    min_content_width = 430
    right_safety_padding = 16
    widest_body_line = max(
        default_font.measure(line)
        for value in body_texts
        for line in (value.splitlines() or [value])
    )
    screen_width = win.winfo_screenwidth()
    max_content_width = min(760, max(min_content_width, screen_width - body_pad_x - icon_column_width - 100))
    content_width = max(min_content_width, min(max_content_width, widest_body_line), source_row_width)
    dialog_width = body_pad_x + icon_column_width + content_width + right_safety_padding

    body = tk.Frame(win, bg="#ffffff")
    body.pack(fill="both", expand=True, padx=30, pady=(24, 26))

    icon_canvas = tk.Canvas(body, width=28, height=28, bg="#ffffff", highlightthickness=0)
    icon_canvas.grid(row=0, column=0, rowspan=4, sticky="n", padx=(0, 17), pady=(0, 0))
    icon_canvas.create_oval(1, 1, 27, 27, fill="#0078d4", outline="#0078d4")
    icon_canvas.create_text(14, 14, text="i", fill="white", font=("Segoe UI", 15, "bold"))

    content = tk.Frame(body, bg="#ffffff")
    content.grid(row=0, column=1, sticky="nw")

    tk.Label(content, text=text("info.heading"), bg="#ffffff", fg="#000000", font=title_font).pack(anchor="w")
    tk.Label(
        content,
        text=wrap_text_by_words(text("info.safety"), default_font, content_width),
        bg="#ffffff",
        fg="#000000",
        justify="left",
    ).pack(anchor="w", pady=(17, 0))
    tk.Label(
        content,
        text=wrap_text_by_words(text("info.source_notice"), default_font, content_width),
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
        text=wrap_text_by_words(text("info.permission"), default_font, content_width),
        bg="#ffffff",
        fg="#000000",
        justify="left",
    ).pack(anchor="w", pady=(16, 0))

    win.bind("<Escape>", lambda _event: win.destroy())

    win.update_idletasks()
    screen_height = win.winfo_screenheight()
    dialog_height = max(180, body.winfo_reqheight() + body_pad_y)
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
    startup_title = text("startup.title")
    startup_message = text("startup.message")
    toast.title(startup_title)
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

    body_lines = startup_message.splitlines() or [startup_message]
    body_natural_width = max(body_font.measure(line) for line in body_lines)
    title_natural_width = title_font.measure(startup_title) + close_button_size + 14
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
        text=startup_title,
        bg="#ffffff",
        fg="#111827",
        font=title_font,
        anchor="w",
        wraplength=content_width,
    ).pack(fill="x")
    tk.Label(
        body,
        text=startup_message,
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
        self.current_browser_detail: dict[str, Any] | None = None
        self.current_browser_detail_ignored = False
        self.current_started_ms: int | None = None
        self.tracking = True
        self.afk = False
        self.ignored = False
        self.lock = threading.Lock()

    def reset(self) -> None:
        with self.lock:
            self.current = None
            self.current_browser_detail = None
            self.current_browser_detail_ignored = False
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
            self.current_browser_detail = None
            self.current_browser_detail_ignored = False
            self.current_started_ms = None

    def debug_state(self) -> dict[str, Any]:
        with self.lock:
            visible_current = None if self.current_browser_detail_ignored else self.current
            visible_detail = None if self.current_browser_detail_ignored else self.current_browser_detail
            return {
                "tracking": self.tracking,
                "afk": self.afk,
                "ignored": self.ignored,
                "current": self._serialize_focus_with_icon_locked(visible_current) if visible_current else None,
                "currentStartedAt": self.current_started_ms if visible_current else None,
                "currentStartedAtText": format_debug_timestamp(self.current_started_ms if visible_current else None),
                "currentBrowserDetail": visible_detail,
                "currentBrowserDetailKey": browser_detail_key(visible_detail),
                "currentBrowserDetailIgnored": self.current_browser_detail_ignored,
            }

    def focus_changed(
        self,
        focus: FocusInfo,
        privacy_mode: str | None = None,
    ) -> dict[str, Any] | None:
        with self.lock:
            if not self.tracking or self.afk:
                return None

            focus = privacy_safe_focus(focus, privacy_mode)
            change_ms = now_ms()
            previous = self.current
            previous_elapsed_ms = self._finalize_current_locked(change_ms)

            if self.store.is_activity_ignored(focus):
                self.current = None
                self.current_browser_detail = None
                self.current_browser_detail_ignored = False
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
            self.current_browser_detail = self._read_browser_detail_locked(focus, privacy_mode)
            self.current_browser_detail_ignored = self.store.is_browser_detail_ignored(self.current_browser_detail)
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
            self.current_browser_detail = None
            self.current_browser_detail_ignored = False
            self.current_started_ms = None
            self.afk = True
            self.ignored = False

            return {
                "type": "afk_started",
                "at": afk_started_ms,
                "idleMs": idle_ms,
                "snapshot": self._snapshot_locked(afk_started_ms),
            }

    def leave_afk(
        self,
        focus: FocusInfo | None,
        privacy_mode: str | None = None,
    ) -> dict[str, Any] | None:
        with self.lock:
            if not self.tracking or not self.afk:
                return None

            focus = privacy_safe_focus(focus, privacy_mode)
            resumed_ms = now_ms()
            self.afk = False
            if focus and self.store.is_activity_ignored(focus):
                self.ignored = True
                self.current = None
                self.current_browser_detail = None
                self.current_browser_detail_ignored = False
                self.current_started_ms = None
            else:
                self.ignored = False
                self.current = focus
                self.current_browser_detail = self._read_browser_detail_locked(focus, privacy_mode)
                self.current_browser_detail_ignored = self.store.is_browser_detail_ignored(self.current_browser_detail)
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
                self.current_browser_detail = None
                self.current_browser_detail_ignored = False
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
                self.current_browser_detail = self._read_browser_detail_locked(focus)
                self.current_browser_detail_ignored = self.store.is_browser_detail_ignored(self.current_browser_detail)
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

    def create_group(self, display_name: str) -> dict[str, Any]:
        with self.lock:
            group = self.store.create_binding_group(display_name)
            timestamp_ms = now_ms()
            return {
                "type": "group_created",
                "at": timestamp_ms,
                "group": group,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def rename_group(self, group_id: str, display_name: str) -> dict[str, Any]:
        with self.lock:
            group = self.store.rename_binding_group(group_id, display_name)
            timestamp_ms = now_ms()
            return {
                "type": "group_renamed",
                "at": timestamp_ms,
                "group": group,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def delete_group(self, group_id: str) -> dict[str, Any]:
        with self.lock:
            self.store.delete_binding_group(group_id)
            timestamp_ms = now_ms()
            return {
                "type": "group_deleted",
                "at": timestamp_ms,
                "groupId": group_id,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def set_group_icon(self, group_id: str, icon_id: str) -> dict[str, Any]:
        with self.lock:
            group = self.store.set_binding_group_icon(group_id, icon_id)
            timestamp_ms = now_ms()
            return {
                "type": "group_icon_changed",
                "at": timestamp_ms,
                "group": group,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def bind_source(self, group_id: str, source_key: str) -> dict[str, Any]:
        with self.lock:
            normalized_source_key = self.store.bind_source(group_id, source_key)
            timestamp_ms = now_ms()
            return {
                "type": "source_bound",
                "at": timestamp_ms,
                "groupId": group_id,
                "sourceKey": normalized_source_key,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def unbind_source(self, source_key: str) -> dict[str, Any]:
        with self.lock:
            normalized_source_key = self.store.unbind_source(source_key)
            timestamp_ms = now_ms()
            return {
                "type": "source_unbound",
                "at": timestamp_ms,
                "sourceKey": normalized_source_key,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def ignore_browser_detail(self, source_key: str, display_name: str) -> dict[str, Any]:
        with self.lock:
            normalized_source_key = self.store.ignore_browser_detail(source_key, display_name)
            timestamp_ms = now_ms()
            if browser_detail_key(self.current_browser_detail) == normalized_source_key:
                self._finalize_current_locked(timestamp_ms)
                self.current_started_ms = timestamp_ms
                self.current_browser_detail_ignored = True
            return {
                "type": "browser_detail_ignored",
                "at": timestamp_ms,
                "sourceKey": normalized_source_key,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def unignore_browser_detail(self, source_key: str) -> dict[str, Any]:
        with self.lock:
            normalized_source_key = self.store.unignore_browser_detail(source_key)
            timestamp_ms = now_ms()
            if browser_detail_key(self.current_browser_detail) == normalized_source_key:
                self.current_browser_detail_ignored = False
                self.current_started_ms = timestamp_ms
            return {
                "type": "browser_detail_unignored",
                "at": timestamp_ms,
                "sourceKey": normalized_source_key,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def delete_browser_detail(self, source_key: str, start_ms: int, end_ms: int) -> dict[str, Any]:
        with self.lock:
            deleted_count = self.store.delete_browser_detail_between(source_key, start_ms, end_ms)
            timestamp_ms = now_ms()
            return {
                "type": "browser_detail_deleted",
                "at": timestamp_ms,
                "sourceKey": source_key,
                "deletedCount": deleted_count,
                "snapshot": self._snapshot_locked(timestamp_ms, start_ms, end_ms),
            }

    def browser_detail_changed(
        self,
        focus: FocusInfo,
        browser_detail: dict[str, Any] | None,
        privacy_mode: str | None = None,
    ) -> dict[str, Any] | None:
        with self.lock:
            if not self.tracking or self.afk or not self.current or self.current_started_ms is None:
                return None
            focus = privacy_safe_focus(focus, privacy_mode)
            if self.current.hwnd != focus.hwnd:
                return None
            if self.store.is_activity_ignored(focus):
                return None

            previous_key = browser_detail_key(self.current_browser_detail)
            next_key = browser_detail_key(browser_detail)
            if previous_key == next_key:
                timestamp_ms = now_ms()
                self.current = focus
                self.current_browser_detail = self._merge_browser_detail_title_locked(browser_detail, focus)
                self.current_browser_detail_ignored = self.store.is_browser_detail_ignored(self.current_browser_detail)
                return {
                    "type": "browser_detail_updated",
                    "at": timestamp_ms,
                    "focus": self._serialize_focus_with_icon_locked(focus),
                    "browserDetail": self.current_browser_detail,
                    "snapshot": self._snapshot_locked(timestamp_ms),
                }

            timestamp_ms = now_ms()
            previous = self.current
            previous_elapsed_ms = self._finalize_current_locked(timestamp_ms)
            self.current = focus
            self.current_browser_detail = self._merge_browser_detail_title_locked(browser_detail, focus)
            self.current_browser_detail_ignored = self.store.is_browser_detail_ignored(self.current_browser_detail)
            self.current_started_ms = timestamp_ms
            self.ignored = False

            return {
                "type": "browser_detail_changed",
                "at": timestamp_ms,
                "focus": self._serialize_focus_with_icon_locked(focus),
                "previous": self._serialize_focus_with_icon_locked(previous),
                "previousElapsedMs": previous_elapsed_ms,
                "browserDetail": self.current_browser_detail,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def window_title_changed(
        self,
        focus: FocusInfo,
        privacy_mode: str | None = None,
        include_snapshot: bool = True,
    ) -> dict[str, Any] | None:
        with self.lock:
            if not self.tracking or self.afk or not self.current:
                return None
            focus = privacy_safe_focus(focus, privacy_mode)
            if self.current.hwnd != focus.hwnd:
                return None
            if self.store.is_activity_ignored(focus):
                return None

            self.current = focus
            if self.current_browser_detail is not None:
                self.current_browser_detail = {
                    **self.current_browser_detail,
                    "title": self._browser_detail_title_locked(self.current_browser_detail, focus),
                }

            timestamp_ms = now_ms()
            if not include_snapshot:
                return None
            return {
                "type": "window_title_changed",
                "at": timestamp_ms,
                "focus": self._serialize_focus_with_icon_locked(focus),
                "browserDetail": self.current_browser_detail,
                "snapshot": self._snapshot_locked(timestamp_ms),
            }

    def snapshot(self, start_ms: int | None = None, end_ms: int | None = None) -> dict[str, Any]:
        with self.lock:
            return self._snapshot_locked(now_ms(), start_ms, end_ms)

    def timeline(self, start_ms: int, end_ms: int) -> dict[str, Any]:
        with self.lock:
            generated_at = now_ms()
            groups = self.store.binding_groups()
            bindings = self.store.source_bindings()
            entries = [
                self._timeline_entry_locked(entry, groups, bindings)
                for entry in self.store.timeline_between(start_ms, end_ms)
            ]
            return {
                "type": "timeline",
                "startMs": start_ms,
                "endMs": end_ms,
                "generatedAt": generated_at,
                "entries": entries,
                "currentEntry": self._current_timeline_entry_locked(
                    start_ms,
                    end_ms,
                    generated_at,
                    groups,
                    bindings,
                ),
            }

    def is_current_window(self, focus: FocusInfo | None) -> bool:
        with self.lock:
            return bool(self.current and focus and self.current.hwnd == focus.hwnd)

    def _finalize_current_locked(self, ended_at: int) -> int:
        if not self.current or self.current_started_ms is None:
            return 0

        elapsed_ms = max(0, ended_at - self.current_started_ms)
        if not self.current_browser_detail_ignored:
            self.store.insert_session(self.current, self.current_started_ms, ended_at, self.current_browser_detail)
        self.current_started_ms = ended_at
        return elapsed_ms

    def _read_browser_detail_locked(
        self,
        focus: FocusInfo | None,
        privacy_mode: str | None = None,
    ) -> dict[str, Any] | None:
        return serialize_browser_detail(read_browser_detail(focus, privacy_mode)) if focus else None

    def _merge_browser_detail_title_locked(
        self,
        browser_detail: dict[str, Any] | None,
        focus: FocusInfo,
    ) -> dict[str, Any] | None:
        if browser_detail is None:
            return None

        title = self._browser_detail_title_locked(browser_detail, focus)

        return {
            **browser_detail,
            "title": title,
        }

    def _browser_detail_title_locked(
        self,
        browser_detail: dict[str, Any],
        focus: FocusInfo,
    ) -> str:
        return ""

    def _snapshot_locked(
        self,
        timestamp_ms: int,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> dict[str, Any]:
        period_start_ms = start_ms if start_ms is not None else start_of_local_day_ms()
        totals = self.store.summary_between(period_start_ms, end_ms)
        visible_current = None if self.current_browser_detail_ignored else self.current
        visible_browser_detail = None if self.current_browser_detail_ignored else self.current_browser_detail
        visible_started_ms = None if self.current_browser_detail_ignored else self.current_started_ms
        if end_ms is None and visible_current:
            inject_current_browser_detail(
                totals,
                current_browser_detail_payload(
                    visible_current,
                    visible_browser_detail,
                    visible_started_ms,
                    self.store.get_activity_key(visible_current),
                ),
                visible_current,
            )
        binding_groups = self.store.binding_groups()
        source_bindings = self.store.source_bindings()
        summary_target = current_summary_target(
            visible_current,
            visible_browser_detail,
            visible_started_ms,
            self.store.get_activity_key(visible_current) if visible_current else "",
            binding_groups,
            source_bindings,
        )
        totals = apply_bindings_to_totals(totals, binding_groups, source_bindings)
        ensure_current_group_item(
            totals,
            summary_target,
            visible_current,
            visible_browser_detail,
        )
        self._add_icons_to_totals_locked(totals)
        return {
            "type": "snapshot",
            "at": timestamp_ms,
            "periodStartMs": period_start_ms,
            "periodEndMs": end_ms,
                "tracking": self.tracking,
                "afk": self.afk,
                "ignored": self.ignored,
                "current": self._serialize_focus_with_icon_locked(visible_current) if visible_current else None,
                "currentStartedAt": visible_started_ms,
                "currentBrowserDetail": current_browser_detail_payload(
                    visible_current,
                    visible_browser_detail,
                    visible_started_ms,
                    self.store.get_activity_key(visible_current) if visible_current else "",
                ),
                "currentSummaryTarget": summary_target,
                "totals": totals,
                "bindingGroups": binding_groups,
                "ignoredActivities": self.store.ignored_activities() + self.store.ignored_browser_details(),
                "recentSessions": self.store.recent_sessions(),
            }

    def _current_browser_detail_payload_locked(self) -> dict[str, Any] | None:
        return current_browser_detail_payload(
            self.current,
            self.current_browser_detail,
            self.current_started_ms,
            self.store.get_activity_key(self.current) if self.current else "",
        )

    def _timeline_entry_locked(
        self,
        source_entry: dict[str, Any],
        groups: list[dict[str, Any]],
        bindings: dict[str, str],
    ) -> dict[str, Any]:
        source_key = str(source_entry["sourceKey"])
        target = binding_target_for_source(source_key, groups, bindings)
        if target is None:
            activity_key = str(source_entry["originalActivityKey"])
            display_name = str(source_entry["originalDisplayName"] or source_entry["sourceLabel"])
            kind = "activity"
            group_id = None
            icon_id = None
        else:
            activity_key = str(target["activityKey"])
            display_name = str(target["displayName"])
            kind = "user_group"
            group_id = str(target["groupId"])
            icon_id = str(target["iconId"])

        return {
            "sessionId": source_entry["sessionId"],
            "startMs": source_entry["startMs"],
            "endMs": source_entry["endMs"],
            "activityKey": activity_key,
            "displayName": display_name,
            "kind": kind,
            "groupId": group_id,
            "iconId": icon_id,
            "sourceKey": source_key,
            "sourceType": source_entry["sourceType"],
            "sourceLabel": source_entry["sourceLabel"],
            "processName": source_entry["processName"],
            "windowTitle": source_entry["windowTitle"],
            "trackingStatus": source_entry["trackingStatus"],
        }

    def _current_timeline_entry_locked(
        self,
        start_ms: int,
        end_ms: int,
        generated_at: int,
        groups: list[dict[str, Any]],
        bindings: dict[str, str],
    ) -> dict[str, Any] | None:
        if (
            not self.tracking
            or self.afk
            or self.ignored
            or self.current is None
            or self.current_started_ms is None
            or self.current_browser_detail_ignored
        ):
            return None

        current_end_ms = min(generated_at, end_ms)
        if current_end_ms <= start_ms or current_end_ms <= self.current_started_ms:
            return None

        source_key = self.store.get_activity_key(self.current)
        source_type = "application"
        source_label = self.current.display_name
        tracking_status: str | None = None
        window_title = self.current.window_title

        if is_supported_browser(self.current.process_name):
            detail = self.current_browser_detail
            source_key = browser_detail_key(detail) or "browser:other"
            tracking_status = str((detail or {}).get("tracking_status") or "other")
            if self.store.is_browser_detail_ignored(detail):
                return None
            host = str((detail or {}).get("host") or "")
            source_type = "browser"
            source_label = site_display_name(host, tracking_status)
            window_title = str((detail or {}).get("title") or "") if tracking_status == "tracked" else ""

        return self._timeline_entry_locked(
            {
                "sessionId": f"current:{self.current.hwnd}:{self.current_started_ms}",
                "startMs": self.current_started_ms,
                "endMs": current_end_ms,
                "sourceKey": source_key,
                "sourceType": source_type,
                "sourceLabel": source_label,
                "originalActivityKey": self.store.get_activity_key(self.current),
                "originalDisplayName": self.current.display_name,
                "processName": self.current.process_name,
                "windowTitle": window_title,
                "trackingStatus": tracking_status,
            },
            groups,
            bindings,
        )

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
            group_items = item.get("groupItems")
            if not isinstance(group_items, list):
                continue
            for group_item in group_items:
                group_focus = group_item.get("focus")
                if isinstance(group_focus, dict):
                    self._add_icon_to_focus_locked(group_focus)

    def _add_icon_to_focus_locked(self, focus: dict[str, Any]) -> None:
        icon_data_url = self.icon_provider.get_icon_data_url(
            focus.get("process_path"),
            focus.get("process_name"),
        )
        if icon_data_url:
            focus["icon_data_url"] = icon_data_url


class AgentWebSocketServer:
    def __init__(self, store: ActivityStore | None = None) -> None:
        self.store = store or ActivityStore()
        self.icon_provider = IconProvider()
        self.tracker = ActivityTracker(self.store, self.icon_provider)
        self.diagnostics = DiagnosticEventRecorder()
        self.clients: set[Any] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.shutdown_event: asyncio.Event | None = None
        self.watch_thread: threading.Thread | None = None
        self.idle_thread: threading.Thread | None = None
        self.focus_sanity_thread: threading.Thread | None = None
        self.browser_event_subscription: ChromeAddressBarEventSubscription | None = None
        self.browser_privacy_modes: dict[tuple[int, int], str] = {}
        self.latest_snapshot: dict[str, Any] | None = None
        self.latest_snapshot_lock = threading.Lock()
        self.revision = 0
        self.last_focus_key: tuple[int, int, str, str] | None = None
        self.last_focus_lock = threading.Lock()
        self.watch_stop_event = threading.Event()
        self.stopping = False

    def debug_state(self) -> dict[str, Any]:
        raw_focus = get_foreground_focus()
        focus = privacy_safe_focus(raw_focus, self._privacy_mode_for_focus(raw_focus))
        snapshot = self._latest_snapshot()
        return {
            "tracker": self.tracker.debug_state(),
            "liveForeground": {
                "focus": serialize_focus(focus),
                "browserDetail": "not read during debug refresh",
            },
            "browserValueEventSubscribed": bool(
                self.browser_event_subscription and self.browser_event_subscription.active
            ),
            "recentEvents": self.diagnostics.recent_events(),
            "diagnosticFileLogging": self.diagnostics.file_logging_enabled(),
            "diagnosticLogPath": str(self.diagnostics.log_path),
            "summary": snapshot.get("totals", {}),
        }

    async def run(self, host: str, port: int) -> None:
        self.loop = asyncio.get_running_loop()
        self.shutdown_event = asyncio.Event()
        self.start_tracking()
        async with websockets.serve(self.handle_client, host, port):
            if sys.stdout:
                print(text("server.listening", host=host, port=port))
            await self.shutdown_event.wait()

    def stop_server(self) -> None:
        if self.stopping:
            return

        self.stopping = True
        self.stop_browser_event_subscription()
        self.stop_tracking()
        if self.loop and self.shutdown_event:
            try:
                self.loop.call_soon_threadsafe(self.shutdown_event.set)
            except RuntimeError:
                pass

    async def handle_client(self, websocket) -> None:
        self.clients.add(websocket)
        snapshot = self.tracker.snapshot()
        snapshot["revision"] = self.revision
        self._remember_snapshot(snapshot)
        await self.send(websocket, {"type": "agent_ready", "revision": self.revision, "snapshot": snapshot})

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
            snapshot = self.tracker.snapshot(start_ms, end_ms)
            snapshot["revision"] = self.revision
            self._remember_snapshot(snapshot)
            await self.send(websocket, snapshot)
        elif message_type == "get_revision":
            await self.send(websocket, {"type": "revision", "revision": self.revision})
        elif message_type == "get_timeline":
            start_ms = self._required_int(message.get("startMs"))
            end_ms = self._required_int(message.get("endMs"))
            if (
                start_ms is None
                or end_ms is None
                or end_ms <= start_ms
                or end_ms - start_ms > MAX_TIMELINE_RANGE_MS
            ):
                await self.send(websocket, {"type": "error", "message": "Missing or invalid timeline range."})
                return
            await self.send(websocket, self.tracker.timeline(start_ms, end_ms))
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
        elif message_type == "create_group":
            name = str(message.get("name") or "")
            await self._broadcast_binding_command(websocket, lambda: self.tracker.create_group(name))
        elif message_type == "rename_group":
            group_id = str(message.get("groupId") or "")
            name = str(message.get("name") or "")
            await self._broadcast_binding_command(websocket, lambda: self.tracker.rename_group(group_id, name))
        elif message_type == "delete_group":
            group_id = str(message.get("groupId") or "")
            await self._broadcast_binding_command(websocket, lambda: self.tracker.delete_group(group_id))
        elif message_type == "set_group_icon":
            group_id = str(message.get("groupId") or "")
            icon_id = str(message.get("iconId") or "")
            await self._broadcast_binding_command(websocket, lambda: self.tracker.set_group_icon(group_id, icon_id))
        elif message_type == "bind_source":
            group_id = str(message.get("groupId") or "")
            source_key = str(message.get("sourceKey") or "")
            await self._broadcast_binding_command(websocket, lambda: self.tracker.bind_source(group_id, source_key))
        elif message_type == "unbind_source":
            source_key = str(message.get("sourceKey") or "")
            await self._broadcast_binding_command(websocket, lambda: self.tracker.unbind_source(source_key))
        elif message_type == "ignore_browser_detail":
            source_key = str(message.get("sourceKey") or "")
            display_name = str(message.get("displayName") or "")
            await self._broadcast_binding_command(
                websocket,
                lambda: self.tracker.ignore_browser_detail(source_key, display_name),
            )
        elif message_type == "unignore_browser_detail":
            source_key = str(message.get("sourceKey") or "")
            await self._broadcast_binding_command(
                websocket,
                lambda: self.tracker.unignore_browser_detail(source_key),
            )
        elif message_type == "delete_browser_detail":
            source_key = str(message.get("sourceKey") or "")
            start_ms = self._optional_int(message.get("startMs"))
            end_ms = self._optional_int(message.get("endMs"))
            if start_ms is None or end_ms is None or end_ms <= start_ms:
                await self.send(websocket, {"type": "error", "message": "Missing or invalid delete range."})
                return
            await self._broadcast_binding_command(
                websocket,
                lambda: self.tracker.delete_browser_detail(source_key, start_ms, end_ms),
            )
        else:
            await self.send(websocket, {"type": "error", "message": f"Unknown message type: {message_type}"})

    async def _broadcast_binding_command(self, websocket, command) -> None:
        try:
            event = command()
        except ValueError as exc:
            await self.send(websocket, {"type": "error", "message": str(exc)})
            return
        await self.broadcast(event)

    def _optional_int(self, value: Any) -> int | None:
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _required_int(self, value: Any) -> int | None:
        return value if type(value) is int else None

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
        self.stop_browser_event_subscription()
        self.tracker.stop()

    def run_watcher(self) -> None:
        watcher = FocusEventWatcher(self.handle_focus_changed, self.handle_window_name_changed)
        watcher.run(self.watch_stop_event)

    def start_idle_monitor(self) -> None:
        if self.idle_thread and self.idle_thread.is_alive():
            return

        self.idle_thread = threading.Thread(target=self.run_idle_monitor, daemon=True)
        self.idle_thread.start()

    def start_focus_sanity_monitor(self) -> None:
        if not FOCUS_SANITY_MONITOR_ENABLED:
            return
        if self.focus_sanity_thread and self.focus_sanity_thread.is_alive():
            return

        self.focus_sanity_thread = threading.Thread(target=self.run_focus_sanity_monitor, daemon=True)
        self.focus_sanity_thread.start()

    def handle_idle_tick(self) -> None:
        if self.stopping:
            return

        try:
            idle_ms = get_idle_ms()
        except Exception as exc:
            print(f"Unable to read idle time: {exc}")
            return

        if idle_ms >= AFK_TIMEOUT_MS:
            self._broadcast_tracking_event("idle_monitor", self.tracker.enter_afk(idle_ms))
            return

        focus = get_foreground_focus()
        self._broadcast_tracking_event(
            "idle_monitor",
            self.tracker.leave_afk(focus, self._privacy_mode_for_focus(focus)),
        )

    def run_idle_monitor(self) -> None:
        while not self.watch_stop_event.wait(IDLE_CHECK_INTERVAL_SECONDS):
            self.handle_idle_tick()

    def run_focus_sanity_monitor(self) -> None:
        while not self.watch_stop_event.wait(FOCUS_SANITY_CHECK_INTERVAL_SECONDS):
            raw_focus = get_foreground_focus()
            if not raw_focus:
                continue
            privacy_mode = self._privacy_mode_for_focus(raw_focus)
            focus = privacy_safe_focus(raw_focus, privacy_mode)
            with self.last_focus_lock:
                if focus.key == self.last_focus_key:
                    continue
            self.diagnostics.record(
                "focus_sanity_monitor",
                note="Foreground differs from the last Windows event.",
                focus=focus,
            )
            self.handle_focus_changed(raw_focus, privacy_mode)

    def handle_focus_changed(self, focus: FocusInfo, privacy_mode: str | None = None) -> None:
        if self.stopping:
            return

        resolved_privacy_mode = privacy_mode or self._privacy_mode_for_focus(focus)
        safe_focus = privacy_safe_focus(focus, resolved_privacy_mode)
        self._remember_focus(safe_focus)
        event = self.tracker.focus_changed(focus, resolved_privacy_mode)
        self.update_browser_event_subscription(focus, resolved_privacy_mode)
        self._broadcast_tracking_event("win_event_foreground", event, focus=safe_focus)

    def handle_window_name_changed(self, focus: FocusInfo) -> None:
        if self.stopping:
            return

        # Native application title changes are intentionally ignored. Chrome's
        # event is retained only as a fallback signal to re-check its URL.
        if not is_supported_browser(focus.process_name):
            return

        privacy_mode = self._privacy_mode_for_focus(focus)
        safe_focus = privacy_safe_focus(focus, privacy_mode)
        self._remember_focus(safe_focus)
        if not self.tracker.is_current_window(focus):
            self.diagnostics.record("win_event_name_change", note="Tracker window differed; treating as a foreground change.", focus=safe_focus)
            self.handle_focus_changed(focus, privacy_mode)
            return

        if self._has_active_browser_subscription(focus):
            self.diagnostics.record(
                "win_event_name_change",
                {"type": "browser_window_changed", "at": now_ms()},
                focus=safe_focus,
            )
            return

        browser_detail = serialize_browser_detail(read_browser_detail(focus, privacy_mode))
        browser_event = self.tracker.browser_detail_changed(focus, browser_detail, privacy_mode)
        self._broadcast_tracking_event("win_event_name_change", browser_event, focus=safe_focus)

    def update_browser_event_subscription(self, focus: FocusInfo | None, privacy_mode: str | None = None) -> None:
        self.stop_browser_event_subscription()
        if self.stopping or not focus or not is_supported_browser(focus.process_name):
            return
        resolved_privacy_mode = privacy_mode or self._privacy_mode_for_focus(focus)
        if resolved_privacy_mode != NORMAL_BROWSER_STATUS:
            self.diagnostics.record(
                "chrome_privacy_guard",
                note="Chrome private or unreadable window: URL subscription intentionally disabled.",
                focus=privacy_safe_focus(focus, resolved_privacy_mode),
            )
            return

        subscription = ChromeAddressBarEventSubscription(focus, self.handle_browser_url_changed, resolved_privacy_mode)
        if subscription.start():
            self.browser_event_subscription = subscription

    def stop_browser_event_subscription(self) -> None:
        if not self.browser_event_subscription:
            return

        self.browser_event_subscription.stop()
        self.browser_event_subscription = None

    def handle_browser_url_changed(self, _subscribed_focus: FocusInfo, url: str) -> None:
        if self.stopping:
            return

        focus = get_foreground_focus()
        if not self.is_active_browser_window(_subscribed_focus, focus):
            self.diagnostics.record(
                "chrome_uia_value_changed",
                note="Ignored stale URL event for a non-foreground Chrome window.",
                focus=privacy_safe_focus(focus, self._privacy_mode_for_focus(focus)),
            )
            return

        privacy_mode = self._privacy_mode_for_focus(focus)
        safe_focus = privacy_safe_focus(focus, privacy_mode)
        if privacy_mode != NORMAL_BROWSER_STATUS:
            browser_detail = serialize_browser_detail(browser_detail_from_url(focus, url, privacy_mode))
            self._broadcast_tracking_event(
                "chrome_privacy_guard",
                self.tracker.browser_detail_changed(focus, browser_detail, privacy_mode),
                focus=safe_focus,
            )
            return

        browser_detail = serialize_browser_detail(browser_detail_from_url(focus, url, privacy_mode))
        self._broadcast_tracking_event(
            "chrome_uia_value_changed",
            self.tracker.browser_detail_changed(focus, browser_detail, privacy_mode),
            focus=safe_focus,
        )

    def is_active_browser_window(
        self,
        subscribed_focus: FocusInfo,
        foreground_focus: FocusInfo | None,
    ) -> bool:
        return bool(
            foreground_focus
            and is_supported_browser(foreground_focus.process_name)
            and foreground_focus.hwnd == subscribed_focus.hwnd
            and foreground_focus.pid == subscribed_focus.pid
            and self.tracker.is_current_window(foreground_focus)
        )

    def _remember_focus(self, focus: FocusInfo | None) -> None:
        if not focus:
            return
        with self.last_focus_lock:
            self.last_focus_key = focus.key

    def _latest_snapshot(self) -> dict[str, Any]:
        with self.latest_snapshot_lock:
            cached = self.latest_snapshot
        if cached is not None:
            return cached

        snapshot = self.tracker.snapshot()
        self._remember_snapshot(snapshot)
        return snapshot

    def _remember_snapshot(self, snapshot: dict[str, Any] | None) -> None:
        if not snapshot or snapshot.get("periodEndMs") is not None:
            return
        with self.latest_snapshot_lock:
            self.latest_snapshot = snapshot

    def _privacy_mode_for_focus(self, focus: FocusInfo | None) -> str:
        if not focus or not is_supported_browser(focus.process_name):
            return NORMAL_BROWSER_STATUS

        key = (focus.hwnd, focus.pid)
        cached = self.browser_privacy_modes.get(key)
        if cached is not None:
            return cached

        privacy_mode = chrome_privacy_mode(focus)
        self.browser_privacy_modes[key] = privacy_mode
        return privacy_mode

    def _has_active_browser_subscription(self, focus: FocusInfo) -> bool:
        subscription = self.browser_event_subscription
        return bool(
            subscription
            and subscription.active
            and subscription.focus.hwnd == focus.hwnd
            and subscription.focus.pid == focus.pid
        )

    def _broadcast_tracking_event(
        self,
        source: str,
        event: dict[str, Any] | None,
        *,
        focus: FocusInfo | None = None,
    ) -> None:
        if event:
            self.diagnostics.record(source, event, focus=focus)
        self.broadcast_from_thread(event)

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
        self.revision += 1
        payload["revision"] = self.revision
        snapshot = payload.pop("snapshot", None)
        if isinstance(snapshot, dict):
            snapshot["revision"] = self.revision
            with self.latest_snapshot_lock:
                previous_snapshot = self.latest_snapshot
            payload["snapshotDelta"] = build_snapshot_delta(previous_snapshot, snapshot)
            payload["snapshotPeriodStartMs"] = snapshot.get("periodStartMs")
            payload["snapshotPeriodEndMs"] = snapshot.get("periodEndMs")
            self._remember_snapshot(snapshot)
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
        error_path = Path.home() / "AppData" / "Local" / "SimpleDesktopLogger" / "server-error.log"
        try:
            asyncio.run(self.server.run(self.host, self.port))
        except Exception:
            error_text = traceback.format_exc()
            try:
                error_path.write_text(error_text, encoding="utf-8")
            except OSError:
                pass
            if sys.stderr:
                print(error_text, file=sys.stderr)

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(text("menu.info"), self._show_info),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(text("menu.open_dashboard"), self._open_dashboard),
            pystray.MenuItem(text("menu.open_debug_console"), self._open_debug_console),
            pystray.MenuItem(
                text("menu.run_at_startup"),
                self._toggle_startup,
                checked=lambda _item: is_startup_enabled(),
            ),
            pystray.MenuItem(
                text("menu.language"),
                pystray.Menu(
                    *(
                        pystray.MenuItem(
                            label,
                            self._language_action(language),
                            checked=self._language_checked(language),
                            radio=True,
                        )
                        for language, label in LANGUAGE_LABELS.items()
                    )
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(text("menu.exit"), self._exit),
        )

    def _language_action(self, language: str):
        def action() -> None:
            self._set_language(language)

        return action

    def _language_checked(self, language: str):
        def checked(_item) -> bool:
            return get_language() == language

        return checked

    def _open_dashboard(self) -> None:
        webbrowser.open(DASHBOARD_URL)

    def _show_info(self) -> None:
        try:
            show_info_dialog()
        except Exception:
            user32.MessageBoxW(None, build_info_message(), text("info.title"), MB_OK | MB_ICONINFORMATION)

    def _open_debug_console(self) -> None:
        threading.Thread(target=show_debug_console, args=(self.server,), daemon=True).start()

    def _toggle_startup(self) -> None:
        set_startup_enabled(not is_startup_enabled())
        self.icon.update_menu()

    def _set_language(self, language: str) -> None:
        set_language(language)
        self.icon.menu = self._build_menu()
        self.icon.update_menu()

    def _exit(self) -> None:
        threading.Timer(1.5, lambda: os._exit(0)).start()
        self.server.stop_server()
        self.icon.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=text("cli.description"))
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
                print(f"\n{text('cli.stopped')}")
                return 0
        else:
            TrayAgentApp(args.host, args.port).run()

        return 0
    finally:
        single_instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
