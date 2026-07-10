from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime, timedelta, timezone
from typing import Any

from i18n import text


KST = timezone(timedelta(hours=9))


class DebugConsoleWindow:
    def __init__(self, server: Any) -> None:
        self.server = server
        self.root = tk.Tk()
        self.root.title(text("debug.title"))
        self.root.geometry("980x760")
        self.root.minsize(700, 420)

        frame = tk.Frame(self.root)
        frame.pack(fill="both", expand=True)

        self.output = tk.Text(frame, wrap="none", font=("Consolas", 10))
        y_scroll = tk.Scrollbar(frame, orient="vertical", command=self.output.yview)
        x_scroll = tk.Scrollbar(frame, orient="horizontal", command=self.output.xview)
        self.output.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.output.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        button_row = tk.Frame(self.root)
        button_row.pack(fill="x", padx=8, pady=8)
        tk.Button(button_row, text="Refresh", command=self.refresh).pack(side="left")
        self.file_logging = tk.BooleanVar(value=self.server.diagnostics.file_logging_enabled())
        tk.Checkbutton(
            button_row,
            text="Write diagnostic log",
            variable=self.file_logging,
            command=self._toggle_file_logging,
        ).pack(side="left", padx=(12, 0))
        tk.Button(button_row, text="Close", command=self.root.destroy).pack(side="right")

        self.root.after(250, self.refresh)
        self.root.after(1000, self.refresh_loop)

    def run(self) -> None:
        self.root.mainloop()

    def refresh_loop(self) -> None:
        if not self.root.winfo_exists():
            return

        self.refresh()
        self.root.after(1000, self.refresh_loop)

    def refresh(self) -> None:
        try:
            value = self._build_text()
        except Exception as exc:
            value = f"Unable to build debug state: {exc}"

        current_scroll = self.output.yview()[0]
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", value)
        self.output.yview_moveto(current_scroll)
        self.output.configure(state="disabled")

    def _toggle_file_logging(self) -> None:
        self.server.diagnostics.set_file_logging_enabled(self.file_logging.get())
        self.refresh()

    def _build_text(self) -> str:
        state = self.server.debug_state()
        lines: list[str] = []
        lines.append("Simple Desktop Logger Agent Debug Console")
        lines.append(f"Refreshed at: {format_debug_timestamp()}")
        lines.append("Display refresh only: this window never reads Chrome URL.")
        lines.append("")
        lines.extend(self._status_section(state))
        lines.extend(self._recent_events_section(state["recentEvents"]))
        lines.append("== Raw state ==")
        lines.append("The sections below are the detailed data used when a problem needs deeper inspection.")
        lines.append("")
        lines.extend(self._section("Tracker", state["tracker"]))
        lines.extend(self._section("Live foreground read", state["liveForeground"]))
        lines.extend(self._browser_details_section(state["summary"]))
        lines.append("")
        lines.append("Test ideas:")
        lines.append("- Switch Chrome tabs across different sites and check whether tracker.currentBrowserDetail changes.")
        lines.append("- Move within one site, such as YouTube video A to video B, and check whether host stays stable.")
        lines.append("- Open a new tab, chrome:// page, or private/unreadable page and check whether it becomes Other.")
        lines.append("- Switch Chrome -> another app -> Chrome and check whether a browser detail row is saved.")
        lines.append("- Use local URLs such as localhost/127.0.0.1 and check whether labels stay sane.")
        return "\n".join(lines)

    def _status_section(self, state: dict[str, Any]) -> list[str]:
        tracker = state["tracker"]
        current = tracker.get("current") or {}
        browser = tracker.get("currentBrowserDetail") or {}
        lines = ["== Current status =="]
        lines.append(
            "Tracking="
            f"{tracker.get('tracking')} | AFK={tracker.get('afk')} | Ignored={tracker.get('ignored')} | "
            f"Chrome URL event subscribed={state['browserValueEventSubscribed']}"
        )
        lines.append(
            "Activity: "
            f"{current.get('display_name') or '-'} | {current.get('process_name') or '-'} | "
            f"hwnd={current.get('hwnd') or '-'} | started={tracker.get('currentStartedAtText') or '-'}"
        )
        lines.append(f"Window: {current.get('window_title') or '-'}")
        lines.append(
            "Browser detail: "
            f"{browser.get('host') or '-'} | key={tracker.get('currentBrowserDetailKey') or '-'} | "
            f"status={browser.get('tracking_status') or '-'}"
        )
        lines.append(
            "Diagnostic file log: "
            f"{'ON' if state['diagnosticFileLogging'] else 'OFF'} | {state['diagnosticLogPath']}"
        )
        lines.append("")
        return lines

    def _recent_events_section(self, events: list[dict[str, Any]]) -> list[str]:
        lines = ["== Recent tracking events =="]
        if not events:
            lines.extend(["(No events yet)", ""])
            return lines

        for event in events[:15]:
            focus = event.get("focus") or {}
            browser = event.get("browser") or {}
            timestamp = format_debug_event_timestamp(event.get("at"))
            event_type = event.get("eventType") or "-"
            activity = focus.get("displayName") or focus.get("processName") or "-"
            host = browser.get("host") or "-"
            suffix = f" | {event['note']}" if event.get("note") else ""
            lines.append(
                f"{timestamp} | {event.get('source')} -> {event_type} | {activity} | host={host}{suffix}"
            )
        lines.append("")
        return lines

    def _section(self, title: str, payload: dict[str, Any]) -> list[str]:
        return [f"== {title} ==", json.dumps(payload, ensure_ascii=False, indent=2), ""]

    def _browser_details_section(self, summary: dict[str, Any]) -> list[str]:
        lines = ["== Today's browserDetails summary =="]
        found = False
        for name, item in summary.items():
            details = item.get("browserDetails")
            if not details:
                continue

            found = True
            lines.append(f"{name} totalMs={item.get('totalMs')}")
            child_total = 0
            for detail in details:
                child_total += int(detail.get("totalMs") or 0)
                lines.append(
                    "  - "
                    f"{detail.get('label')} | host={detail.get('host')!r} | "
                    f"totalMs={detail.get('totalMs')} | status={detail.get('trackingStatus')} | "
                    f"key={detail.get('key')}"
                )
            lines.append(f"  childTotalMs={child_total}")
        if not found:
            lines.append("(none)")
        lines.append("")
        return lines


def format_debug_timestamp() -> str:
    return datetime.now(tz=timezone.utc).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def format_debug_event_timestamp(timestamp_ms: Any) -> str:
    try:
        value = int(timestamp_ms)
    except (TypeError, ValueError):
        return "-"
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).astimezone(KST).strftime("%H:%M:%S.%f")[:-3]


def show_debug_console(server: Any) -> None:
    DebugConsoleWindow(server).run()
