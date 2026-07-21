import os
import re
import sys
import time
import threading
import subprocess
from datetime import datetime

import requests

# Ensure prints show up immediately in the terminal
sys.stdout.reconfigure(line_buffering=True)

if os.geteuid() == 0:
    print("ERROR: Claude Tracker must NOT be run as root or with sudo.")
    print("Running as root breaks the WebKit sandbox (causing gray screens) and prevents the app indicator from showing up on your desktop.")
    print("Please run it as your normal user (e.g. simply type `claude-tracker`).")
    sys.exit(1)

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, AyatanaAppIndicator3 as AppIndicator, GLib, Gio

from .auth import get_session
from .config import clear_config, save_config, load_config
from .usage import extract_model_limits

# Constants
APP_ID = "claude-tracker"
VERSION = "1.0.5"
RELEASES_API_URL = "https://api.github.com/repos/Dragosez/claude_tracker/releases/latest"
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
        
        self.dynamic_model_items = {}
        
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
        
        self.item_select_plan = Gtk.MenuItem(label="Select Plan")
        self.plan_menu = Gtk.Menu()
        self.item_select_plan.set_submenu(self.plan_menu)
        self.menu.append(self.item_select_plan)
        
        item_refresh = Gtk.MenuItem(label="Refresh Data")
        item_refresh.connect("activate", lambda _: self.refresh_data())
        self.menu.append(item_refresh)
        
        item_login = Gtk.MenuItem(label="Login / Change Account")
        item_login.connect("activate", lambda _: self.open_login())
        self.menu.append(item_login)

        self.menu.append(Gtk.SeparatorMenuItem())

        self.item_update = Gtk.MenuItem(label=f"Version: {VERSION}")
        self.item_update.connect("activate", self._on_update_clicked)
        self.menu.append(self.item_update)

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

        # Check for updates in background: once at startup, then every 24h
        # for machines that stay on for days
        self.update_available = False
        self.latest_version_data = None
        threading.Thread(target=self._check_for_updates, daemon=True).start()
        GLib.timeout_add_seconds(24 * 3600, self._schedule_update_check)

    def _schedule_update_check(self):
        threading.Thread(target=self._check_for_updates, daemon=True).start()
        return True

    def _check_for_updates(self):
        try:
            print(f"Checking for updates at {RELEASES_API_URL}...")
            response = requests.get(RELEASES_API_URL, timeout=10)
            if response.status_code == 200:
                data = response.json()
                latest_tag = data.get("tag_name", "")
                if latest_tag and self._is_newer(latest_tag, VERSION):
                    print(f"Update available: {VERSION} -> {latest_tag}")
                    self.update_available = True
                    self.latest_version_data = data
                    GLib.idle_add(lambda: self.item_update.set_label(f"Update to {latest_tag} Available!"))
                else:
                    print(f"Already on latest version: {VERSION}")
        except Exception as e:
            print(f"Update check failed: {e}")

    def _is_newer(self, latest, current):
        # Strip any leading non-digit prefix so tags like "v1.0.2" and
        # "v.1.0.2" both normalize to "1.0.2"
        l = re.sub(r"^[^0-9]*", "", latest)
        c = re.sub(r"^[^0-9]*", "", current)
        try:
            l_parts = [int(p) for p in l.split(".")]
            c_parts = [int(p) for p in c.split(".")]
            return l_parts > c_parts
        except ValueError:
            return l != c

    def _on_update_clicked(self, _):
        if not self.update_available or not self.latest_version_data:
            return

        version_name = self.latest_version_data["tag_name"]
        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"New version {version_name} is available!"
        )
        dialog.format_secondary_text("Would you like to download and install it now?")
        response = dialog.run()
        dialog.destroy()

        if response == Gtk.ResponseType.YES:
            threading.Thread(target=self._perform_update, daemon=True).start()

    def _perform_update(self):
        try:
            GLib.idle_add(lambda: self.item_update.set_label("Updating..."))

            assets = self.latest_version_data.get("assets", [])
            deb_url = next((a["browser_download_url"] for a in assets if a["name"].endswith(".deb")), None)
            if not deb_url:
                raise Exception("No .deb package found in the latest release.")

            print(f"Downloading update from {deb_url}...")
            temp_path = "/tmp/claude-tracker-update.deb"
            with requests.get(deb_url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(temp_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            print("Installing update via pkexec...")
            process = subprocess.Popen(["pkexec", "dpkg", "-i", temp_path])
            process.wait()

            if process.returncode == 0:
                print("Update installed successfully. Restarting...")
                # Prefer the system launcher so we pick up the freshly
                # installed copy even if this process was started from a
                # user-local install
                launcher = "/usr/bin/claude-tracker"
                if os.path.exists(launcher):
                    os.execv(launcher, [launcher])
                else:
                    os.execv(sys.executable, [sys.executable] + sys.argv)
            else:
                raise Exception(f"Installation failed with exit code {process.returncode}")
        except Exception as e:
            print(f"Update error: {e}")
            GLib.idle_add(lambda: self.item_update.set_label(f"Update Failed: {str(e)[:30]}..."))

    def _ui_heartbeat(self):
        # Toggle a trailing space to force the indicator label to redraw;
        # some shells drop the label after suspend/panel restarts otherwise
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
        self.session.fetch_json("https://claude.ai/api/organizations", self._on_orgs_fetched)
        return True

    def _on_orgs_fetched(self, data, error):
        if error:
            print(f"DEBUG: Org fetch error: {error}")
            return
        if data and len(data) > 0:
            for child in self.plan_menu.get_children():
                self.plan_menu.remove(child)

            valid_orgs = [org["uuid"] for org in data]
            if self.org_id not in valid_orgs:
                self.org_id = valid_orgs[0]
                save_config({"organization_uuid": self.org_id})

            for org in data:
                name = org.get("name") or "Unknown Plan"
                uuid = org["uuid"]
                is_active = (uuid == self.org_id)
                
                label = f"{name}{' (Active)' if is_active else ''}"
                item = Gtk.MenuItem(label=label)
                item.connect("activate", self._make_org_selector(uuid))
                self.plan_menu.append(item)
            
            self.plan_menu.show_all()
            self._fetch_usage()

    def _make_org_selector(self, uuid):
        def on_select(_):
            self.org_id = uuid
            save_config({"organization_uuid": self.org_id})
            self.refresh_data()
        return on_select

    def _fetch_usage(self):
        url = f"https://claude.ai/api/organizations/{self.org_id}/usage"
        self.session.fetch_json(url, self._on_usage_fetched)

    def _format_time(self, timestamp, include_day=False):
        if not timestamp: return None
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            dt_local = dt.astimezone()
            if include_day:
                weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                day_name = weekdays[dt_local.weekday()]
                return f"{day_name} {dt_local.strftime('%H:%M')}"
            else:
                return dt_local.strftime('%H:%M')
        except Exception:
            if include_day:
                return timestamp
            return timestamp[11:16]

    def _on_usage_fetched(self, data, error):
        if error:
            print(f"DEBUG: Usage fetch error: {error}")
            self._safe_set_label("Auth Error")
            return
        
        if not data:
            data = {}

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
                r7 = self._format_time(seven_day.get("resets_at"), include_day=True)
                self.item_usage_7d.set_label(f"All models (Weekly): {p7}%" + (f" ({r7})" if r7 else ""))
                
            # 3. Per-model usage (modern `limits` array, legacy seven_day_*
            # and iguana_necktie keys as fallback)
            model_rows = extract_model_limits(data)
            active_model_keys = [row["key"] for row in model_rows]

            # Remove any dynamic menu items that are no longer active
            keys_to_remove = []
            for key, item in self.dynamic_model_items.items():
                if key not in active_model_keys:
                    self.menu.remove(item)
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                del self.dynamic_model_items[key]

            # Update or create menu items for the active rows
            children = self.menu.get_children()
            idx_7d = children.index(self.item_usage_7d)

            for i, row in enumerate(model_rows):
                key = row["key"]
                r = self._format_time(row["resets_at"], include_day=True)
                label_text = f"{row['name']}: {row['percent']}%" + (f" ({r})" if r else "")

                if key in self.dynamic_model_items:
                    self.dynamic_model_items[key].set_label(label_text)
                    self.dynamic_model_items[key].show()
                else:
                    item = Gtk.MenuItem(label=label_text)
                    item.set_sensitive(False)
                    insert_idx = idx_7d + 1 + i
                    self.menu.insert(item, insert_idx)
                    self.dynamic_model_items[key] = item
                    item.show()
                
            # 4. Routine Runs (Mapping if available, otherwise hide)
            routines = data.get("routine_runs")
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
