import unittest
from unittest.mock import patch

from agent_server import ActivityTracker
from browser_tracking import BrowserDetail, PRIVATE_BROWSER_STATUS
from browser_tracking import browser_detail_key
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


def windows_operation_focus() -> FocusInfo:
    return FocusInfo(
        hwnd=606,
        pid=707,
        process_name="explorer.exe",
        process_path="explorer.exe",
        window_class="CabinetWClass",
        window_title="Confidential Client Folder",
        display_name="윈도우 조작",
    )


class FakeStore:
    def __init__(self) -> None:
        self.sessions = []
        self.groups = []
        self.bindings = {}
        self.ignored_browser_sources = set()

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

    def ignored_browser_details(self):
        return []

    def recent_sessions(self):
        return []

    def binding_groups(self):
        return list(self.groups)

    def source_bindings(self):
        return dict(self.bindings)

    def create_binding_group(self, display_name):
        group = {"groupId": "group-game", "displayName": display_name.strip(), "iconId": "folder"}
        self.groups.append(group)
        return group

    def bind_source(self, group_id, source_key):
        self.bindings[source_key] = group_id
        return source_key

    def is_browser_detail_ignored(self, browser_detail):
        return browser_detail_key(browser_detail) in self.ignored_browser_sources

    def ignore_browser_detail(self, source_key, _display_name):
        self.ignored_browser_sources.add(source_key)
        return source_key

    def unignore_browser_detail(self, source_key):
        self.ignored_browser_sources.discard(source_key)
        return source_key


class FakeIconProvider:
    def get_icon_data_url(self, _process_path, _process_name):
        return None


class ActivityTrackerTests(unittest.TestCase):
    def test_duplicate_focus_signal_does_not_split_the_current_session(self) -> None:
        store = FakeStore()
        tracker = ActivityTracker(store, FakeIconProvider())

        first_event = tracker.focus_changed(other_focus())
        duplicate_event = tracker.focus_changed(other_focus())

        self.assertEqual(first_event["type"], "focus_changed")
        self.assertIsNone(duplicate_event)
        self.assertEqual(store.sessions, [])

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

    def test_windows_operation_title_is_redacted_before_state_and_persistence(self) -> None:
        store = FakeStore()
        tracker = ActivityTracker(store, FakeIconProvider())

        event = tracker.focus_changed(windows_operation_focus())
        tracker.focus_changed(other_focus())

        self.assertEqual(event["focus"]["window_title"], "")
        persisted_focus, _started_at, _ended_at, _browser_detail = store.sessions[0]
        self.assertEqual(persisted_focus.display_name, "윈도우 조작")
        self.assertEqual(persisted_focus.window_title, "")

    def test_regular_application_title_is_redacted_before_state_and_persistence(self) -> None:
        store = FakeStore()
        tracker = ActivityTracker(store, FakeIconProvider())

        event = tracker.focus_changed(other_focus())
        tracker.focus_changed(windows_operation_focus())

        self.assertEqual(event["focus"]["window_title"], "")
        persisted_focus, _started_at, _ended_at, _browser_detail = store.sessions[0]
        self.assertEqual(persisted_focus.process_name, "notepad.exe")
        self.assertEqual(persisted_focus.window_title, "")

    def test_bound_current_browser_site_targets_group_live_time(self) -> None:
        store = FakeStore()
        tracker = ActivityTracker(store, FakeIconProvider())
        tracker.create_group("Game")
        tracker.bind_source("group-game", "browser:tooli.com")
        detail = BrowserDetail(
            "chrome.exe",
            "https://tooli.com/play",
            "tooli.com",
            "Tooli - Chrome",
            "tracked",
        )

        with patch("agent_server.read_browser_detail", return_value=detail):
            event = tracker.focus_changed(chrome_focus())

        snapshot = event["snapshot"]
        self.assertEqual(snapshot["currentSummaryTarget"], {
            "activityKey": "group:group-game",
            "displayName": "Game",
            "startedAt": tracker.current_started_ms,
            "sourceKey": "browser:tooli.com",
        })
        self.assertNotIn("chrome.exe", snapshot["totals"])
        self.assertEqual(
            snapshot["totals"]["group:group-game"]["groupItems"][0]["sourceKey"],
            "browser:tooli.com",
        )

    def test_current_ignored_browser_site_is_hidden_from_live_snapshot(self) -> None:
        store = FakeStore()
        tracker = ActivityTracker(store, FakeIconProvider())
        detail = BrowserDetail(
            "chrome.exe",
            "https://youtube.com/watch?v=1",
            "youtube.com",
            "YouTube - Chrome",
            "tracked",
        )
        with patch("agent_server.read_browser_detail", return_value=detail):
            tracker.focus_changed(chrome_focus())
        event = tracker.ignore_browser_detail("browser:youtube.com", "YouTube")

        snapshot = event["snapshot"]
        self.assertIsNone(snapshot["current"])
        self.assertIsNone(snapshot["currentBrowserDetail"])
        self.assertIsNone(snapshot["currentStartedAt"])

        resumed = tracker.unignore_browser_detail("browser:youtube.com")
        self.assertIsNotNone(resumed["snapshot"]["current"])
        self.assertIsNotNone(resumed["snapshot"]["currentBrowserDetail"])


if __name__ == "__main__":
    unittest.main()
