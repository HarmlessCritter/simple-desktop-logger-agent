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
    def test_initialization_removes_previously_stored_window_titles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "activity.db"
            store = ActivityStore(db_path)
            store.insert_session(
                chrome_focus(),
                1_000,
                4_000,
                {
                    "browser_name": "chrome.exe",
                    "url": "https://youtube.com/watch",
                    "host": "youtube.com",
                    "title": "Sensitive video title",
                    "tracking_status": "tracked",
                },
            )
            store.connection.execute("UPDATE activity_sessions SET window_title = 'Sensitive file name'")
            store.connection.execute("UPDATE browser_session_details SET title = 'Sensitive video title'")
            store.connection.commit()
            store.close()

            reopened = ActivityStore(db_path)
            try:
                app_titles = reopened.connection.execute("SELECT window_title FROM activity_sessions").fetchall()
                browser_titles = reopened.connection.execute("SELECT title FROM browser_session_details").fetchall()
                self.assertEqual([row["window_title"] for row in app_titles], [""])
                self.assertEqual([row["title"] for row in browser_titles], [""])
            finally:
                reopened.close()

    def test_existing_localhost_records_are_reclassified_as_local(self) -> None:
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
                        "key": "browser:local",
                        "label": "Local",
                        "host": "127.0.0.1",
                        "faviconUrl": "",
                        "trackingStatus": "tracked",
                        "processName": "chrome.exe",
                        "windowTitle": "",
                        "totalMs": 3_000,
                    }
                ])
            finally:
                store.close()

    def test_binding_rules_are_stored_separately_from_activity_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                store.insert_session(chrome_focus(), 1_000, 4_000, None)
                group = store.create_binding_group("Game")
                source_key = store.bind_source(group["groupId"], "browser:tooli.com")

                self.assertEqual(store.binding_groups(), [group])
                self.assertEqual(group["iconId"], "folder")
                self.assertEqual(store.source_bindings(), {"browser:tooli.com": group["groupId"]})
                self.assertEqual(source_key, "browser:tooli.com")
                self.assertEqual(store.summary_between(0)["chrome.exe"]["totalMs"], 3_000)

                store.delete_binding_group(group["groupId"])
                self.assertEqual(store.binding_groups(), [])
                self.assertEqual(store.source_bindings(), {})
            finally:
                store.close()

    def test_group_icon_is_stored_without_agent_side_icon_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                group = store.create_binding_group("Game")
                updated = store.set_binding_group_icon(group["groupId"], "gamepad-2")

                self.assertEqual(updated["iconId"], "gamepad-2")
                self.assertEqual(store.binding_groups(), [updated])
                with self.assertRaises(ValueError):
                    store.set_binding_group_icon(group["groupId"], "Gamepad Icon")
            finally:
                store.close()

    def test_rejects_other_and_browser_parent_binding_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                group = store.create_binding_group("Game")
                with self.assertRaises(ValueError):
                    store.bind_source(group["groupId"], "browser:other")
                with self.assertRaises(ValueError):
                    store.bind_source(group["groupId"], "chrome.exe")
                self.assertEqual(store.bind_source(group["groupId"], "browser:local"), "browser:local")
            finally:
                store.close()

    def test_ignored_browser_source_is_removed_without_ignoring_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                store.insert_session(
                    chrome_focus(),
                    1_000,
                    3_000,
                    {"url": "https://youtube.com/watch?v=1", "host": "youtube.com", "tracking_status": "tracked"},
                )
                store.insert_session(
                    chrome_focus(),
                    3_000,
                    6_000,
                    {"url": "https://naver.com", "host": "naver.com", "tracking_status": "tracked"},
                )

                store.ignore_browser_detail("browser:youtube.com", "YouTube")
                summary = store.summary_between(0)

                self.assertEqual(summary["chrome.exe"]["totalMs"], 3_000)
                self.assertEqual(
                    [detail["key"] for detail in summary["chrome.exe"]["browserDetails"]],
                    ["browser:naver.com"],
                )
                self.assertEqual(
                    store.ignored_browser_details()[0],
                    {
                        "activity_key": "browser:youtube.com",
                        "display_name": "YouTube - Chrome",
                        "created_at": store.ignored_browser_details()[0]["created_at"],
                        "sourceType": "browser",
                        "source_type": "browser",
                    },
                )

                store.unignore_browser_detail("browser:youtube.com")
                self.assertEqual(store.ignored_browser_details(), [])
            finally:
                store.close()

    def test_local_browser_source_can_be_ignored_and_deleted_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                store.insert_session(
                    chrome_focus(),
                    1_000,
                    3_000,
                    {"url": "http://localhost:5173", "host": "localhost", "tracking_status": "other"},
                )
                store.insert_session(
                    chrome_focus(),
                    3_000,
                    6_000,
                    {"url": "https://youtube.com", "host": "youtube.com", "tracking_status": "tracked"},
                )

                store.ignore_browser_detail("browser:local", "Local")
                details = store.summary_between(0)["chrome.exe"]["browserDetails"]
                self.assertEqual([detail["key"] for detail in details], ["browser:youtube.com"])
                store.unignore_browser_detail("browser:local")

                self.assertEqual(store.delete_browser_detail_between("browser:local", 0, 10_000), 1)
                details = store.summary_between(0)["chrome.exe"]["browserDetails"]
                self.assertEqual([detail["key"] for detail in details], ["browser:youtube.com"])
            finally:
                store.close()

    def test_delete_browser_source_only_deletes_matching_host_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                store.insert_session(
                    chrome_focus(),
                    1_000,
                    3_000,
                    {"url": "https://youtube.com/watch?v=1", "host": "youtube.com", "tracking_status": "tracked"},
                )
                store.insert_session(
                    chrome_focus(),
                    3_000,
                    6_000,
                    {"url": "https://naver.com", "host": "naver.com", "tracking_status": "tracked"},
                )

                self.assertEqual(store.delete_browser_detail_between("browser:youtube.com", 0, 10_000), 1)
                summary = store.summary_between(0)
                self.assertEqual(summary["chrome.exe"]["totalMs"], 3_000)
                self.assertEqual(
                    [detail["key"] for detail in summary["chrome.exe"]["browserDetails"]],
                    ["browser:naver.com"],
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
