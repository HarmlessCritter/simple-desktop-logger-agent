import tempfile
import unittest
from pathlib import Path

from activity_store import ActivityStore
from focus_watcher import FocusInfo


def chrome_focus() -> FocusInfo:
    return FocusInfo(
        hwnd=101,
        pid=202,
        process_name="chrome.exe",
        process_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        window_class="Chrome_WidgetWin_1",
        window_title="Example - Chrome",
        display_name="chrome.exe",
    )


class ActivityStoreBrowserSummaryTests(unittest.TestCase):
    def test_existing_localhost_records_are_reclassified_as_other(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                store.insert_session(
                    chrome_focus(),
                    1_000,
                    4_000,
                    {
                        "browser_name": "chrome.exe",
                        "url": "http://127.0.0.1:5173/dashboard",
                        "host": "127.0.0.1",
                        "title": "Dashboard - Chrome",
                        "tracking_status": "tracked",
                    },
                )

                summary = store.summary_between(0)
                detail = summary["chrome.exe"]["browserDetails"]
                self.assertEqual(detail, [
                    {
                        "key": "browser:other",
                        "label": "Other",
                        "host": "",
                        "faviconUrl": "",
                        "trackingStatus": "other",
                        "totalMs": 3_000,
                    }
                ])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
