import unittest

from binding_summary import (
    apply_bindings_to_totals,
    current_summary_target,
    ensure_current_group_item,
)
from focus_watcher import FocusInfo


def chrome_focus() -> FocusInfo:
    return FocusInfo(
        hwnd=101,
        pid=202,
        process_name="chrome.exe",
        process_path="chrome.exe",
        window_class="Chrome_WidgetWin_1",
        window_title="Tooli - Chrome",
        display_name="chrome.exe",
    )


class BindingSummaryTests(unittest.TestCase):
    def test_moves_activity_and_browser_source_into_one_group(self) -> None:
        totals = {
            "league of legends.exe": {
                "activityKey": "league of legends.exe",
                "displayName": "League of Legends.exe",
                "totalMs": 80_000,
                "focus": {"process_name": "League of Legends.exe"},
            },
            "chrome.exe": {
                "activityKey": "chrome.exe",
                "displayName": "chrome.exe",
                "totalMs": 100_000,
                "focus": {"process_name": "chrome.exe"},
                "browserDetails": [
                    {
                        "key": "browser:tooli.com",
                        "label": "Tooli's Classic Games",
                        "host": "tooli.com",
                        "faviconUrl": "https://example.test/tooli.ico",
                        "totalMs": 43_456,
                    },
                    {
                        "key": "browser:youtube.com",
                        "label": "YouTube",
                        "host": "youtube.com",
                        "faviconUrl": "https://example.test/youtube.ico",
                        "totalMs": 56_544,
                    },
                ],
            },
        }
        groups = [{"groupId": "game", "displayName": "Game"}]
        bindings = {
            "league of legends.exe": "game",
            "browser:tooli.com": "game",
        }

        result = apply_bindings_to_totals(totals, groups, bindings)

        self.assertNotIn("league of legends.exe", result)
        self.assertEqual(result["chrome.exe"]["totalMs"], 56_544)
        self.assertEqual(
            [item["key"] for item in result["chrome.exe"]["browserDetails"]],
            ["browser:youtube.com"],
        )
        game = result["group:game"]
        self.assertEqual(game["totalMs"], 123_456)
        self.assertEqual(
            {item["sourceKey"] for item in game["groupItems"]},
            {"league of legends.exe", "browser:tooli.com"},
        )

    def test_current_bound_browser_targets_group_and_injects_zero_item(self) -> None:
        groups = [{"groupId": "game", "displayName": "Game"}]
        bindings = {"browser:tooli.com": "game"}
        browser_detail = {
            "host": "tooli.com",
            "tracking_status": "tracked",
        }
        target = current_summary_target(
            chrome_focus(),
            browser_detail,
            1_000,
            "chrome.exe",
            groups,
            bindings,
        )
        totals = apply_bindings_to_totals({}, groups, bindings)
        ensure_current_group_item(totals, target, chrome_focus(), browser_detail)

        self.assertEqual(target["activityKey"], "group:game")
        self.assertEqual(target["sourceKey"], "browser:tooli.com")
        self.assertEqual(totals["group:game"]["groupItems"][0]["sourceKey"], "browser:tooli.com")
        self.assertEqual(totals["group:game"]["groupItems"][0]["totalMs"], 0)

    def test_other_browser_source_never_becomes_summary_target(self) -> None:
        target = current_summary_target(
            chrome_focus(),
            {"host": "", "tracking_status": "other"},
            1_000,
            "chrome.exe",
            [{"groupId": "game", "displayName": "Game"}],
            {"browser:other": "game"},
        )
        self.assertIsNone(target)


if __name__ == "__main__":
    unittest.main()
