import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from activity_store import ActivityStore
from agent_server import AgentWebSocketServer, ActivityTracker
from focus_watcher import FocusInfo


def app_focus() -> FocusInfo:
    return FocusInfo(
        hwnd=1,
        pid=11,
        process_name="notepad.exe",
        process_path=r"C:\Windows\System32\notepad.exe",
        window_class="Notepad",
        window_title="Daily notes",
        display_name="notepad.exe",
    )


def chrome_focus(title: str = "YouTube - Chrome") -> FocusInfo:
    return FocusInfo(
        hwnd=2,
        pid=22,
        process_name="chrome.exe",
        process_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        window_class="Chrome_WidgetWin_1",
        window_title=title,
        display_name="chrome.exe",
    )


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class TimelineStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = ActivityStore(Path(self.directory.name) / "activity.db")

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def test_timeline_clips_filters_and_orders_source_sessions(self) -> None:
        self.store.insert_session(app_focus(), 500, 1_500)
        self.store.insert_session(
            chrome_focus(),
            1_500,
            2_500,
            {
                "url": "https://youtube.com/watch?v=private-id",
                "host": "youtube.com",
                "title": "Private video title - YouTube - Chrome",
                "tracking_status": "tracked",
            },
        )
        self.store.insert_session(
            chrome_focus("Unreadable - Chrome"),
            2_500,
            3_500,
            {
                "url": "",
                "host": "",
                "title": "Do not expose this unreadable title",
                "tracking_status": "other",
            },
        )
        self.store.insert_session(app_focus(), 5_000, 6_000)

        rows = self.store.timeline_between(1_000, 4_000)

        self.assertEqual([(row["startMs"], row["endMs"]) for row in rows], [
            (1_000, 1_500),
            (1_500, 2_500),
            (2_500, 3_500),
        ])
        self.assertEqual(rows[0]["sourceKey"], "notepad.exe")
        self.assertEqual(rows[0]["sourceType"], "application")
        self.assertEqual(rows[1]["sourceKey"], "browser:youtube.com")
        self.assertEqual(rows[1]["sourceType"], "browser")
        self.assertEqual(rows[1]["sourceLabel"], "YouTube")
        self.assertEqual(rows[1]["windowTitle"], "")
        self.assertEqual(rows[2]["sourceKey"], "browser:other")
        self.assertEqual(rows[2]["sourceLabel"], "Other")
        self.assertEqual(rows[2]["windowTitle"], "")
        self.assertNotIn("private-id", json.dumps(rows))

    def test_timeline_excludes_ignored_application_and_browser_source(self) -> None:
        self.store.insert_session(app_focus(), 1_000, 2_000)
        self.store.insert_session(
            chrome_focus(),
            2_000,
            3_000,
            {"url": "https://youtube.com/watch", "host": "youtube.com", "tracking_status": "tracked"},
        )
        self.store.ignore_activity("notepad.exe", "notepad.exe")
        self.store.ignore_browser_detail("browser:youtube.com", "YouTube")

        self.assertEqual(self.store.timeline_between(0, 4_000), [])


class TimelineTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = ActivityStore(Path(self.directory.name) / "activity.db")
        self.server = AgentWebSocketServer(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def test_bound_sources_keep_original_data_and_use_group_display_target(self) -> None:
        group = self.store.create_binding_group("Game")
        self.store.bind_source(group["groupId"], "notepad.exe")
        self.store.bind_source(group["groupId"], "browser:youtube.com")
        self.store.insert_session(app_focus(), 1_000, 2_000)
        self.store.insert_session(
            chrome_focus(),
            2_000,
            3_000,
            {"url": "https://youtube.com/watch", "host": "youtube.com", "tracking_status": "tracked"},
        )

        entries = self.server.tracker.timeline(0, 4_000)["entries"]

        self.assertEqual([entry["activityKey"] for entry in entries], [
            f"group:{group['groupId']}",
            f"group:{group['groupId']}",
        ])
        self.assertEqual([entry["kind"] for entry in entries], ["user_group", "user_group"])
        self.assertEqual(entries[0]["sourceKey"], "notepad.exe")
        self.assertEqual(entries[0]["sourceType"], "application")
        self.assertEqual(entries[1]["sourceKey"], "browser:youtube.com")
        self.assertEqual(entries[1]["sourceLabel"], "YouTube")
        self.assertEqual(entries[1]["iconId"], "folder")

    def test_current_entry_uses_actual_start_and_group_target(self) -> None:
        group = self.store.create_binding_group("Game")
        self.store.bind_source(group["groupId"], "browser:youtube.com")
        tracker = self.server.tracker
        tracker.current = chrome_focus()
        tracker.current_started_ms = 1_500
        tracker.current_browser_detail = {
            "host": "youtube.com",
            "title": "YouTube - Chrome",
            "tracking_status": "tracked",
        }

        with patch("agent_server.now_ms", return_value=5_000):
            entry = tracker.timeline(2_000, 4_000)["currentEntry"]

        self.assertEqual(entry["sessionId"], "current:2:1500")
        self.assertEqual(entry["startMs"], 1_500)
        self.assertEqual(entry["endMs"], 4_000)
        self.assertEqual(entry["activityKey"], f"group:{group['groupId']}")
        self.assertEqual(entry["sourceKey"], "browser:youtube.com")

    def test_current_application_entry_uses_the_unbound_source_target(self) -> None:
        tracker = self.server.tracker
        tracker.current = app_focus()
        tracker.current_started_ms = 1_500

        with patch("agent_server.now_ms", return_value=3_000):
            entry = tracker.timeline(1_000, 4_000)["currentEntry"]

        self.assertEqual(entry["activityKey"], "notepad.exe")
        self.assertEqual(entry["displayName"], "notepad.exe")
        self.assertEqual(entry["kind"], "activity")
        self.assertIsNone(entry["groupId"])
        self.assertEqual(entry["sourceKey"], "notepad.exe")
        self.assertEqual(entry["sourceType"], "application")
        self.assertEqual(entry["windowTitle"], "Daily notes")

    def test_afk_and_ignored_current_activity_do_not_create_current_entry(self) -> None:
        tracker = self.server.tracker
        tracker.current = app_focus()
        tracker.current_started_ms = 1_000
        tracker.afk = True
        self.assertIsNone(tracker.timeline(0, 4_000)["currentEntry"])
        tracker.afk = False
        tracker.ignored = True
        self.assertIsNone(tracker.timeline(0, 4_000)["currentEntry"])


class TimelineWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_timeline_validates_strict_range_and_returns_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                store.insert_session(app_focus(), 1_000, 2_000)
                server = AgentWebSocketServer(store)
                websocket = FakeWebSocket()

                await server.handle_message(websocket, json.dumps({"type": "get_timeline", "startMs": "0", "endMs": 1_000}))
                self.assertEqual(websocket.messages[-1]["type"], "error")

                await server.handle_message(websocket, json.dumps({"type": "get_timeline", "startMs": 0, "endMs": 7 * 24 * 60 * 60 * 1000 + 1}))
                self.assertEqual(websocket.messages[-1]["type"], "error")

                await server.handle_message(websocket, json.dumps({"type": "get_timeline", "startMs": 0, "endMs": 4_000}))
                response = websocket.messages[-1]
                self.assertEqual(response["type"], "timeline")
                self.assertEqual(response["startMs"], 0)
                self.assertEqual(response["endMs"], 4_000)
                self.assertEqual(len(response["entries"]), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
