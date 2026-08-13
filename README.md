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

**Create & convert** (File menu)
- **Combine** multiple PDFs and images into one PDF
- Convert **images → PDF**
- **Reduce file size** (optimize / recompress)

**Editing & markup**
- Insert real, selectable **text**
- **Highlight**, **rectangle**, freehand **draw (ink)**, **sticky-note**, and **stamp** markup
- Insert and place **images**
- **Crop** pages (drag a box)

**Organize** (Page / File menus)
- Insert blank, delete, rotate, reorder, replace, extract, append pages
- **Split** a document by page count, by bookmarks, or by maximum file size

**Document tools** (Document menu)
- **Watermarks** (diagonal, semi-transparent text or image)
- **Headers / footers** with automatic `{page}` / `{pages}` numbering
- **Bates numbering** (e.g. `ABC000001`) across all pages
- **Bookmarks**, clickable **web links**, and file **attachments**
- **Extract all images** from a document

**Forms** (Forms menu)
- Add fillable **text fields**, auto-detect and **fill** existing fields
- **Flatten** to bake annotations and fields into the page

**Secure & redact** (Secure menu)
- **Password protection** with 256-bit AES + permissions
- True **redaction** and **search-and-redact** (permanent removal)
- **Sanitize**: strip hidden metadata, JavaScript, and embedded XML

**Compare**
- Compare two PDF versions and view a **text change report**

**Also**: edit document **metadata** (title, author, …); save (incremental) and Save As.

> **Roadmap** (not yet built): Office→PDF (needs LibreOffice), OCR / scan cleanup
> (needs Tesseract), PDF/A & preflight (needs Ghostscript), certificate signatures
> (needs pyHanko), and an optional AI Assistant (needs a Claude API key). Cloud-only
> Acrobat features — signature-request tracking, shared reviews, AEM rights management,
> Acrobat Studio / PDF Spaces — require a hosted backend and are out of scope for a
> local desktop app. See the feature map in the project notes.

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
