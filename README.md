# PDF Viewer & Editor

A native **Linux** PDF viewer and editor — an Adobe-Acrobat-style desktop app
for Fedora and other distributions. Built with **PySide6 (Qt 6)** for the UI and
**PyMuPDF** for the PDF engine.

![status](https://img.shields.io/badge/platform-Linux-blue) ![python](https://img.shields.io/badge/python-3.9%2B-green)

## Features

**Viewing**
- Open any PDF (and common images / XPS, auto-converted to PDF)
- Continuous zoom, fit-to-width, page-by-page navigation
- Thumbnail sidebar with live previews
- Full-text search across the document
- Password-protected (encrypted) PDF support

**Editing**
- Insert real, selectable **text**
- **Highlight**, **rectangle**, freehand **draw (ink)**, and **sticky-note** annotations
- Insert **images**
- True **redaction** (permanently removes content, not just a black box)
- **Page management**: insert blank, delete, rotate, reorder, extract, append another PDF
- Edit document **metadata** (title, author, …)
- Save (incremental) and Save As

## Quick start (Fedora)

Install the system dependencies and run:

```bash
# 1. Install Python + the Qt runtime libraries Fedora needs for Qt apps
sudo dnf install -y python3 python3-pip python3-virtualenv \
    mesa-libGL libxkbcommon xcb-util-cursor

# 2. Clone and launch (the script creates a venv and installs deps on first run)
git clone https://github.com/lindabroders/pdf_viewer_editor.git
cd pdf_viewer_editor
./run.sh                 # or:  ./run.sh /path/to/document.pdf
```

That's it — `run.sh` builds a local virtual environment, installs `PySide6` and
`PyMuPDF` from PyPI, and starts the app.

### Manual install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pdfeditor            # optionally pass a PDF path
```

### Install as a desktop application

To get an application-menu entry and make it the default PDF handler:

```bash
pip install --user .
mkdir -p ~/.local/share/applications
cp packaging/pdf-editor.desktop ~/.local/share/applications/
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

## Usage

- **Open / Save**: toolbar buttons or `Ctrl+O` / `Ctrl+S` (`Ctrl+Shift+S` for Save As).
- **Zoom**: `Ctrl++` / `Ctrl+-`, or **Fit Width**.
- **Navigate**: `PgUp` / `PgDown`, `Ctrl+G` to jump to a page, or click a thumbnail.
- **Find**: `Ctrl+F`.
- **Edit tools**: pick a tool from the toolbar dropdown, then:
  - *Text / Sticky Note* — click where you want it, type in the dialog.
  - *Highlight / Rectangle / Redact* — drag a box.
  - *Draw* — click-drag to sketch (set the color under **Tools ▸ Drawing Color**).
- **Pages**: use the **Page** menu to insert, delete, or rotate; **File ▸ Append PDF**
  to merge another document; **File ▸ Extract Current Page** to split one out.

## Project layout

```
pdfeditor/
  document.py     PDF engine (PyMuPDF) — GUI-free, fully unit-tested
  viewer.py       Interactive page-rendering widget + editing tools
  main_window.py  Menus, toolbar, thumbnails, status bar
  main.py         Application entry point
tests/            Headless tests for the engine (run in CI, no display needed)
packaging/        .desktop launcher for the app menu
```

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest            # runs the headless engine tests
```

The `document.py` engine is intentionally free of any Qt imports, so its tests
run without a display server (useful for CI). GUI code lives in `viewer.py` and
`main_window.py`.

## License

MIT
