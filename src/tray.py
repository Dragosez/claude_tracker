import os

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


def _load_xapp():
    try:
        gi.require_version('XApp', '1.0')
        from gi.repository import XApp
        return XApp
    except (ImportError, ValueError):
        return None


def _is_xapp_desktop(desktop, xapp_monitors_running):
    """Cinnamon (Linux Mint) ignores AppIndicator labels, so we must use XApp
    there. Other desktops with an XApp status applet running also show labels."""
    desktops = [d.strip().lower() for d in (desktop or "").split(":")]
    return any("cinnamon" in d for d in desktops) or xapp_monitors_running


class _XAppTray:
    def __init__(self, XApp, app_id, icon_path):
        self.icon = XApp.StatusIcon()
        self.icon.set_name(app_id)
        self.icon.set_icon_name(icon_path)
        self.icon.set_tooltip_text("Claude Tracker")
        self.icon.set_visible(True)

    def set_menu(self, menu):
        # Show the same menu on left and right click
        self.icon.set_primary_menu(menu)
        self.icon.set_secondary_menu(menu)

    def set_label(self, label):
        self.icon.set_label(label)
        self.icon.set_tooltip_text(f"Claude Tracker: {label.strip()}")


class _AppIndicatorTray:
    def __init__(self, app_id, icon_path):
        gi.require_version('AyatanaAppIndicator3', '0.1')
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
        self.indicator = AppIndicator.Indicator.new(
            app_id,
            icon_path,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    def set_menu(self, menu):
        self.indicator.set_menu(menu)

    def set_label(self, label):
        self.indicator.set_label(label, " " * 30)


def create_tray(app_id, icon_path):
    XApp = _load_xapp()
    if XApp is not None:
        try:
            monitors = XApp.StatusIcon.any_monitors()
        except Exception:
            monitors = False
        if _is_xapp_desktop(os.environ.get("XDG_CURRENT_DESKTOP"), monitors):
            print("DEBUG: Using XApp.StatusIcon tray backend")
            return _XAppTray(XApp, app_id, icon_path)
    print("DEBUG: Using AyatanaAppIndicator tray backend")
    return _AppIndicatorTray(app_id, icon_path)
