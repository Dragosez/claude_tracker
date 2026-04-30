import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, WebKit2, GLib
from .config import save_config
import json
import os

class ClaudeSession(Gtk.Window):
    def __init__(self, on_success_callback=None):
        super().__init__(title="Claude Session")
        self.set_default_size(1000, 1000)
        self.on_success_callback = on_success_callback
        self.is_ready = False

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("Claude Session")
        self.set_titlebar(header)

        ready_btn = Gtk.Button(label="Force Ready")
        ready_btn.connect("clicked", lambda _: self._mark_ready())
        header.pack_start(ready_btn)

        clear_btn = Gtk.Button(label="Clear Cache & Cookies")
        clear_btn.connect("clicked", lambda _: self._clear_cache_and_reload())
        header.pack_start(clear_btn)

        refresh_btn = Gtk.Button()
        refresh_btn.add(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        refresh_btn.connect("clicked", lambda _: self.webview.reload())
        header.pack_end(refresh_btn)

        # Content Manager for script injection and results
        self.content_manager = WebKit2.UserContentManager()
        self.content_manager.register_script_message_handler("json_callback")
        self.content_manager.connect("script-message-received::json_callback", self._on_json_received)
        self._setup_user_scripts()

        self.webview = WebKit2.WebView.new_with_user_content_manager(self.content_manager)
        self.started = False
        self.callbacks = {}
        self.callback_id = 0
        
        context = self.webview.get_context()
        cookie_manager = context.get_cookie_manager()
        storage_path = os.path.expanduser("~/.config/claude-tracker/cookies.txt")
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)
        cookie_manager.set_persistent_storage(storage_path, WebKit2.CookiePersistentStorage.TEXT)
        
        settings = self.webview.get_settings()
        # Stable Chrome on Linux UA
        settings.set_user_agent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        settings.set_enable_javascript(True)
        settings.set_enable_webgl(True)
        settings.set_enable_developer_extras(True)
        settings.set_enable_write_console_messages_to_stdout(True)
        settings.set_enable_site_specific_quirks(True)
        settings.set_enable_back_forward_navigation_gestures(True)
        settings.set_enable_smooth_scrolling(True)
        settings.set_enable_media_stream(True)
        settings.set_enable_mediasource(True)
        settings.set_enable_encrypted_media(True)
        settings.set_enable_media(True)
        settings.set_enable_webaudio(True)
        settings.set_enable_page_cache(True)
        settings.set_javascript_can_access_clipboard(True)
        settings.set_javascript_can_open_windows_automatically(True)
        settings.set_allow_universal_access_from_file_urls(True)
        settings.set_allow_file_access_from_file_urls(True)
        
        try:
            settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ALWAYS)
        except:
            pass
        
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.connect("notify::uri", self._on_uri_changed)

        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.webview)
        self.add(scrolled)
        self.connect("delete-event", self._on_delete_event)

    def _clear_cache_and_reload(self):
        print("DEBUG: Clearing session cache, cookies and reloading...")
        context = self.webview.get_context()
        context.clear_cache()
        cookie_manager = context.get_cookie_manager()
        cookie_manager.delete_all_cookies()
        storage_path = os.path.expanduser("~/.config/claude-tracker/cookies.txt")
        if os.path.exists(storage_path):
            try: os.remove(storage_path)
            except: pass
        self.webview.reload()

    def _on_delete_event(self, window, event):
        self.hide()
        return True 

    def _mark_ready(self):
        if not self.is_ready:
            print("DEBUG: Session marked as Ready. Starting data refresh...")
            self.is_ready = True
            self.hide()
            if self.on_success_callback:
                # Ensure it only runs once since refresh_data returns True
                GLib.idle_add(lambda: [self.on_success_callback(), False][1])

    def _on_json_received(self, manager, result):
        try:
            data = json.loads(result.get_js_value().to_string())
            cid = data.get("callback_id")
            payload = data.get("payload")
            error = data.get("error")
            
            if cid in self.callbacks:
                callback = self.callbacks.pop(cid)
                callback(payload, error)
        except Exception as e:
            print(f"DEBUG: Error processing JS callback: {e}")

    def _on_uri_changed(self, webview, pspec):
        uri = webview.get_uri()
        if not uri: return
        self._check_ready_state(uri)

    def _check_ready_state(self, uri):
        # Broaden detection for SPA transitions
        if any(term in uri for term in ["challenge", "chk_jschl", "/login"]):
            if self.get_visible():
                self.present()
            return

        # If we are on the main area and NOT on a login/challenge page, we are ready
        if uri.rstrip("/") in ["https://claude.ai", "https://claude.ai/chats"] or \
           any(path in uri for path in ["/chats/", "/new", "/settings"]):
            self._mark_ready()

    def _setup_user_scripts(self):
        script_content = """
        (function() {
            // 1. Trusted Types
            if (window.trustedTypes && !window.trustedTypes.defaultPolicy) {
                try {
                    window.trustedTypes.createPolicy('default', {
                        createHTML: (s) => s, createScript: (s) => s, createScriptURL: (s) => s
                    });
                } catch (e) {}
            }

            // 2. Normality Spoofing
            try {
                Object.defineProperty(navigator, 'webdriver', { get: () => false, configurable: true });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
                Object.defineProperty(navigator, 'platform', { get: () => 'Linux x86_64', configurable: true });
                Object.defineProperty(navigator, 'vendor', { get: () => 'Google Inc.', configurable: true });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8, configurable: true });
            } catch (e) {}

            // 3. Chrome Polyfill
            if (!window.chrome) {
                window.chrome = {
                    runtime: {},
                    app: {},
                    csi: function() {},
                    loadTimes: function() {}
                };
            }

            // 4. Fix for 'Blocked a frame' error
            const originalPostMessage = window.postMessage;
            window.postMessage = function(message, targetOrigin, transfer) {
                try {
                    return originalPostMessage.call(this, message, targetOrigin, transfer);
                } catch (e) {
                    if (targetOrigin !== '*') {
                        return originalPostMessage.call(this, message, '*', transfer);
                    }
                    throw e;
                }
            };
            
            window.addEventListener('error', function(e) {
                if (e.message && e.message.includes('Blocked a frame')) {
                    e.preventDefault();
                    e.stopPropagation();
                }
            }, true);
            
            if (window.opener) {
                try { window.opener = null; } catch(e) {}
            }
        })();
        """
        user_script = WebKit2.UserScript.new(
            script_content,
            WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserScriptInjectionTime.START,
            None, None
        )
        self.content_manager.add_script(user_script)

    def ensure_started(self):
        if not self.started:
            print("DEBUG: Initializing Claude session engine...")
            self.webview.load_uri("https://claude.ai/login")
            self.started = True

    def _on_load_changed(self, webview, event):
        if event == WebKit2.LoadEvent.FINISHED:
            uri = webview.get_uri()
            print(f"DEBUG: Page loaded: {uri}")
            self._check_ready_state(uri)

    def fetch_json(self, url, callback):
        if not self.is_ready: return
        self.ensure_started()
        
        cid = str(self.callback_id)
        self.callback_id += 1
        self.callbacks[cid] = callback

        script = f"""
        fetch("{url}", {{
            headers: {{ 
                "Accept": "application/json",
                "anthropic-client-platform": "web_claude_ai",
                "anthropic-client-version": "1.0.0"
            }}
        }})
        .then(r => r.json())
        .then(data => window.webkit.messageHandlers.json_callback.postMessage(JSON.stringify({{
            callback_id: "{cid}",
            payload: data
        }})))
        .catch(err => window.webkit.messageHandlers.json_callback.postMessage(JSON.stringify({{
            callback_id: "{cid}",
            error: err.toString()
        }})));
        """
        self.webview.run_javascript(script, None, None)

_session = None
def get_session(on_success=None):
    global _session
    if not _session: _session = ClaudeSession(on_success)
    return _session
