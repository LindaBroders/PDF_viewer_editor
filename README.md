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

**Convert & scan** (needs one extra tool each — see below)
- **Office → PDF** (Word, Excel, PowerPoint, ODF, …) via LibreOffice
- **OCR — make scans searchable**, with deskew, clean, and auto-rotate, via OCRmyPDF/Tesseract
- **Convert to PDF/A** (archival) and run a **print preflight** check

**Sign** (Secure menu)
- Create a **self-signed certificate** and apply a **digital signature** (optional RFC 3161 timestamp) via pyHanko

**AI Assistant** (AI menu, needs a Claude API key)
- **Summarize** the document and **ask questions** about its content

**Also**: edit document **metadata** (title, author, …); save (incremental) and Save As.

### Optional feature dependencies

The app runs fully without these; each feature shows the exact install command if
its tool is missing.

```bash
# System tools (Fedora):
sudo dnf install libreoffice        # Office → PDF
sudo dnf install ocrmypdf tesseract # OCR / scan cleanup
sudo dnf install ghostscript        # PDF/A conversion

# Python packages (signing + AI):
pip install -r requirements-optional.txt
export ANTHROPIC_API_KEY=sk-...     # for the AI Assistant
```

> **Out of scope** — these are cloud services or Adobe-proprietary and can't run as a
> local desktop app: signature-request tracking / reminders / bulk-send, shared review
> links, @mentions, web-form response collection (all need a hosted backend), and Adobe
> Experience Manager rights management / Acrobat Studio / PDF Spaces.

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

### Launch by clicking (no terminal)

Run the installer once — it adds the app to your applications menu **and** puts a
double-clickable icon on your Desktop:

```bash
./packaging/install-desktop.sh              # menu entry + Desktop icon
./packaging/install-desktop.sh --icon 3     # pick icon option 3 (see below)
./packaging/install-desktop.sh --no-desktop # menu entry only
./packaging/install-desktop.sh --uninstall  # remove both
```

Then press the **Super** key, type “PDF”, and click **PDF Viewer & Editor** — or
double-click the icon on your Desktop. (First launch may take a moment while the
virtualenv builds; after that it opens instantly.)

To make it your default PDF opener: right-click any PDF in **Files → Open With →
Set as default → PDF Viewer & Editor**.

**App icons.** Six ready-made designs live in `packaging/icons/` (as SVG and PNG).
Pass `--icon <1-6>` to the installer to choose one, or point `--icon` at your own
SVG/PNG file.

**Single portable executable (advanced, optional).** If you want one standalone
file you can copy to another machine, `./packaging/build-executable.sh` bundles the
whole app (Qt + PDF engine) into `dist/pdf-editor` via PyInstaller. It's large
(~150 MB); most people should just use the Desktop icon above.

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
