import os
import sys
import time
from datetime import datetime

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, AyatanaAppIndicator3 as AppIndicator, GLib, Gio

from .auth import get_session
from .config import clear_config, save_config, load_config

# Constants
APP_ID = "claude-tracker"
VERSION = "1.0.1"
ICON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", "claude-tracker-icon.png"))

class ClaudeTrackerApp:
    def __init__(self):
        self.is_fetching = False
        self.current_label = "Login Required"
        self.org_id = (load_config() or {}).get("organization_uuid")
        
        self.indicator = AppIndicator.Indicator.new(
            APP_ID,
            ICON_PATH,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._safe_set_label(self.current_label)
        
        # Build menu
        self.menu = Gtk.Menu()
        self.item_usage = Gtk.MenuItem(label="Current session: ...")
        self.item_usage.set_sensitive(False)
        self.menu.append(self.item_usage)
        
        self.item_usage_7d = Gtk.MenuItem(label="All models (Weekly): ...")
        self.item_usage_7d.set_sensitive(False)
        self.menu.append(self.item_usage_7d)
        
        self.item_sonnet = Gtk.MenuItem(label="Sonnet only: ...")
        self.item_sonnet.set_sensitive(False)
        self.menu.append(self.item_sonnet)
        
        self.item_design = Gtk.MenuItem(label="Claude Design: ...")
        self.item_design.set_sensitive(False)
        self.menu.append(self.item_design)
        
        self.item_routines = Gtk.MenuItem(label="Daily routines: ...")
        self.item_routines.set_sensitive(False)
        self.menu.append(self.item_routines)
        
        self.menu.append(Gtk.SeparatorMenuItem())
        
        self.item_reset = Gtk.MenuItem(label="Resets at: ...")
        self.item_reset.set_sensitive(False)
        self.menu.append(self.item_reset)
        
        self.item_time = Gtk.MenuItem(label="Last Checked: ...")
        self.item_time.set_sensitive(False)
        self.menu.append(self.item_time)
        
        self.menu.append(Gtk.SeparatorMenuItem())
        item_refresh = Gtk.MenuItem(label="Refresh Data")
        item_refresh.connect("activate", lambda _: self.refresh_data())
        self.menu.append(item_refresh)
        
        item_login = Gtk.MenuItem(label="Login / Change Account")
        item_login.connect("activate", lambda _: self.open_login())
        self.menu.append(item_login)
        
        self.menu.append(Gtk.SeparatorMenuItem())
        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        self.menu.append(item_quit)
        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        # Initialize Session
        self.session = get_session(on_success=self.refresh_data)
        
        # Automatically start the engine if we likely have auth
        cookies_path = os.path.expanduser("~/.config/claude-tracker/cookies.txt")
        if self.org_id or (os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0):
            self.session.ensure_started()

        # Periodic updates - refresh_data will skip if session not ready
        GLib.timeout_add_seconds(10 * 60, self.refresh_data)
        GLib.timeout_add_seconds(15, self._ui_heartbeat)

    def _ui_heartbeat(self):
        label = self.current_label
        new_label = label.rstrip(" ") + (" " if not label.endswith(" ") else "")
        self._safe_set_label(new_label)
        return True

    def _safe_set_label(self, label):
        self.current_label = label
        GLib.idle_add(self._do_set_label, label)

    def _do_set_label(self, label):
        try:
            self.indicator.set_label(label, " " * 30)
        except Exception as e:
            print(f"DEBUG: Indicator set_label error: {e}")
        return False

    def open_login(self):
        self.session.ensure_started()
        self.session.show_all()
        self.session.present() # Bring to front

    def refresh_data(self):
        if not self.session.is_ready:
            print("DEBUG: Session not ready yet, skipping refresh.")
            return True
            
        print("DEBUG: Refreshing data via WebKit...")
        if not self.org_id:
            self.session.fetch_json("https://claude.ai/api/organizations", self._on_orgs_fetched)
        else:
            self._fetch_usage()
        return True

    def _on_orgs_fetched(self, data, error):
        if error:
            print(f"DEBUG: Org fetch error: {error}")
            return
        if data and len(data) > 0:
            self.org_id = data[0]["uuid"]
            save_config({"organization_uuid": self.org_id})
            self._fetch_usage()

    def _fetch_usage(self):
        url = f"https://claude.ai/api/organizations/{self.org_id}/usage"
        self.session.fetch_json(url, self._on_usage_fetched)

    def _format_time(self, timestamp):
        if not timestamp: return None
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            return dt.astimezone().strftime('%H:%M')
        except:
            return timestamp[11:16]

    def _on_usage_fetched(self, data, error):
        if error:
            print(f"DEBUG: Usage fetch error: {error}")
            self._safe_set_label("Auth Error")
            return
        
        try:
            # 1. Current Session (5h)
            five_hour = data.get("five_hour", {})
            util = five_hour.get("utilization", 0)
            pct = int(util * 100) if isinstance(util, float) and util <= 1.0 else int(util)
            reset_str = self._format_time(five_hour.get("resets_at")) or "..."

            if reset_str != "...":
                label = f"{pct}% ({reset_str})"
            else:
                label = f"{pct}%"

            self._safe_set_label(label)
            self.item_usage.set_label(f"Current session: {pct}%" + (f" (Resets {reset_str})" if reset_str != "..." else ""))
            
            # 2. All Models (Weekly)
            seven_day = data.get("seven_day", {})
            if seven_day:
                u7 = seven_day.get("utilization", 0)
                p7 = int(u7 * 100) if isinstance(u7, float) and u7 <= 1.0 else int(u7)
                r7 = self._format_time(seven_day.get("resets_at"))
                self.item_usage_7d.set_label(f"All models (Weekly): {p7}%" + (f" ({r7})" if r7 else ""))
                
            # 3. Sonnet Only (Mapping to seven_day_sonnet)
            sonnet = data.get("seven_day_sonnet")
            if sonnet:
                us = sonnet.get("utilization", 0)
                ps = int(us * 100) if isinstance(us, float) and us <= 1.0 else int(us)
                rs = self._format_time(sonnet.get("resets_at"))
                self.item_sonnet.set_label(f"Sonnet only: {ps}%" + (f" ({rs})" if rs else ""))
                self.item_sonnet.show()
            else:
                self.item_sonnet.hide()
                
            # 4. Claude Design (Mapping to seven_day_omelette)
            design = data.get("seven_day_omelette")
            if design:
                ud = design.get("utilization", 0)
                pd = int(ud * 100) if isinstance(ud, float) and ud <= 1.0 else int(ud)
                rsd = self._format_time(design.get("resets_at"))
                self.item_design.set_label(f"Claude Design: {pd}%" + (f" ({rsd})" if rsd else ""))
                self.item_design.show()
            else:
                self.item_design.hide()
                
            # 5. Routine Runs (Mapping if available, otherwise hide)
            routines = data.get("routine_runs") or data.get("iguana_necktie") # Fallback check
            if routines and isinstance(routines, dict):
                curr = routines.get("current", 0)
                lim = routines.get("limit", 15)
                self.item_routines.set_label(f"Daily routines: {curr}/{lim}")
                self.item_routines.show()
            else:
                self.item_routines.hide()
                
            self.item_reset.set_label(f"Resets at: {reset_str}")
            self.item_time.set_label(f"Last Checked: {datetime.now().strftime('%H:%M')}")
        except Exception as e:
            print(f"DEBUG: UI update error: {e}")

def main():
    app = ClaudeTrackerApp()
    Gtk.main()

if __name__ == "__main__":
    main()
