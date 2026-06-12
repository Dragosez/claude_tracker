#!/bin/bash
set -e

APP_NAME="claude-tracker"

# If a version argument is provided, use it and sync src/main.py to match
if [ ! -z "$1" ]; then
    VERSION=$(echo $1 | sed 's/^[^0-9]*//')
    sed -i "s/^VERSION\s*=\s*\".*\"/VERSION = \"$VERSION\"/" src/main.py
else
    # Otherwise read the version from src/main.py (single source of truth)
    VERSION=$(grep -E "^VERSION\s*=" src/main.py | head -n 1 | cut -d '"' -f 2)
fi

BUILD_DIR="build/deb"
STAGED_DIR="$BUILD_DIR/$APP_NAME-$VERSION"

echo "Creating build directory..."
rm -rf $BUILD_DIR
mkdir -p $STAGED_DIR/DEBIAN
mkdir -p $STAGED_DIR/opt/$APP_NAME
mkdir -p $STAGED_DIR/usr/bin
mkdir -p $STAGED_DIR/usr/share/applications
mkdir -p $STAGED_DIR/etc/xdg/autostart

# Copy source files
cp -r src run.py $STAGED_DIR/opt/$APP_NAME/

# Create launcher script
cat <<EOF > $STAGED_DIR/usr/bin/$APP_NAME
#!/bin/bash
/usr/bin/python3 /opt/$APP_NAME/run.py "\$@"
EOF
chmod +x $STAGED_DIR/usr/bin/$APP_NAME

# Create Desktop Entry
cat <<EOF > $STAGED_DIR/usr/share/applications/$APP_NAME.desktop
[Desktop Entry]
Type=Application
Exec=/usr/bin/$APP_NAME
Name=Claude Tracker
Comment=Track Claude.ai usage
Icon=/opt/$APP_NAME/src/assets/claude-tracker-icon.png
Categories=Utility;
Terminal=false
EOF

# Create Autostart Entry
cp $STAGED_DIR/usr/share/applications/$APP_NAME.desktop $STAGED_DIR/etc/xdg/autostart/

# Create Control file
cat <<EOF > $STAGED_DIR/DEBIAN/control
Package: $APP_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: Dragos <dragos2992@yahoo.com>
Depends: python3, python3-gi, gir1.2-ayatanaappindicator3-0.1, gir1.2-webkit2-4.1 | gir1.2-webkit2-4.0, python3-requests
Description: Native Linux topbar indicator for Claude.ai usage.
 Supports wide horizontal text in Ubuntu/GNOME.
EOF

echo "Building .deb package..."
dpkg-deb --build $STAGED_DIR

echo "Package created: $STAGED_DIR.deb"
mv $STAGED_DIR.deb ./$APP_NAME.deb
