import unittest

from src.tray import _is_xapp_desktop


class TestTrayBackendSelection(unittest.TestCase):
    def test_cinnamon_uses_xapp(self):
        self.assertTrue(_is_xapp_desktop("X-Cinnamon", False))

    def test_gnome_uses_appindicator(self):
        self.assertFalse(_is_xapp_desktop("ubuntu:GNOME", False))

    def test_running_xapp_applet_uses_xapp(self):
        self.assertTrue(_is_xapp_desktop("MATE", True))

    def test_missing_desktop_env(self):
        self.assertFalse(_is_xapp_desktop(None, False))


if __name__ == "__main__":
    unittest.main()
