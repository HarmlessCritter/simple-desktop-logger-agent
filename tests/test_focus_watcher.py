import unittest

from focus_watcher import FocusInfo, WINDOWS_OPERATION, get_display_name


class FocusWatcherTests(unittest.TestCase):
    def test_temporary_pid_identity_is_replaced_when_process_name_resolves(self) -> None:
        temporary = FocusInfo(10, 20, "pid:20", "", "WindowClass", "", "pid:20")
        resolved = FocusInfo(10, 20, "new-app.exe", "C:/new-app.exe", "WindowClass", "", "new-app.exe")
        self.assertNotEqual(temporary.key, resolved.key)

    def test_explorer_display_name_does_not_depend_on_a_stored_title(self) -> None:
        self.assertEqual(get_display_name("explorer.exe", "CabinetWClass"), WINDOWS_OPERATION)
