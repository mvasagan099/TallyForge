#!/bin/bash
# ==============================================================================
# Script to build native standalone macOS .app bundle for TallyForge
# TallyForge — Reverse Engineered. Rebuilt. Reimagined.
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR/dist/TallyForge.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

echo "============================================================"
echo " Building Standalone TallyForge.app for macOS..."
echo " TallyForge — Reverse Engineered. Rebuilt. Reimagined."
echo "============================================================"

# Remove previous bundle if exists
rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# Copy project files into Resources
echo "[*] Copying engine, static assets, and definitions..."
cp -R "$SCRIPT_DIR/engine" "$RESOURCES_DIR/"
cp -R "$SCRIPT_DIR/static" "$RESOURCES_DIR/"
cp -R "$SCRIPT_DIR/definitions" "$RESOURCES_DIR/"
cp "$SCRIPT_DIR/server.py" "$RESOURCES_DIR/"
cp "$SCRIPT_DIR/desktop_app.py" "$RESOURCES_DIR/"
if [ -f "$SCRIPT_DIR/tally_data.db" ]; then
    cp "$SCRIPT_DIR/tally_data.db" "$RESOURCES_DIR/"
fi

# Create Info.plist
echo "[*] Generating Contents/Info.plist..."
cat << 'EOF' > "$CONTENTS_DIR/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>TallyForge</string>
    <key>CFBundleDisplayName</key>
    <string>TallyForge — Reverse Engineered. Rebuilt. Reimagined.</string>
    <key>CFBundleExecutable</key>
    <string>TallyForge</string>
    <key>CFBundleIdentifier</key>
    <string>com.tallyforge.offline.enterprise</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

# Create executable launcher script
echo "[*] Generating Contents/MacOS/TallyForge launcher executable..."
cat << 'EOF' > "$MACOS_DIR/TallyForge"
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
cd "$DIR"

# Launch desktop app with local python3
exec python3 desktop_app.py
EOF

chmod +x "$MACOS_DIR/TallyForge"

# Also maintain TallyPrime.app symlink for backwards compatibility
rm -rf "$SCRIPT_DIR/dist/TallyPrime.app"
cp -R "$APP_DIR" "$SCRIPT_DIR/dist/TallyPrime.app"

echo "============================================================"
echo "✓ Standalone macOS Bundle Created Successfully:"
echo "  Location: $APP_DIR"
echo "  Launch with: open \"$APP_DIR\""
echo "============================================================"
