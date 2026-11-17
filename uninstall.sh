#!/usr/bin/env bash
# Signal Filter -- Uninstaller
#
# Removes the extension package, revokes its Orca approval, and clears
# the loader block left behind by pre-extension versions of the
# installer. Nothing else in orca-customizations.py is touched.
#
# Settings and the debug log are kept, so a reinstall picks up where you
# left off. Pass --purge to remove them.

set -euo pipefail

ADDON_NAME="signal_filter"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
ADDON_DIR="$ORCA_DIR/extensions/$ADDON_NAME"
DATA_DIR="$ORCA_DIR/$ADDON_NAME"
CUSTOMIZATIONS="$ORCA_DIR/orca-customizations.py"

BEGIN_MARKER="# --- signal-filter begin ---"
END_MARKER="# --- signal-filter end ---"

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

info() { echo "  [+] $*"; }

echo ""
echo "=== Signal Filter -- Uninstaller ==="
echo ""

# Revoke first: once the directory is gone Orca can no longer identify it.
if orca --revoke-extension "$ADDON_NAME" >/dev/null 2>&1; then
    info "Revoked Orca's approval of '$ADDON_NAME'."
else
    info "No Orca approval to revoke (already removed)."
fi

if [ -d "$ADDON_DIR" ]; then
    rm -rf "$ADDON_DIR"
    info "Removed $ADDON_DIR"
else
    info "No installed extension at $ADDON_DIR (already removed)."
fi

if [ -f "$CUSTOMIZATIONS" ] && grep -qF "$BEGIN_MARKER" "$CUSTOMIZATIONS" 2>/dev/null; then
    sed -i "/${BEGIN_MARKER//\//\\/}/,/${END_MARKER//\//\\/}/d" "$CUSTOMIZATIONS"
    info "Removed the legacy Signal Filter block from orca-customizations.py."
fi

for legacy in "$ORCA_DIR/signal_filter_v51" "$ORCA_DIR/signal_filter_v50"; do
    [ -d "$legacy" ] && info "A pre-extension install remains at $legacy; remove it by hand if unwanted."
done

if [ "$PURGE" -eq 1 ]; then
    if [ -d "$DATA_DIR" ]; then
        rm -rf "$DATA_DIR"
        info "Removed the debug log directory."
    fi
    dconf reset -f /org/gnome/orca/default/extensions/signal_filter/ 2>/dev/null \
        && info "Removed the extension settings." \
        || info "No extension settings to remove."
    dconf reset -f /org/gnome/orca/signal-filter/ 2>/dev/null \
        && info "Removed the legacy org.gnome.Orca.SignalFilter settings." \
        || info "No legacy settings to remove."
else
    info "Settings and debug log kept. Re-run with --purge to remove them."
fi

echo ""
echo "=== Uninstall complete ==="
echo ""
echo "  Restart Orca for the change to take effect:"
echo "    orca --replace &"
echo ""
