#!/usr/bin/env bash
# ADVANCED / OPTIONAL: build a single, self-contained clickable executable
# using PyInstaller. Most people don't need this — the Desktop icon created by
# install-desktop.sh already gives you click-to-launch. Use this only if you
# want one portable file you can copy to another machine.
#
#   ./packaging/build-executable.sh
#
# Output: dist/pdf-editor  (a single executable you can double-click)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# Use the app's virtualenv if it exists, else create a build one.
if [[ -d .venv ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    python3 -m venv .buildvenv
    # shellcheck disable=SC1091
    source .buildvenv/bin/activate
    pip install -r requirements.txt
fi

pip install --upgrade pyinstaller

# Bundle everything into one file. --windowed = no terminal window.
pyinstaller \
    --noconfirm \
    --onefile \
    --windowed \
    --name pdf-editor \
    --collect-all PySide6 \
    --collect-all pymupdf \
    -m pdfeditor

echo
echo "Built: $PROJECT_DIR/dist/pdf-editor"
echo "Double-click it, or run ./dist/pdf-editor from a terminal."
echo "Note: the file is large (~150 MB) because it bundles Qt and the PDF engine."
