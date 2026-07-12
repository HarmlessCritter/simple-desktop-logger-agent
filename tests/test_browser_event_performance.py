import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from activity_store import ActivityStore
from agent_server import AgentWebSocketServer
from browser_tracking import NORMAL_BROWSER_STATUS
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


class BrowserEventPerformanceTests(unittest.TestCase):
    def test_privacy_mode_is_cached_per_chrome_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                server = AgentWebSocketServer(store)
                focus = chrome_focus()
                with patch("agent_server.chrome_privacy_mode", return_value=NORMAL_BROWSER_STATUS) as read_mode:
                    self.assertEqual(server._privacy_mode_for_focus(focus), NORMAL_BROWSER_STATUS)
                    self.assertEqual(server._privacy_mode_for_focus(focus), NORMAL_BROWSER_STATUS)
                read_mode.assert_called_once_with(focus)
            finally:
                store.close()

    def test_title_event_does_not_reread_url_when_uia_subscription_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ActivityStore(Path(directory) / "activity.db")
            try:
                server = AgentWebSocketServer(store)
                focus = chrome_focus()
                server.tracker.current = focus
                server.tracker.current_browser_detail = {
                    "host": "youtube.com",
                    "tracking_status": "tracked",
                }
                server.tracker.current_started_ms = 1_000
                server.browser_privacy_modes[(focus.hwnd, focus.pid)] = NORMAL_BROWSER_STATUS
                server.browser_event_subscription = SimpleNamespace(active=True, focus=focus)

                with patch("agent_server.read_browser_detail") as read_detail, patch.object(
                    server.tracker,
                    "window_title_changed",
                    return_value=None,
                ) as update_title:
                    server.handle_window_name_changed(focus)

                read_detail.assert_not_called()
                update_title.assert_called_once_with(
                    focus,
                    NORMAL_BROWSER_STATUS,
                    include_snapshot=False,
                )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
