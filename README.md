# Claude Tracker (Native Linux Edition)

A native Linux topbar indicator to track your Claude.ai message usage and limits.

Inspired by the Copilot Tracker, this tool displays your 5-hour rolling window usage directly in the Ubuntu/GNOME topbar.

## Features
- **Native Topbar Label:** Displays `Usage % | Reset Time` directly in the panel.
- **Lightweight:** Uses Python and GTK.
- **Native Login:** Uses a native WebKit2 window for Claude.ai authentication.
- **Autostart:** Automatically starts when you log into your desktop.

## Installation

### Build from Source
1. Clone this repository (or enter the directory).
2. Run the installer:
   ```bash
   make install
   ```
   *Note: This will ask for your sudo password to install necessary system libraries (`python3-gi`, `webkit2`, `appindicator`).*

3. Launch it:
   ```bash
   claude-tracker
   ```

## Usage
1. Click the indicator and select **Login / Change Account**.
2. Log in to your Claude.ai account in the window that appears.
3. Once logged in, the window will close, and your usage will start appearing in the top bar.

## Uninstallation
To remove the app:
```bash
make uninstall
```
