#!/usr/bin/env bash
# Install a desktop-menu entry so the app appears in your Fedora application
# menu and can be set as the default PDF handler.
#
# This generates the .desktop file with the correct absolute paths to *this*
# checkout, so launching from the menu runs the same run.sh you use in the
# terminal. Run it once:
#
#   ./packaging/install-desktop.sh
#
# Undo with:  ./packaging/install-desktop.sh --uninstall
set -euo pipefail

# Absolute path to the project root (the parent of this script's directory).
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS_DIR/pdf-viewer-editor.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$DESKTOP_FILE"
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    echo "Removed $DESKTOP_FILE"
    exit 0
fi

mkdir -p "$APPS_DIR"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=PDF Viewer & Editor
GenericName=PDF Editor
Comment=View and edit PDF documents
Exec=$PROJECT_DIR/run.sh %f
Icon=$PROJECT_DIR/packaging/icon.svg
Terminal=false
Categories=Office;Viewer;Graphics;
MimeType=application/pdf;
Keywords=PDF;editor;viewer;annotate;
EOF

chmod +x "$PROJECT_DIR/run.sh" 2>/dev/null || true
update-desktop-database "$APPS_DIR" 2>/dev/null || true

echo "Installed menu entry: $DESKTOP_FILE"
echo "It launches: $PROJECT_DIR/run.sh"
echo
echo "Look for 'PDF Viewer & Editor' in your applications menu."
echo "To make it your default PDF opener: right-click a PDF in Files ->"
echo "Open With -> Set as default -> choose 'PDF Viewer & Editor'."
