INSTALL_DIR = $(HOME)/.local/share/claude-tracker
BIN_DIR = $(HOME)/.local/bin
AUTOSTART_DIR = $(HOME)/.config/autostart
APPS_DIR = $(HOME)/.local/share/applications

.PHONY: install
install:
	@echo "Installing system dependencies..."
	@sudo apt-get install -y python3-gi gir1.2-ayatanaappindicator3-0.1 gir1.2-webkit2-4.1 python3-requests || (echo "Apt failed, trying to continue..." && true)
	@echo "Creating directories..."
	@mkdir -p $(INSTALL_DIR)
	@mkdir -p $(BIN_DIR)
	@mkdir -p $(AUTOSTART_DIR)
	@mkdir -p $(APPS_DIR)
	@echo "Copying files..."
	@cp -r src run.py $(INSTALL_DIR)/
	@echo "Creating executable..."
	@echo '#!/bin/bash\npython3 $(INSTALL_DIR)/run.py "$$@"' > $(BIN_DIR)/claude-tracker
	@chmod +x $(BIN_DIR)/claude-tracker
	@echo "Creating desktop entries..."
	@echo "[Desktop Entry]\nType=Application\nExec=$(BIN_DIR)/claude-tracker\nHidden=false\nNoDisplay=false\nX-GNOME-Autostart-enabled=true\nName=Claude Tracker\nComment=Track Claude Usage\nIcon=$(INSTALL_DIR)/src/assets/claude-tracker-icon.png" > $(AUTOSTART_DIR)/claude-tracker.desktop
	@echo "[Desktop Entry]\nType=Application\nExec=$(BIN_DIR)/claude-tracker\nName=Claude Tracker\nComment=Track Claude Usage\nIcon=$(INSTALL_DIR)/src/assets/claude-tracker-icon.png\nCategories=Utility;\nTerminal=false" > $(APPS_DIR)/claude-tracker.desktop
	@echo "Installation complete! Run 'claude-tracker' to start."

.PHONY: uninstall
uninstall:
	@rm -rf $(INSTALL_DIR)
	@rm -f $(BIN_DIR)/claude-tracker
	@rm -f $(AUTOSTART_DIR)/claude-tracker.desktop
	@rm -f $(APPS_DIR)/claude-tracker.desktop
	@echo "Uninstalled."

.PHONY: deb
deb:
	@bash scripts/build_deb.sh $(VERSION)
