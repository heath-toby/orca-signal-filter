#!/usr/bin/env bash
# Orca Signal Filter — Installer
set -euo pipefail

ADDON_NAME="signal_filter"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
ADDON_DIR="$ORCA_DIR/$ADDON_NAME"
CUSTOMIZATIONS="$ORCA_DIR/orca-customizations.py"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/$ADDON_NAME"
SCHEMA_FILE="org.gnome.Orca.SignalFilter.gschema.xml"
SCHEMA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/glib-2.0/schemas"

BEGIN_MARKER="# --- signal-filter begin ---"
END_MARKER="# --- signal-filter end ---"

info()  { echo "  [+] $*"; }
warn()  { echo "  [!] $*"; }
error() { echo "  [ERROR] $*" >&2; exit 1; }

echo ""
echo "=== Orca Signal Filter — Installer ==="
echo ""

# Pre-flight
if ! python3 -c "import orca" 2>/dev/null; then
    error "Orca screen reader not found. Please install Orca first."
fi
info "Orca found."

if [ ! -d "$SOURCE_DIR" ]; then
    error "Source directory '$SOURCE_DIR' not found."
fi

# Install add-on files
info "Installing add-on files to $ADDON_DIR..."
mkdir -p "$ADDON_DIR"
cp "$SOURCE_DIR"/__init__.py "$ADDON_DIR/"
cp "$SOURCE_DIR"/interceptor.py "$ADDON_DIR/"
cp "$SOURCE_DIR"/config.py "$ADDON_DIR/"
cp "$SOURCE_DIR"/config_ui.py "$ADDON_DIR/"
info "Python modules installed."

# Install GSettings schema
if [ -f "$SOURCE_DIR/$SCHEMA_FILE" ]; then
    info "Installing GSettings schema..."
    mkdir -p "$SCHEMA_DIR"
    cp "$SOURCE_DIR/$SCHEMA_FILE" "$SCHEMA_DIR/"
    if command -v glib-compile-schemas >/dev/null 2>&1; then
        glib-compile-schemas "$SCHEMA_DIR" 2>/dev/null && \
            info "GSettings schema compiled." || \
            warn "Could not compile GSettings schema."
    else
        warn "glib-compile-schemas not found."
    fi
else
    warn "GSettings schema file not found."
fi

# Set up orca-customizations.py
LOADER_BLOCK="${BEGIN_MARKER}
try:
    import sys as _sys, os as _os
    _orca_dir = _os.path.join(
        _os.environ.get(\"XDG_DATA_HOME\", _os.path.expanduser(\"~/.local/share\")),
        \"orca\"
    )
    if _orca_dir not in _sys.path:
        _sys.path.insert(0, _orca_dir)
    from signal_filter.interceptor import install as _signal_filter_install
    _signal_filter_install()
except Exception as _e:
    import logging as _logging
    _logging.getLogger(\"orca-signal-filter\").error(
        f\"Failed to load Signal Filter: {_e}\", exc_info=True
    )
${END_MARKER}"

# Create customizations file if needed
if [ ! -f "$CUSTOMIZATIONS" ]; then
    touch "$CUSTOMIZATIONS"
    info "Created $CUSTOMIZATIONS"
fi

# Remove any previous signal-filter block
if grep -q "$BEGIN_MARKER" "$CUSTOMIZATIONS" 2>/dev/null; then
    sed -i "/${BEGIN_MARKER//\//\\/}/,/${END_MARKER//\//\\/}/d" "$CUSTOMIZATIONS"
    info "Removed previous Signal Filter loader block."
fi

# Append the loader block
if [ -s "$CUSTOMIZATIONS" ] && grep -q '[^[:space:]]' "$CUSTOMIZATIONS" 2>/dev/null; then
    echo "" >> "$CUSTOMIZATIONS"
    echo "$LOADER_BLOCK" >> "$CUSTOMIZATIONS"
    info "Loader appended to existing orca-customizations.py."
else
    echo "$LOADER_BLOCK" > "$CUSTOMIZATIONS"
    info "Created orca-customizations.py with loader."
fi

echo ""
echo "=== Installation complete! ==="
echo ""
echo "  Restart Orca for changes to take effect:"
echo "    orca --replace &"
echo ""
echo "  Settings: press Orca+Shift+S at any time."
echo "  Uninstall: run ./uninstall.sh"
echo ""
