"""A modern, minimal dark-grey theme (Qt style sheet).

Medium-to-dark grey surfaces, flat rounded controls, soft blue accent. Applied
application-wide from :func:`pdfeditor.main.main`.
"""

from __future__ import annotations

import os

# Path to the drop-down chevron asset (forward slashes for the Qt style sheet).
_CHEVRON = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "packaging", "icons", "chevron-down.png")
).replace("\\", "/")

# Palette
BG = "#2b2f34"          # window background (medium-dark grey)
SURFACE = "#33383f"     # panels, menus, inputs
SURFACE_ALT = "#3b414a"  # hover / raised
BORDER = "#454b54"
TEXT = "#e6e8eb"
MUTED = "#a5abb3"
ACCENT = "#5b9bd5"      # soft blue
ACCENT_TEXT = "#ffffff"
PAGE_BG = "#404751"     # canvas behind the PDF page

DARK_QSS = f"""
* {{
    color: {TEXT};
    font-size: 13px;
}}
QMainWindow, QDialog, QWidget {{
    background-color: {BG};
}}

/* Menu bar */
QMenuBar {{
    background-color: {BG};
    padding: 2px;
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item {{
    background: transparent;
    padding: 6px 12px;
    border-radius: 6px;
}}
QMenuBar::item:selected {{ background-color: {SURFACE_ALT}; }}

QMenu {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px;
    border-radius: 6px;
}}
QMenu::item:selected {{ background-color: {ACCENT}; color: {ACCENT_TEXT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 8px; }}

/* Toolbar + tool buttons (flat, rounded, minimal) */
QToolBar {{
    background-color: {BG};
    border: none;
    padding: 6px;
    spacing: 4px;
}}
QToolButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 7px 12px;
    color: {TEXT};
}}
QToolButton:hover {{ background-color: {SURFACE_ALT}; }}
QToolButton:pressed {{ background-color: {ACCENT}; color: {ACCENT_TEXT}; }}
QToolButton:disabled {{ color: {MUTED}; }}
/* Split-button (Save ▾) dropdown section */
QToolButton::menu-button {{
    border: none;
    background: transparent;
    width: 16px;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QToolButton::menu-arrow {{ image: url("{_CHEVRON}"); width: 9px; height: 9px; }}

/* Push buttons (dialogs) */
QPushButton {{
    background-color: {SURFACE_ALT};
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    color: {TEXT};
}}
QPushButton:hover {{ background-color: {ACCENT}; color: {ACCENT_TEXT}; }}
QPushButton:pressed {{ background-color: #4a86bd; color: {ACCENT_TEXT}; }}
QPushButton:disabled {{ background-color: {SURFACE}; color: {MUTED}; }}
QPushButton:default {{ background-color: {ACCENT}; color: {ACCENT_TEXT}; }}

/* Combo boxes */
QComboBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 32px 6px 12px;   /* extra right padding for the arrow */
    min-width: 100px;
}}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 26px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url("{_CHEVRON}");
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_TEXT};
    padding: 4px;
}}

/* Text inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
}}

/* Thumbnail list */
QListWidget {{
    background-color: {SURFACE};
    border: none;
    border-right: 1px solid {BORDER};
    padding: 6px;
    outline: 0;
}}
QListWidget::item {{
    border-radius: 8px;
    padding: 4px;
    margin: 3px;
    color: {MUTED};
}}
QListWidget::item:selected {{ background-color: {SURFACE_ALT}; color: {TEXT}; }}

/* Canvas area behind the page */
QScrollArea {{ border: none; background-color: {PAGE_BG}; }}
QAbstractScrollArea > QWidget > QWidget {{ background-color: {PAGE_BG}; }}

/* Status bar */
QStatusBar {{
    background-color: {BG};
    border-top: 1px solid {BORDER};
    color: {MUTED};
}}
QStatusBar QLabel {{ color: {MUTED}; }}

/* Scrollbars — slim and minimal */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* Tooltips */
QToolTip {{
    background-color: {SURFACE};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
}}
"""
