import unittest
from unittest.mock import patch

from agent_server import ActivityTracker
from browser_tracking import BrowserDetail, PRIVATE_BROWSER_STATUS
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


def private_chrome_focus() -> FocusInfo:
    return FocusInfo(
        hwnd=303,
        pid=202,
        process_name="chrome.exe",
        process_path="chrome.exe",
        window_class="Chrome_WidgetWin_1",
        window_title="Sensitive Naver Dictionary Page - Chrome",
        display_name="chrome.exe",
    )


def other_focus() -> FocusInfo:
    return FocusInfo(
        hwnd=404,
        pid=505,
        process_name="notepad.exe",
        process_path="notepad.exe",
        window_class="Notepad",
        window_title="Notes",
        display_name="notepad.exe",
    )


class FakeStore:
    def __init__(self) -> None:
        self.sessions = []

    def is_activity_ignored(self, _focus: FocusInfo) -> bool:
        return False

    def get_activity_key(self, focus: FocusInfo) -> str:
        return focus.display_name.lower()

    def insert_session(self, focus, started_at, ended_at, browser_detail) -> None:
        self.sessions.append((focus, started_at, ended_at, browser_detail))

    def summary_between(self, _start_ms, _end_ms):
        return {}

    def ignored_activities(self):
        return []

    def recent_sessions(self):
        return []


class FakeIconProvider:
    def get_icon_data_url(self, _process_path, _process_name):
        return None


class ActivityTrackerTests(unittest.TestCase):
    def test_new_browser_site_is_in_snapshot_before_session_is_persisted(self) -> None:
        tracker = ActivityTracker(FakeStore(), FakeIconProvider())
        detail = BrowserDetail("chrome.exe", "https://youtube.com/watch?v=1", "youtube.com", "YouTube - Chrome", "tracked")

        with patch("agent_server.read_browser_detail", return_value=detail):
            event = tracker.focus_changed(chrome_focus())

        browser_details = event["snapshot"]["totals"]["chrome.exe"]["browserDetails"]
        self.assertEqual(browser_details[0]["key"], "browser:youtube.com")
        self.assertEqual(browser_details[0]["totalMs"], 0)
        self.assertEqual(tracker.current_browser_detail["host"], "youtube.com")

    def test_site_change_finalizes_previous_browser_segment(self) -> None:
        store = FakeStore()
        tracker = ActivityTracker(store, FakeIconProvider())
        first_detail = BrowserDetail("chrome.exe", "https://youtube.com/watch?v=1", "youtube.com", "YouTube - Chrome", "tracked")
        second_detail = {
            "browser_name": "chrome.exe",
            "url": "https://github.com/openai/example",
            "host": "github.com",
            "title": "GitHub - Chrome",
            "tracking_status": "tracked",
        }

        with patch("agent_server.read_browser_detail", return_value=first_detail):
            tracker.focus_changed(chrome_focus())
        with patch("agent_server.get_foreground_focus", return_value=chrome_focus()):
            event = tracker.browser_detail_changed(chrome_focus(), second_detail)

        self.assertEqual(event["type"], "browser_detail_changed")
        self.assertEqual(len(store.sessions), 1)
        self.assertEqual(store.sessions[0][3]["host"], "youtube.com")
        self.assertEqual(tracker.current_browser_detail["host"], "github.com")

    def test_private_chrome_title_and_browser_data_are_redacted_before_persistence(self) -> None:
        store = FakeStore()
        tracker = ActivityTracker(store, FakeIconProvider())
        private_detail = BrowserDetail(
            "chrome.exe",
            "",
            "",
            "",
            "other",
            PRIVATE_BROWSER_STATUS,
        )

        with patch("agent_server.chrome_privacy_mode", return_value=PRIVATE_BROWSER_STATUS), patch(
            "agent_server.read_browser_detail", return_value=private_detail
        ), patch("agent_server.get_foreground_focus", return_value=private_chrome_focus()):
            event = tracker.focus_changed(private_chrome_focus())
            tracker.focus_changed(other_focus())

        self.assertEqual(event["focus"]["window_title"], "")
        persisted_focus, _started_at, _ended_at, persisted_detail = store.sessions[0]
        self.assertEqual(persisted_focus.window_title, "")
        self.assertEqual(persisted_detail["title"], "")
        self.assertEqual(persisted_detail["url"], "")
        self.assertEqual(persisted_detail["host"], "")


if __name__ == "__main__":
    unittest.main()
