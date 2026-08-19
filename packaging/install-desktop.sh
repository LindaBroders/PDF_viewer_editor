#!/usr/bin/env bash
# Install the app so you can launch it by CLICKING — both from the applications
# menu and (optionally) a double-clickable icon on your Desktop.
#
# It generates the .desktop launcher with the correct absolute paths to *this*
# checkout, so clicking runs the same run.sh you use in the terminal.
#
#   ./packaging/install-desktop.sh              # menu entry + Desktop icon
#   ./packaging/install-desktop.sh --no-desktop # menu entry only
#   ./packaging/install-desktop.sh --icon 3     # use icon option 3 (see packaging/icons)
#   ./packaging/install-desktop.sh --uninstall  # remove both
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS_DIR/pdf-viewer-editor.desktop"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
SHORTCUT="$DESKTOP_DIR/pdf-viewer-editor.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$DESKTOP_FILE" "$SHORTCUT"
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    echo "Removed the menu entry and Desktop shortcut."
    exit 0
fi

# Parse options.
MAKE_DESKTOP=1
ICON="$PROJECT_DIR/packaging/icon.svg"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-desktop) MAKE_DESKTOP=0 ;;
        --icon)
            shift
            # Accept a number (1-6) or a path.
            if [[ "${1:-}" =~ ^[1-6]$ ]]; then
                match=$(ls "$PROJECT_DIR"/packaging/icons/0"$1"-*.svg 2>/dev/null | head -1 || true)
                [[ -n "$match" ]] && ICON="$match"
            elif [[ -f "${1:-}" ]]; then
                ICON="$1"
            fi
            ;;
    esac
    shift || true
done

mkdir -p "$APPS_DIR"

write_launcher() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Name=PDF Viewer & Editor
GenericName=PDF Editor
Comment=View and edit PDF documents
Exec=$PROJECT_DIR/run.sh %F
Icon=$ICON
Terminal=false
Categories=Office;Viewer;Graphics;
MimeType=application/pdf;
Keywords=PDF;editor;viewer;annotate;
StartupWMClass=pdf-viewer-editor
EOF
}

chmod +x "$PROJECT_DIR/run.sh" 2>/dev/null || true

# 1) Applications-menu entry.
write_launcher "$DESKTOP_FILE"
update-desktop-database "$APPS_DIR" 2>/dev/null || true
echo "✓ Added to the applications menu (search 'PDF')."

# 2) Double-clickable Desktop icon (GNOME needs it marked executable + trusted).
if [[ "$MAKE_DESKTOP" == "1" ]]; then
    mkdir -p "$DESKTOP_DIR"
    write_launcher "$SHORTCUT"
    chmod +x "$SHORTCUT"
    gio set "$SHORTCUT" "metadata::trusted" true 2>/dev/null || true
    echo "✓ Added a double-clickable icon to your Desktop: $SHORTCUT"
    echo "  (If it looks like a text file, right-click it → 'Allow Launching'.)"
fi

echo
echo "Icon in use: $(basename "$ICON")"
echo "Make it your default PDF opener: right-click a PDF in Files →"
echo "Open With → Set as default → 'PDF Viewer & Editor'."
