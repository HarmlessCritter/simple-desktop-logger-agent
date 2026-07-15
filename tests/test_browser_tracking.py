import unittest
from unittest.mock import patch

from browser_tracking import (
    browser_detail_key,
    browser_tracking_status,
    favicon_url,
    NORMAL_BROWSER_STATUS,
    PRIVATE_BROWSER_STATUS,
    UNKNOWN_BROWSER_STATUS,
    normalize_browser_host,
    read_browser_detail,
    site_display_name,
)
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


class BrowserTrackingTests(unittest.TestCase):
    def test_known_sites_keep_curated_labels(self) -> None:
        self.assertEqual(normalize_browser_host("https://music.youtube.com/watch?v=1"), "music.youtube.com")
        self.assertEqual(site_display_name("music.youtube.com"), "YouTube Music")
        self.assertEqual(site_display_name("chatgpt.com"), "ChatGPT")
        self.assertEqual(site_display_name("github.com"), "GitHub")

    def test_unknown_hosts_use_a_readable_fallback_label(self) -> None:
        self.assertEqual(normalize_browser_host("https://www.example-service.com/path"), "example-service.com")
        self.assertEqual(site_display_name("example-service.com"), "Example Service")

    def test_local_hosts_have_a_bindable_local_source(self) -> None:
        for host in ("localhost", "127.0.0.1", "::1"):
            self.assertEqual(browser_tracking_status(host), "tracked")
            self.assertEqual(site_display_name(host, "tracked"), "Local")
            self.assertEqual(favicon_url(host, "tracked"), "")
            self.assertEqual(
                browser_detail_key({"host": host, "tracking_status": "tracked"}),
                "browser:local",
            )

    def test_private_chrome_never_reads_its_address_bar(self) -> None:
        focus = chrome_focus()
        with patch("browser_tracking.ChromeWindowInspector.privacy_mode", return_value=PRIVATE_BROWSER_STATUS), patch(
            "browser_tracking.ChromeUrlReader.read_url"
        ) as read_url:
            detail = read_browser_detail(focus)

        self.assertIsNotNone(detail)
        self.assertEqual(detail.tracking_status, "other")
        self.assertEqual(detail.host, "")
        self.assertEqual(detail.url, "")
        self.assertEqual(detail.title, "")
        self.assertEqual(detail.privacy_mode, PRIVATE_BROWSER_STATUS)
        read_url.assert_not_called()

    def test_unknown_chrome_state_also_never_reads_its_address_bar(self) -> None:
        focus = chrome_focus()
        with patch("browser_tracking.ChromeWindowInspector.privacy_mode", return_value=UNKNOWN_BROWSER_STATUS), patch(
            "browser_tracking.ChromeUrlReader.read_url"
        ) as read_url:
            detail = read_browser_detail(focus)

        self.assertIsNotNone(detail)
        self.assertEqual(detail.tracking_status, "other")
        self.assertEqual(detail.privacy_mode, UNKNOWN_BROWSER_STATUS)
        read_url.assert_not_called()

    def test_normal_chrome_keeps_address_bar_tracking(self) -> None:
        focus = chrome_focus()
        with patch("browser_tracking.ChromeWindowInspector.privacy_mode", return_value=NORMAL_BROWSER_STATUS), patch(
            "browser_tracking.ChromeUrlReader.read_url", return_value="https://youtube.com/watch?v=1"
        ):
            detail = read_browser_detail(focus)

        self.assertIsNotNone(detail)
        self.assertEqual(detail.host, "youtube.com")
        self.assertEqual(detail.privacy_mode, NORMAL_BROWSER_STATUS)


if __name__ == "__main__":
    unittest.main()
