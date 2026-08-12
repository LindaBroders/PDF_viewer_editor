#!/usr/bin/env bash
# Launch the PDF Viewer & Editor.
#
# Creates a local virtual environment on first run, installs dependencies,
# then starts the app. Pass a PDF path to open it directly:
#   ./run.sh mydoc.pdf
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
if [[ ! -d "$VENV" ]]; then
    echo "Creating virtual environment in $VENV ..."
    python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# Ensure dependencies are present. This also self-heals a previous install
# that was interrupted (e.g. a cancelled download): if the modules can't be
# imported, (re)install them. pip is a no-op when everything is already there.
if ! python -c "import PySide6.QtWidgets, fitz" >/dev/null 2>&1; then
    echo "Installing dependencies (first run may take a few minutes) ..."
    pip install --upgrade pip
    pip install -r requirements.txt
fi

exec python -m pdfeditor "$@"
