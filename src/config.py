import os
import json

CONFIG_DIR = os.path.expanduser("~/.config/claude-tracker")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

def ensure_config_dir():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)

def save_config(data):
    ensure_config_dir()
    # Merge with existing config if it exists
    current = load_config() or {}
    current.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(current, f)

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except:
        return None

def clear_config():
    if os.path.exists(CONFIG_FILE):
        os.remove(CONFIG_FILE)
