import unittest

from browser_detail_snapshot import current_browser_detail_payload, inject_current_browser_detail
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


class BrowserDetailSnapshotTests(unittest.TestCase):
    def test_current_detail_is_injected_before_persistence(self) -> None:
        focus = chrome_focus()
        detail = current_browser_detail_payload(
            focus,
            {
                "host": "youtube.com",
                "tracking_status": "tracked",
            },
            1_000,
            "chrome.exe",
        )
        totals: dict[str, dict] = {}

        inject_current_browser_detail(totals, detail, focus)

        self.assertEqual(totals["chrome.exe"]["browserDetails"], [
            {
                "key": "browser:youtube.com",
                "label": "YouTube",
                "host": "youtube.com",
                "faviconUrl": "https://www.google.com/s2/favicons?domain=youtube.com&sz=64",
                "trackingStatus": "tracked",
                "totalMs": 0,
            }
        ])

    def test_existing_detail_is_not_duplicated(self) -> None:
        focus = chrome_focus()
        detail = current_browser_detail_payload(
            focus,
            {"host": "youtube.com", "tracking_status": "tracked"},
            1_000,
            "chrome.exe",
        )
        totals = {
            "chrome.exe": {
                "activityKey": "chrome.exe",
                "browserDetails": [{"key": "browser:youtube.com", "totalMs": 500}],
            }
        }

        inject_current_browser_detail(totals, detail, focus)

        self.assertEqual(totals["chrome.exe"]["browserDetails"], [{"key": "browser:youtube.com", "totalMs": 500}])


if __name__ == "__main__":
    unittest.main()
