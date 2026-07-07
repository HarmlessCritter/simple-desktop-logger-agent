from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from focus_watcher import FocusEventWatcher, FocusInfo, format_focus_line


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class FocusWatcherGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("Simple Desktop Logger Agent Debug")
        self.geometry("920x560")
        self.minsize(720, 420)

        self.status_text = tk.StringVar(value="Stopped")
        self.current_focus_text = tk.StringVar(value="-")

        self.log_queue: queue.Queue[FocusInfo | str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.current_focus: FocusInfo | None = None
        self.current_started_at: float | None = None
        self.totals: dict[str, float] = {}
        self.latest_by_activity: dict[str, FocusInfo] = {}

        self.configure(padx=14, pady=14)
        self._build_layout()
        self._poll_log_queue()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Status").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(top, textvariable=self.status_text).grid(row=0, column=1, sticky=tk.W, padx=(8, 24))

        ttk.Label(top, text="Mode").grid(row=0, column=2, sticky=tk.W)
        ttk.Label(top, text="Windows foreground event hook").grid(
            row=0,
            column=3,
            sticky=tk.W,
            padx=(8, 24),
        )

        self.start_button = ttk.Button(top, text="Start", command=self.start_watching)
        self.start_button.grid(row=0, column=4, padx=(24, 8))

        self.stop_button = ttk.Button(top, text="Stop", command=self.stop_watching, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=5)

        ttk.Button(top, text="Clear", command=self.clear_logs).grid(row=0, column=6, padx=(8, 0))

        top.columnconfigure(7, weight=1)

        current = ttk.LabelFrame(self, text="Current focus")
        current.pack(fill=tk.X, pady=(14, 10))
        ttk.Label(current, textvariable=self.current_focus_text).pack(fill=tk.X, padx=10, pady=8)

        totals_frame = ttk.LabelFrame(self, text="Accumulated time")
        totals_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.totals_tree = ttk.Treeview(
            totals_frame,
            columns=("duration", "process", "title"),
            show="tree headings",
            height=8,
        )
        self.totals_tree.heading("#0", text="Activity")
        self.totals_tree.heading("duration", text="Time")
        self.totals_tree.heading("process", text="Process")
        self.totals_tree.heading("title", text="Window title")
        self.totals_tree.column("#0", width=220, minwidth=160)
        self.totals_tree.column("duration", width=90, minwidth=80, anchor=tk.E)
        self.totals_tree.column("process", width=160, minwidth=120)
        self.totals_tree.column("title", width=360, minwidth=200)
        self.totals_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        totals_scrollbar = ttk.Scrollbar(totals_frame, orient=tk.VERTICAL, command=self.totals_tree.yview)
        totals_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.totals_tree.configure(yscrollcommand=totals_scrollbar.set)

        console_frame = ttk.LabelFrame(self, text="Console")
        console_frame.pack(fill=tk.BOTH, expand=True)

        self.console = tk.Text(
            console_frame,
            wrap=tk.NONE,
            state=tk.DISABLED,
            bg="#101418",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            font=("Consolas", 10),
        )
        self.console.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar_y = ttk.Scrollbar(console_frame, orient=tk.VERTICAL, command=self.console.yview)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.console.configure(yscrollcommand=scrollbar_y.set)

    def start_watching(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        self.stop_event.clear()
        self.current_focus = None
        self.current_started_at = None
        self.totals.clear()
        self.latest_by_activity.clear()
        self._refresh_totals()

        self.worker = threading.Thread(
            target=self._watch_loop,
            daemon=True,
        )
        self.worker.start()

        self.status_text.set("Running")
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self._append_log("Focus watcher started. Waiting for Windows foreground events.")

    def stop_watching(self) -> None:
        if not self.worker:
            return

        self._finalize_current_focus(time.perf_counter())
        self.stop_event.set()
        self.status_text.set("Stopping...")
        self.after(100, self._finish_stop)

    def _finish_stop(self) -> None:
        if self.worker and self.worker.is_alive():
            self.after(100, self._finish_stop)
            return

        self.worker = None
        self.status_text.set("Stopped")
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self._append_log("Focus watcher stopped.")

    def clear_logs(self) -> None:
        self.console.configure(state=tk.NORMAL)
        self.console.delete("1.0", tk.END)
        self.console.configure(state=tk.DISABLED)

    def _watch_loop(self) -> None:
        watcher = FocusEventWatcher(lambda focus: self.log_queue.put(focus))
        try:
            watcher.run(self.stop_event)
        except Exception as exc:
            self.log_queue.put(f"Watcher error: {exc}")

    def _poll_log_queue(self) -> None:
        while True:
            try:
                event = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if isinstance(event, str):
                self._append_log(event)
                continue

            self._handle_focus_changed(event)

        self._refresh_totals()
        self.after(50, self._poll_log_queue)

    def _handle_focus_changed(self, focus: FocusInfo) -> None:
        now = time.perf_counter()
        previous = self.current_focus
        previous_elapsed = self._finalize_current_focus(now)

        self.current_focus = focus
        self.current_started_at = now
        self.latest_by_activity[focus.display_name] = focus

        line = format_focus_line(focus)
        if previous and previous_elapsed > 0:
            line += f" (previous: {previous.display_name} +{format_duration(previous_elapsed)})"

        self._append_log(line)
        self.current_focus_text.set(line)

    def _finalize_current_focus(self, now: float) -> float:
        if not self.current_focus or self.current_started_at is None:
            return 0

        elapsed = max(0, now - self.current_started_at)
        key = self.current_focus.display_name
        self.totals[key] = self.totals.get(key, 0) + elapsed
        self.latest_by_activity[key] = self.current_focus
        self.current_started_at = now
        return elapsed

    def _get_live_totals(self) -> dict[str, float]:
        totals = dict(self.totals)
        if self.current_focus and self.current_started_at is not None:
            key = self.current_focus.display_name
            totals[key] = totals.get(key, 0) + max(0, time.perf_counter() - self.current_started_at)

        return totals

    def _refresh_totals(self) -> None:
        totals = self._get_live_totals()
        existing_ids = set(self.totals_tree.get_children())
        ordered = sorted(totals.items(), key=lambda item: item[1], reverse=True)

        for activity, seconds in ordered:
            focus = self.latest_by_activity.get(activity)
            values = (
                format_duration(seconds),
                focus.process_name if focus else "",
                focus.window_title if focus else "",
            )

            if activity in existing_ids:
                self.totals_tree.item(activity, text=activity, values=values)
                existing_ids.remove(activity)
            else:
                self.totals_tree.insert("", tk.END, iid=activity, text=activity, values=values)

        for stale_id in existing_ids:
            self.totals_tree.delete(stale_id)

        for index, (activity, _) in enumerate(ordered):
            self.totals_tree.move(activity, "", index)

    def _append_log(self, line: str) -> None:
        self.console.configure(state=tk.NORMAL)
        self.console.insert(tk.END, line + "\n")
        self.console.see(tk.END)
        self.console.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        self.stop_event.set()
        self.destroy()


def main() -> None:
    app = FocusWatcherGui()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        app.stop_event.set()
        app.destroy()


if __name__ == "__main__":
    main()
