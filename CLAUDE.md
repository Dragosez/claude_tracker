# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A native Linux (Ubuntu/GNOME) topbar indicator that shows Claude.ai usage limits. Python 3 + GTK3 via PyGObject, with an AyatanaAppIndicator3 tray icon and a WebKit2 window for claude.ai login. Dependencies are system packages (`python3-gi`, `gir1.2-ayatanaappindicator3-0.1`, `gir1.2-webkit2-4.1`, `python3-requests`), not pip.

## Commands

```bash
python3 run.py                                  # Run the app from source
python3 -m unittest discover -s tests -t .      # Run all tests
python3 -m unittest tests.test_usage_parsing    # Run a single test module
python3 -m py_compile src/main.py               # Quick syntax check
make install                                    # Install to ~/.local (asks sudo for apt deps)
make deb                                        # Build claude-tracker.deb (scripts/build_deb.sh)
```

The app refuses to run as root (breaks the WebKit sandbox and the tray icon). A GUI display is required; the installed copy usually already runs from `/opt/claude-tracker`, so running from source adds a second tray icon temporarily.

## Architecture

**Everything goes through WebKit, never plain HTTP.** claude.ai is Cloudflare-protected: a direct `requests`/`curl` call to `https://claude.ai/api/...` returns `403 forbidden` even with valid cookies. `src/auth.py` (`ClaudeSession`) hosts a hidden WebKit2 WebView logged into claude.ai; `fetch_json(url, callback)` injects a JS `fetch()` into the page and returns the JSON via a `script-message-received` handler. To inspect an API response during debugging, write a small GTK script that reuses the same cookie store and this fetch pattern (set `WEBKIT_DISABLE_COMPOSITING_MODE=1`; `Gtk.OffscreenWindow` crashes on GL context).

**State on disk:** cookies at `~/.config/claude-tracker/cookies.txt` (Mozilla text format, persisted by WebKit), config at `~/.config/claude-tracker/config.json` (holds `organization_uuid`).

**Data flow** (`src/main.py`, `ClaudeTrackerApp`): a 10-minute GLib timer → `refresh_data()` → `/api/organizations` (populates the Select Plan submenu) → `/api/organizations/{org}/usage` → `_on_usage_fetched()` updates the menu items and the panel label. The session becomes "ready" when the WebView lands on a non-login claude.ai URL.

**Usage API format** (this has changed over time — the source of past bugs):
- `five_hour` and `seven_day` top-level objects: `{utilization, resets_at}` — still present, used for the session/weekly rows.
- Per-model usage now lives in the `limits` array: entries with `kind: "weekly_scoped"` and `scope.model.display_name` (e.g. "Fable") carry `percent` and `resets_at`. This is what the claude.ai settings page renders.
- The legacy per-model keys (`seven_day_opus`, `seven_day_fable`, `iguana_necktie`, ...) are now `null` in modern responses. `src/usage.py:extract_model_limits()` prefers `limits` entries and only falls back to the legacy keys when no scoped entry exists. It is a pure function precisely so it can be unit-tested without GTK — keep parsing logic there, not in `main.py`.

**UI quirk:** `_ui_heartbeat` toggles a trailing space on the panel label every 15s because some GNOME shells drop indicator labels after suspend/panel restarts.

## Versioning & releases

- `VERSION` in `src/main.py` is the single source of truth.
- Pushing a tag `v*` triggers `.github/workflows/release.yml`: it builds the .deb (`make deb VERSION=<tag>`), commits a "Bump version to <tag> [skip ci]" to main, and publishes a GitHub release with `claude-tracker.deb`.
- Because CI pushes that bump commit, **always `git pull --rebase` before pushing** after a release.
- Tag as `v1.0.5`, not `v.1.0.5` — `_is_newer()` normalizes prefixes with a regex, but malformed tags caused a phantom "update available" bug before.
- The app self-updates: it polls the GitHub releases API at startup and every 24h, downloads the .deb, installs via `pkexec dpkg -i`, and restarts through `/usr/bin/claude-tracker`.

## Related project

`Copilot_tracker` (sibling project) shares the same indicator + GitHub-release auto-update architecture and served as the reference implementation for the update mechanism.
