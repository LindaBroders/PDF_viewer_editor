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
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
    pip install --upgrade pip
    pip install -r requirements.txt
else
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
fi

exec python -m pdfeditor "$@"
