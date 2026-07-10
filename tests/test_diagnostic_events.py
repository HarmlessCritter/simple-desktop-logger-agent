import json
import tempfile
import unittest
from pathlib import Path

from diagnostic_events import DiagnosticEventRecorder
from focus_watcher import FocusInfo


def chrome_focus() -> FocusInfo:
    return FocusInfo(
        hwnd=101,
        pid=202,
        process_name="chrome.exe",
        process_path="chrome.exe",
        window_class="Chrome_WidgetWin_1",
        window_title="Example - Chrome",
        display_name="chrome.exe",
    )


class DiagnosticEventRecorderTests(unittest.TestCase):
    def test_keeps_recent_event_with_safe_browser_context(self) -> None:
        recorder = DiagnosticEventRecorder(event_limit=2)
        recorder.record(
            "chrome_uia_value_changed",
            {
                "type": "browser_detail_changed",
                "at": 1_000,
                "browserDetail": {
                    "host": "youtube.com",
                    "tracking_status": "tracked",
                    "url": "https://youtube.com/watch?v=private-value",
                },
            },
            focus=chrome_focus(),
        )

        event = recorder.recent_events()[0]
        self.assertEqual(event["source"], "chrome_uia_value_changed")
        self.assertEqual(event["browser"]["host"], "youtube.com")
        self.assertNotIn("url", json.dumps(event))
        self.assertEqual(event["focus"]["hwnd"], 101)

    def test_file_output_is_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-events.jsonl"
            recorder = DiagnosticEventRecorder(log_path=path)
            recorder.record("win_event_foreground", focus=chrome_focus())
            self.assertFalse(path.exists())

            recorder.set_file_logging_enabled(True)
            recorder.record("win_event_foreground", focus=chrome_focus())
            self.assertTrue(path.exists())
            line = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(line["source"], "win_event_foreground")


if __name__ == "__main__":
    unittest.main()
