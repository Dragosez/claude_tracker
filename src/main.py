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
from .cleanup import remove_legacy_user_install
from .config import clear_config, save_config, load_config
from .cswap import (
    is_cswap_available,
    is_cswap_installed,
    is_cswap_data_available,
    fetch_cswap_accounts_async,
    switch_cswap_account_async,
    cswap_account_to_tracker_payload,
    format_account_label,
    get_account_age_seconds,
    format_age,
)
from .standalone import (
    CREDENTIALS_PATH,
    fetch_standalone_accounts_async,
    switch_standalone_account_async,
    import_cswap_accounts_async,
    load_standalone_accounts,
    clear_inactive_standalone_accounts,
)
from .pace import compute_session_pace, compute_weekly_pace
from .usage import extract_model_limits
from .watchdog import is_stalled

# Constants
APP_ID = "claude-tracker"
VERSION = "1.0.12"
RELEASES_API_URL = "https://api.github.com/repos/Dragosez/claude_tracker/releases/latest"
ICON_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", "claude-tracker-icon.png"))

class ClaudeTrackerApp:
    def __init__(self):
        self.is_fetching = False
        self.is_fetching_cswap = False
        self.is_fetching_standalone = False
        self.last_fetch_completed = time.time()
        self.current_label = "Login Required"
        
        cfg = load_config() or {}
        self.org_id = cfg.get("organization_uuid")
        self.pinned_account = cfg.get("pinned_account")
        self.follow_cli_active = cfg.get("follow_cli_active", True)
        
        configured_source = cfg.get("account_source")
        if configured_source in ("standalone", "cswap"):
            self.account_source = configured_source
        else:
            if is_cswap_installed() and not os.path.exists(CREDENTIALS_PATH):
                self.account_source = "cswap"
            else:
                self.account_source = "standalone"

        self.cswap_accounts = []
        self.standalone_accounts = []
        
        self.indicator = AppIndicator.Indicator.new(
            APP_ID,
            ICON_PATH,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._safe_set_label(self.current_label)
        
        # Build menu
        self.menu = Gtk.Menu()

        self.item_account_header = Gtk.MenuItem(label="Account: ...")
        self.item_account_header.set_sensitive(False)
        self.menu.append(self.item_account_header)

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

        self.item_accounts = Gtk.MenuItem(label="Accounts")
        self.accounts_menu = Gtk.Menu()
        self.item_accounts.set_submenu(self.accounts_menu)
        self.menu.append(self.item_accounts)
        
        self.item_select_plan = Gtk.MenuItem(label="Select Plan")
        self.plan_menu = Gtk.Menu()
        self.item_select_plan.set_submenu(self.plan_menu)
        self.menu.append(self.item_select_plan)
        
        item_refresh = Gtk.MenuItem(label="Refresh Data")
        item_refresh.connect("activate", lambda _: self.refresh_data())
        self.menu.append(item_refresh)
        
        self.item_login = Gtk.MenuItem(label="Login / Change Account")
        self.item_login.connect("activate", lambda _: self.open_login())
        self.menu.append(self.item_login)

        self.menu.append(Gtk.SeparatorMenuItem())

        self.item_update = Gtk.MenuItem(label=f"Version: {VERSION}")
        self.item_update.connect("activate", self._on_update_clicked)
        self.menu.append(self.item_update)

        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", lambda _: Gtk.main_quit())
        self.menu.append(item_quit)
        self.menu.show_all()
        
        # Hide accounts items until cswap accounts are discovered
        self.item_account_header.hide()
        self.item_accounts.hide()

        self.indicator.set_menu(self.menu)

        # Initialize Session lazily (skip WebKit startup if CLI credentials exist)
        self.session = None
        cookies_path = os.path.expanduser("~/.config/claude-tracker/cookies.txt")
        has_cookies = os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0
        if not os.path.exists(CREDENTIALS_PATH) and not is_cswap_available():
            self.session = get_session(on_success=self.refresh_data)
            if self.org_id or has_cookies:
                self.session.ensure_started()

        # Monitor ~/.claude/.credentials.json for instant account switch detection
        self._creds_monitor = None
        self._creds_refresh_timeout = None
        self._setup_creds_monitor()

        # Periodic updates: poll every 60s
        GLib.timeout_add_seconds(60, self.refresh_data)
        GLib.timeout_add_seconds(15, self._ui_heartbeat)

        # Check for updates in background: once at startup, then every 24h
        # for machines that stay on for days
        self.update_available = False
        self.latest_version_data = None
        threading.Thread(target=self._check_for_updates, daemon=True).start()
        GLib.timeout_add_seconds(24 * 3600, self._schedule_update_check)

        # Trigger immediate data fetch once (must return False to avoid infinite idle loop)
        def _initial_refresh():
            self.refresh_data()
            return False

        GLib.idle_add(_initial_refresh)

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
        if not self.session:
            self.session = get_session(on_success=self.refresh_data)
        self.session.ensure_started()
        self.session.show_all()
        self.session.present() # Bring to front

    def _setup_creds_monitor(self):
        try:
            creds_file = Gio.File.new_for_path(CREDENTIALS_PATH)
            self._creds_monitor = creds_file.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._creds_monitor.connect("changed", self._on_creds_file_changed)
        except Exception as e:
            print(f"DEBUG: Could not set up credentials file monitor: {e}")

    def _on_creds_file_changed(self, monitor, file, other_file, event_type):
        if event_type in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        ):
            if self.account_source == "standalone" or self.follow_cli_active:
                if getattr(self, "_creds_refresh_timeout", None):
                    GLib.source_remove(self._creds_refresh_timeout)
                self._creds_refresh_timeout = GLib.timeout_add(300, self._do_creds_triggered_refresh)

    def _do_creds_triggered_refresh(self):
        self._creds_refresh_timeout = None
        self.refresh_data()
        return False

    def refresh_data(self):
        try:
            if self.account_source == "cswap" and is_cswap_available():
                if not self.is_fetching_cswap:
                    self.is_fetching_cswap = True
                    fetch_cswap_accounts_async(self._on_cswap_accounts_fetched)
                return True

            # Standalone mode: fetch via direct CLI credentials
            if not self.is_fetching_standalone:
                self.is_fetching_standalone = True
                fetch_standalone_accounts_async(self._on_standalone_accounts_fetched)
            return True
        except Exception as e:
            print(f"DEBUG: refresh_data error: {e}")
        return True

    def _on_cswap_accounts_fetched(self, accounts, error):
        GLib.idle_add(self._apply_cswap_accounts, accounts, error)

    def _apply_cswap_accounts(self, accounts, error):
        self.is_fetching_cswap = False
        if error or not accounts:
            if error:
                print(f"DEBUG: cswap fetch error: {error}")
            return False

        old_accounts = self.cswap_accounts
        old_pinned = getattr(self, "_last_rendered_pinned", None)
        self.cswap_accounts = accounts
        self.last_fetch_completed = time.time()

        # Determine target account
        target_account = None
        if self.follow_cli_active:
            target_account = next((a for a in accounts if a.get("active")), None)
            if target_account:
                self.pinned_account = target_account.get("number")

        if not target_account and self.pinned_account is not None:
            target_account = next(
                (a for a in accounts if str(a.get("number")) == str(self.pinned_account)),
                None,
            )

        if not target_account and accounts:
            target_account = accounts[0]
            self.pinned_account = target_account.get("number")

        needs_rebuild = (
            old_accounts != accounts or
            old_pinned != self.pinned_account or
            len(self.accounts_menu.get_children()) == 0
        )
        if needs_rebuild:
            self._last_rendered_pinned = self.pinned_account
            self._rebuild_accounts_menu()
            self.item_accounts.show()

        if target_account:
            self._display_account(target_account)

        # In cswap mode, hide WebKit plan menu and login button
        self.item_select_plan.hide()
        self.item_login.hide()
        return False

    def _on_standalone_accounts_fetched(self, accounts, error):
        GLib.idle_add(self._apply_standalone_accounts, accounts, error)

    def _apply_standalone_accounts(self, accounts, error):
        self.is_fetching_standalone = False
        if error:
            print(f"DEBUG: Standalone fetch error: {error}")

        old_accounts = self.standalone_accounts
        old_pinned = getattr(self, "_last_rendered_pinned", None)
        self.standalone_accounts = accounts or []
        self.last_fetch_completed = time.time()

        target_account = None
        if self.follow_cli_active:
            target_account = next((a for a in self.standalone_accounts if a.get("active")), None)
            if target_account:
                self.pinned_account = target_account.get("id") or target_account.get("email")

        if not target_account and self.pinned_account is not None:
            target_account = next(
                (
                    a for a in self.standalone_accounts
                    if str(a.get("id")) == str(self.pinned_account)
                    or str(a.get("email")) == str(self.pinned_account)
                    or (a.get("number") is not None and str(a.get("number")) == str(self.pinned_account))
                ),
                None,
            )

        if not target_account and self.standalone_accounts:
            target_account = self.standalone_accounts[0]
            self.pinned_account = target_account.get("id") or target_account.get("email")

        needs_rebuild = (
            old_accounts != self.standalone_accounts or
            old_pinned != self.pinned_account or
            len(self.accounts_menu.get_children()) == 0
        )
        if needs_rebuild:
            self._last_rendered_pinned = self.pinned_account
            self._rebuild_accounts_menu()
            self.item_accounts.show()

        if target_account:
            self._display_account(target_account)
            self.item_select_plan.hide()
        elif not self.standalone_accounts:
            self._fallback_to_webkit()

        return False

    def _fallback_to_webkit(self):
        if not self.session:
            self.session = get_session(on_success=self.refresh_data)
        cookies_path = os.path.expanduser("~/.config/claude-tracker/cookies.txt")
        if not self.session.started and (self.org_id or (os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0)):
            self.session.ensure_started()

        if self.session and self.session.is_ready:
            self.session.fetch_json("https://claude.ai/api/organizations", self._on_orgs_fetched)
            self.item_select_plan.show()
            self.item_login.show()
        else:
            self._safe_set_label("Login Required")
            self.item_account_header.set_label("Account: Not logged in")
            self.item_account_header.show()
            self.item_login.show()

    def _set_account_source(self, source):
        if self.account_source == source:
            return
        self.account_source = source
        save_config({"account_source": source})
        self.pinned_account = None
        self._last_rendered_pinned = None
        self.refresh_data()

    def _on_clear_inactive_clicked(self, _):
        removed = clear_inactive_standalone_accounts()
        def _show():
            msg = f"Cleared {removed} inactive account(s)." if removed > 0 else "No inactive accounts to clear."
            dialog = Gtk.MessageDialog(
                transient_for=None,
                flags=0,
                message_type=Gtk.MessageType.INFO,
                buttons=Gtk.ButtonsType.OK,
                text=msg,
            )
            dialog.run()
            dialog.destroy()
            self.refresh_data()
            return False
        GLib.idle_add(_show)

    def _on_import_cswap_clicked(self, _):
        import_cswap_accounts_async(self._on_import_cswap_completed)

    def _on_import_cswap_completed(self, count, err):
        def _show():
            if err:
                msg = f"Failed to import from cswap: {err}"
            elif count == 0:
                msg = "No new accounts to import from cswap."
            else:
                msg = f"Successfully imported {count} accounts from cswap!"
            dialog = Gtk.MessageDialog(
                transient_for=None,
                flags=0,
                message_type=Gtk.MessageType.INFO if not err else Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=msg,
            )
            dialog.run()
            dialog.destroy()
            self.refresh_data()
            return False
        GLib.idle_add(_show)

    def _rebuild_accounts_menu(self):
        for child in self.accounts_menu.get_children():
            self.accounts_menu.remove(child)

        if self.account_source == "cswap":
            def sort_key_cswap(a):
                num = a.get("number", 0)
                num_val = int(num) if str(num).isdigit() else 9999
                is_active = 0 if a.get("active") else 1
                is_ok = 0 if a.get("usageStatus") == "ok" else 1
                return (is_active, is_ok, num_val)

            sorted_accs = sorted(self.cswap_accounts, key=sort_key_cswap)
            for acc in sorted_accs:
                num = acc.get("number")
                is_pinned = str(num) == str(self.pinned_account)
                lbl = format_account_label(acc, is_pinned=is_pinned)
                item = Gtk.MenuItem(label=lbl)
                item.connect("activate", self._make_account_selector(num))
                self.accounts_menu.append(item)
        else:
            def sort_key_standalone(a):
                num = a.get("number", 9999)
                num_val = int(num) if str(num).isdigit() else 9999
                is_active = 0 if a.get("active") else 1
                is_ok = 0 if a.get("usageStatus") == "ok" else 1
                return (is_active, is_ok, num_val)

            sorted_accs = sorted(self.standalone_accounts, key=sort_key_standalone)
            if not sorted_accs:
                item_none = Gtk.MenuItem(label="No accounts (Run 'claude auth login')")
                item_none.set_sensitive(False)
                self.accounts_menu.append(item_none)
            else:
                for acc in sorted_accs:
                    acc_id = acc.get("id") or acc.get("email")
                    is_pinned = (
                        str(acc_id) == str(self.pinned_account)
                        or (acc.get("number") is not None and str(acc.get("number")) == str(self.pinned_account))
                    )
                    lbl = format_account_label(acc, is_pinned=is_pinned)
                    item = Gtk.MenuItem(label=lbl)
                    item.connect("activate", self._make_standalone_account_selector(acc))
                    self.accounts_menu.append(item)

        self.accounts_menu.append(Gtk.SeparatorMenuItem())

        item_follow = Gtk.CheckMenuItem(label="Follow CLI Active Account")
        item_follow.set_active(self.follow_cli_active)
        item_follow.connect("toggled", self._on_follow_cli_toggled)
        self.accounts_menu.append(item_follow)

        self.accounts_menu.append(Gtk.SeparatorMenuItem())

        # Account Source (flat items to avoid GNOME AppIndicator nested submenu sizing bug)
        item_src_standalone = Gtk.MenuItem(
            label="● Source: Standalone (CLI)" if self.account_source == "standalone" else "○ Source: Standalone (CLI)"
        )
        item_src_standalone.connect("activate", lambda _: self._set_account_source("standalone"))
        self.accounts_menu.append(item_src_standalone)

        cswap_label = "Source: cswap"
        if not is_cswap_installed():
            cswap_label += " (Cache only)" if is_cswap_data_available() else " (Not installed)"
        item_src_cswap = Gtk.MenuItem(
            label="● " + cswap_label if self.account_source == "cswap" else "○ " + cswap_label
        )
        item_src_cswap.connect("activate", lambda _: self._set_account_source("cswap"))
        self.accounts_menu.append(item_src_cswap)

        # Standalone management actions
        if self.account_source == "standalone":
            self.accounts_menu.append(Gtk.SeparatorMenuItem())
            if len(self.standalone_accounts) > 1:
                item_clear = Gtk.MenuItem(label="Clear Inactive Accounts")
                item_clear.connect("activate", self._on_clear_inactive_clicked)
                self.accounts_menu.append(item_clear)

            if is_cswap_data_available():
                item_import = Gtk.MenuItem(label="Import Accounts from cswap")
                item_import.connect("activate", self._on_import_cswap_clicked)
                self.accounts_menu.append(item_import)

        self.accounts_menu.show_all()

    def _make_account_selector(self, account_num):
        def on_select(_):
            self.pinned_account = account_num
            self.follow_cli_active = False
            save_config({
                "pinned_account": self.pinned_account,
                "follow_cli_active": False,
            })
            target = next(
                (a for a in self.cswap_accounts if str(a.get("number")) == str(account_num)),
                None,
            )
            if target:
                self._display_account(target)
            self._rebuild_accounts_menu()
        return on_select

    def _make_standalone_account_selector(self, target_account):
        def on_select(_):
            acc_id = target_account.get("id") or target_account.get("email")
            self.pinned_account = acc_id
            save_config({"pinned_account": self.pinned_account})

            if target_account.get("credentials") and not target_account.get("active"):
                print(f"DEBUG: Switching standalone account to {target_account.get('email')}...")
                switch_standalone_account_async(
                    acc_id,
                    callback=lambda success, err: GLib.idle_add(self.refresh_data),
                )
            else:
                self._display_account(target_account)
                self._rebuild_accounts_menu()
        return on_select

    def _on_follow_cli_toggled(self, widget):
        self.follow_cli_active = widget.get_active()
        save_config({"follow_cli_active": self.follow_cli_active})
        if self.follow_cli_active:
            if self.account_source == "standalone":
                active_acc = next((a for a in self.standalone_accounts if a.get("active")), None)
                if active_acc:
                    self.pinned_account = active_acc.get("id") or active_acc.get("email")
                    save_config({"pinned_account": self.pinned_account})
                    self._display_account(active_acc)
                    self._rebuild_accounts_menu()
            else:
                active_acc = next((a for a in self.cswap_accounts if a.get("active")), None)
                if active_acc:
                    self.pinned_account = active_acc.get("number")
                    save_config({"pinned_account": self.pinned_account})
                    self._display_account(active_acc)
                    self._rebuild_accounts_menu()

    def _display_account(self, account):
        num = account.get("number")
        num_str = f"#{num}: " if num is not None else ""
        email = account.get("email", "")
        alias = account.get("alias")
        name = account.get("name")
        display_name = alias or name
        tag = display_name or (f"{num}" if num is not None else (email.split("@")[0] if email else "cli"))
        active_badge = " [CLI Active]" if account.get("active") else ""
        if display_name and email and display_name != email:
            name_str = f"{display_name} ({email})"
        elif display_name:
            name_str = display_name
        else:
            name_str = email

        status_str = account.get("usageStatus", "ok")
        if status_str == "relogin_required":
            status_badge = " [Re-login needed]"
        elif status_str != "ok":
            status_badge = f" [{status_str.replace('_', ' ').title()}]"
        else:
            status_badge = ""
        self.item_account_header.set_label(f"Account: {num_str}{name_str}{status_badge}{active_badge}")
        self.item_account_header.show()

        data = cswap_account_to_tracker_payload(account)
        age_str = format_age(get_account_age_seconds(account))
        self._render_usage(data, account_tag=tag, age_str=age_str, status_str=status_str)

    def _render_usage(self, data, account_tag=None, age_str=None, status_str="ok"):
        try:
            # 1. Current Session (5h)
            five_hour = data.get("five_hour", {})
            util = five_hour.get("utilization", 0)
            if util is None:
                pct = 0
            else:
                pct = int(util * 100) if isinstance(util, float) and util <= 1.0 else int(util)
            reset_str = self._format_time(five_hour.get("resets_at")) or five_hour.get("clock") or "..."

            prefix = f"[{account_tag}] " if account_tag else ""
            if status_str == "relogin_required" and not data.get("five_hour", {}).get("resets_at"):
                label = f"{prefix}Re-login needed"
            elif age_str and status_str == "relogin_required":
                label = f"{prefix}{pct}% ({age_str})"
            elif reset_str != "...":
                label = f"{prefix}{pct}% ({reset_str})"
            else:
                label = f"{prefix}{pct}%"

            self._safe_set_label(label)
            pace_5h = compute_session_pace(pct, five_hour.get("resets_at"), fetched_at=self.last_fetch_completed)
            ahead_5h = " (ahead)" if pace_5h and pace_5h.ahead else ""
            st_note = " [Re-login needed]" if status_str == "relogin_required" else ""
            self.item_usage.set_label(f"Current session: {pct}%{ahead_5h}" + (f" (Resets {reset_str})" if reset_str != "..." else "") + st_note)

            # 2. All Models (Weekly)
            seven_day = data.get("seven_day", {})
            if seven_day:
                u7 = seven_day.get("utilization", 0)
                p7 = int(u7 * 100) if isinstance(u7, float) and u7 <= 1.0 else int(u7)
                r7 = self._format_time(seven_day.get("resets_at"), include_day=True) or seven_day.get("clock")
                pace_7d = compute_weekly_pace(p7, seven_day.get("resets_at"), fetched_at=self.last_fetch_completed)
                ahead_7d = " (ahead)" if pace_7d and pace_7d.ahead else ""
                self.item_usage_7d.set_label(f"All models (Weekly): {p7}%{ahead_7d}" + (f" ({r7})" if r7 else ""))

            # 3. Per-model usage
            model_rows = extract_model_limits(data, fetched_at=self.last_fetch_completed)
            active_model_keys = [row["key"] for row in model_rows]

            keys_to_remove = []
            for key, item in self.dynamic_model_items.items():
                if key not in active_model_keys:
                    self.menu.remove(item)
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                del self.dynamic_model_items[key]

            children = self.menu.get_children()
            idx_7d = children.index(self.item_usage_7d)

            for i, row in enumerate(model_rows):
                key = row["key"]
                r = self._format_time(row["resets_at"], include_day=True) or row.get("resets_at")
                pace_row = row.get("pace")
                ahead_row = " (ahead)" if pace_row and pace_row.ahead else ""
                label_text = f"{row['name']}: {row['percent']}%{ahead_row}" + (f" ({r})" if r else "")

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

            # 4. Routine Runs
            routines = data.get("routine_runs")
            if routines and isinstance(routines, dict):
                curr = routines.get("current", 0)
                lim = routines.get("limit", 15)
                self.item_routines.set_label(f"Daily routines: {curr}/{lim}")
                self.item_routines.show()
            else:
                self.item_routines.hide()

            self.item_reset.set_label(f"Resets at: {reset_str}")
            if age_str:
                self.item_time.set_label(f"Last Updated: {age_str}")
            else:
                self.item_time.set_label(f"Last Checked: {datetime.now().strftime('%H:%M')}")
        except Exception as e:
            print(f"DEBUG: UI update error: {e}")

    def _on_orgs_fetched(self, data, error):
        self.last_fetch_completed = time.time()
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
        self.last_fetch_completed = time.time()
        if error:
            print(f"DEBUG: Usage fetch error: {error}")
            if not self.cswap_accounts and not self.standalone_accounts:
                self._safe_set_label("Auth Error")
            return
        
        if not data:
            data = {}

        # Only render WebKit usage if neither cswap nor standalone is providing accounts
        if not self.cswap_accounts and not self.standalone_accounts:
            self._render_usage(data, account_tag=None)

def main():
    try:
        removed = remove_legacy_user_install()
        for path in removed:
            print(f"Removed stale user-level install artifact: {path}")
    except Exception as e:
        print(f"Legacy install cleanup failed: {e}")
    app = ClaudeTrackerApp()
    Gtk.main()

if __name__ == "__main__":
    main()
