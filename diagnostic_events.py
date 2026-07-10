from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_EVENT_LIMIT = 80


class DiagnosticEventRecorder:
    """Keeps a small in-memory event history and optionally mirrors it to disk."""

    def __init__(self, event_limit: int = DEFAULT_EVENT_LIMIT, log_path: Path | None = None) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=event_limit)
        self._lock = threading.Lock()
        self._file_logging_enabled = False
        self._log_path = log_path or Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / (
            "SimpleDesktopLogger"
        ) / "diagnostic-events.jsonl"

    @property
    def log_path(self) -> Path:
        return self._log_path

    def set_file_logging_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._file_logging_enabled = enabled

    def file_logging_enabled(self) -> bool:
        with self._lock:
            return self._file_logging_enabled

    def record(
        self,
        source: str,
        event: dict[str, Any] | None = None,
        *,
        note: str | None = None,
        focus: Any = None,
    ) -> None:
        entry = self._build_entry(source, event, note, focus)
        with self._lock:
            self._events.append(entry)
            enabled = self._file_logging_enabled

        if enabled:
            self._append_to_file(entry)

    def recent_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._events))

    def _build_entry(
        self,
        source: str,
        event: dict[str, Any] | None,
        note: str | None,
        focus: Any,
    ) -> dict[str, Any]:
        event_focus = event.get("focus") if event else None
        browser_detail = event.get("browserDetail") if event else None
        resolved_focus = event_focus if isinstance(event_focus, dict) else focus
        if browser_detail is None and event:
            snapshot = event.get("snapshot")
            if isinstance(snapshot, dict):
                browser_detail = snapshot.get("currentBrowserDetail")

        entry: dict[str, Any] = {
            "at": int(event.get("at")) if event and event.get("at") is not None else int(datetime.now(timezone.utc).timestamp() * 1000),
            "source": source,
            "eventType": event.get("type") if event else None,
            "note": note,
        }
        if isinstance(resolved_focus, dict):
            entry["focus"] = self._focus_payload(resolved_focus)
        elif resolved_focus is not None:
            entry["focus"] = self._focus_payload_from_object(resolved_focus)
        if isinstance(browser_detail, dict):
            entry["browser"] = {
                "key": browser_detail.get("key"),
                "host": browser_detail.get("host"),
                "trackingStatus": browser_detail.get("trackingStatus") or browser_detail.get("tracking_status"),
            }
        return entry

    def _focus_payload(self, focus: dict[str, Any]) -> dict[str, Any]:
        return {
            "hwnd": focus.get("hwnd"),
            "pid": focus.get("pid"),
            "processName": focus.get("process_name"),
            "displayName": focus.get("display_name"),
            "windowTitle": focus.get("window_title"),
        }

    def _focus_payload_from_object(self, focus: Any) -> dict[str, Any]:
        return {
            "hwnd": getattr(focus, "hwnd", None),
            "pid": getattr(focus, "pid", None),
            "processName": getattr(focus, "process_name", None),
            "displayName": getattr(focus, "display_name", None),
            "windowTitle": getattr(focus, "window_title", None),
        }

    def _append_to_file(self, entry: dict[str, Any]) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            # Diagnostics must never interfere with activity tracking.
            pass
