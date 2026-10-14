#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo " Packaging Harness-Zero into Native macOS App Bundle (.app & .dmg)"
echo "=========================================================="

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DIST_DIR="$REPO_ROOT/dist"
APP_BUNDLE="$DIST_DIR/Harness-Zero.app"
DMG_PATH="$DIST_DIR/Harness-Zero.dmg"

mkdir -p "$DIST_DIR"

echo "==> Step 1: Compiling standalone Python engine with PyInstaller..."
PYINSTALLER_BIN=""
if command -v pyinstaller >/dev/null 2>&1; then
    PYINSTALLER_BIN="pyinstaller"
elif [ -x "$REPO_ROOT/.venv/bin/pyinstaller" ]; then
    PYINSTALLER_BIN="$REPO_ROOT/.venv/bin/pyinstaller"
else
    echo "Error: PyInstaller not found. Install via: pip install '.[package]'" >&2
    exit 1
fi

"$PYINSTALLER_BIN" \
    --name harness-engine \
    --onefile \
    --clean \
    --noconfirm \
    --add-data "harness/web:harness/web" \
    --add-data "builtin:builtin" \
    harness/engine_main.py

echo "==> Step 2: Compiling native macOS Swift WebKit launcher..."
swiftc \
    -framework Cocoa \
    -framework WebKit \
    -parse-as-library \
    -O \
    -o "$DIST_DIR/Harness-Zero-Launcher" \
    launcher/HarnessZeroApp.swift

echo "==> Step 3: Apple AppIcon.icns..."
if [ ! -f "$REPO_ROOT/packaging/macos/AppIcon.icns" ]; then
    swift scripts/generate_icon.swift
    iconutil -c icns AppIcon.iconset -o "$REPO_ROOT/packaging/macos/AppIcon.icns"
    rm -rf AppIcon.iconset
fi

echo "==> Step 4: Assembling macOS App Bundle..."
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

cp "$DIST_DIR/Harness-Zero-Launcher" "$APP_BUNDLE/Contents/MacOS/Harness-Zero"
cp "$DIST_DIR/harness-engine" "$APP_BUNDLE/Contents/Resources/harness-engine"
cp "$REPO_ROOT/packaging/macos/AppIcon.icns" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
chmod +x "$APP_BUNDLE/Contents/MacOS/Harness-Zero" "$APP_BUNDLE/Contents/Resources/harness-engine"

cat << 'EOF' > "$APP_BUNDLE/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleExecutable</key>
    <string>Harness-Zero</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.harnesszero.desktop</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Harness-Zero</string>
    <key>CFBundleDisplayName</key>
    <string>Harness-Zero</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
    <key>NSRequiresAquaSystemAppearance</key>
    <false/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsArbitraryLoads</key>
        <true/>
        <key>NSAllowsLocalNetworking</key>
        <true/>
    </dict>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright © 2026 Sovereign Agentic Systems. All rights reserved.</string>
</dict>
</plist>
EOF

echo "==> Step 5: Applying code signature..."
codesign --force --deep --sign - "$APP_BUNDLE"

echo "==> Step 6: Creating Drag-and-Drop .dmg Installer..."
DMG_STAGING="$DIST_DIR/dmg_staging"
rm -rf "$DMG_STAGING" "$DMG_PATH"
mkdir -p "$DMG_STAGING"
cp -R "$APP_BUNDLE" "$DMG_STAGING/"
ln -s /Applications "$DMG_STAGING/Applications"

hdiutil create \
    -volname "Harness-Zero" \
    -srcfolder "$DMG_STAGING" \
    -ov \
    -format UDZO \
    "$DMG_PATH"

rm -rf "$DMG_STAGING"

echo "=========================================================="
echo " SUCCESS! Output files created in $DIST_DIR:"
echo " 1. App Bundle: $APP_BUNDLE"
echo " 2. DMG Installer: $DMG_PATH"
echo "=========================================================="
