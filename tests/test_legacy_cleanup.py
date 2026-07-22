import os
import tempfile
import unittest

from src.cleanup import remove_legacy_user_install

OPT_ROOT = "/opt/claude-tracker/src/cleanup.py"


def make_legacy_install(home):
    """Recreate the artifacts `make install` leaves in the user's home."""
    app_dir = os.path.join(home, ".local/share/claude-tracker")
    os.makedirs(os.path.join(app_dir, "src"))
    with open(os.path.join(app_dir, "run.py"), "w") as f:
        f.write("# legacy copy\n")

    launcher = os.path.join(home, ".local/bin/claude-tracker")
    os.makedirs(os.path.dirname(launcher))
    with open(launcher, "w") as f:
        f.write(f"#!/bin/bash\npython3 {app_dir}/run.py \"$@\"\n")

    desktop_body = (
        "[Desktop Entry]\nType=Application\n"
        f"Exec={launcher}\nName=Claude Tracker\n"
    )
    autostart = os.path.join(home, ".config/autostart/claude-tracker.desktop")
    os.makedirs(os.path.dirname(autostart))
    with open(autostart, "w") as f:
        f.write(desktop_body)

    app_entry = os.path.join(home, ".local/share/applications/claude-tracker.desktop")
    os.makedirs(os.path.dirname(app_entry))
    with open(app_entry, "w") as f:
        f.write(desktop_body)

    return app_dir, launcher, autostart, app_entry


class RemoveLegacyUserInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_removes_all_legacy_artifacts_when_running_from_opt(self):
        paths = make_legacy_install(self.home)
        removed = remove_legacy_user_install(home=self.home, running_path=OPT_ROOT)
        for path in paths:
            self.assertFalse(os.path.exists(path), f"{path} should be removed")
        self.assertEqual(sorted(removed), sorted(paths))

    def test_noop_when_not_running_from_opt(self):
        paths = make_legacy_install(self.home)
        legacy_running = os.path.join(
            self.home, ".local/share/claude-tracker/src/cleanup.py")
        removed = remove_legacy_user_install(
            home=self.home, running_path=legacy_running)
        self.assertEqual(removed, [])
        for path in paths:
            self.assertTrue(os.path.exists(path), f"{path} should be kept")

    def test_keeps_desktop_entries_that_do_not_point_at_user_install(self):
        make_legacy_install(self.home)
        autostart = os.path.join(self.home, ".config/autostart/claude-tracker.desktop")
        with open(autostart, "w") as f:
            f.write("[Desktop Entry]\nExec=/usr/bin/claude-tracker\n")
        remove_legacy_user_install(home=self.home, running_path=OPT_ROOT)
        self.assertTrue(os.path.exists(autostart))

    def test_noop_when_nothing_to_clean(self):
        removed = remove_legacy_user_install(home=self.home, running_path=OPT_ROOT)
        self.assertEqual(removed, [])


if __name__ == "__main__":
    unittest.main()
