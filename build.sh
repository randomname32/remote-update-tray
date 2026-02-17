#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

cp -a "$SCRIPT_DIR/debian" "$BUILD_DIR/"
cp "$SCRIPT_DIR/update_tray.py" "$BUILD_DIR/"
cp "$SCRIPT_DIR/remote-update-tray" "$BUILD_DIR/"
cp "$SCRIPT_DIR/remote-update-tray.desktop" "$BUILD_DIR/"
cp "$SCRIPT_DIR/Makefile" "$BUILD_DIR/"

cd "$BUILD_DIR"
dpkg-buildpackage -us -uc -b

mkdir -p "$BUILD_DIR/deb"
mv "$SCRIPT_DIR"/remote-update-tray_*.deb "$SCRIPT_DIR"/remote-update-tray_*.buildinfo "$SCRIPT_DIR"/remote-update-tray_*.changes "$BUILD_DIR/deb/"

echo ""
echo "Package built successfully:"
ls "$BUILD_DIR/deb/"*.deb
