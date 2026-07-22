"""Removal of leftover user-level (make install) copies of the app.

A `make install` copy lives under ~/.local and registers user-level
autostart/application .desktop entries. Those entries shadow the system
ones from the .deb package (XDG user dirs take precedence), so after an
auto-update the next login silently boots the stale user copy again.
"""
import os
import shutil

SYSTEM_INSTALL_ROOT = "/opt/claude-tracker"

# Paths relative to $HOME, as created by `make install`
LEGACY_APP_DIR = ".local/share/claude-tracker"
LEGACY_LAUNCHER = ".local/bin/claude-tracker"
LEGACY_DESKTOP_ENTRIES = (
    ".config/autostart/claude-tracker.desktop",
    ".local/share/applications/claude-tracker.desktop",
)


def remove_legacy_user_install(home=None, running_path=None):
    """If running from the system (.deb) install, delete any leftover
    user-level install that would shadow it at login. Returns the list of
    paths removed."""
    home = home or os.path.expanduser("~")
    running_path = running_path or os.path.abspath(__file__)
    if not running_path.startswith(SYSTEM_INSTALL_ROOT + os.sep):
        return []

    removed = []

    app_dir = os.path.join(home, LEGACY_APP_DIR)
    if os.path.isdir(app_dir):
        shutil.rmtree(app_dir, ignore_errors=True)
        removed.append(app_dir)

    launcher = os.path.join(home, LEGACY_LAUNCHER)
    if os.path.isfile(launcher):
        os.remove(launcher)
        removed.append(launcher)

    # Only drop .desktop entries that actually point at the user install,
    # in case someone hand-wrote an entry for the system launcher
    for rel in LEGACY_DESKTOP_ENTRIES:
        path = os.path.join(home, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                content = f.read()
        except OSError:
            continue
        if ".local/bin/claude-tracker" in content or LEGACY_APP_DIR in content:
            os.remove(path)
            removed.append(path)

    return removed
