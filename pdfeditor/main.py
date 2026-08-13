"""Application entry point."""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def _app_icon() -> QIcon:
    """Load the application icon shipped in packaging/ (SVG preferred)."""
    base = os.path.join(os.path.dirname(__file__), "..", "packaging")
    for name in ("icon.svg", "icon.png"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            return QIcon(path)
    return QIcon()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("PDF Viewer & Editor")
    app.setOrganizationName("pdfeditor")
    app.setWindowIcon(_app_icon())

    window = MainWindow()
    window.setWindowIcon(app.windowIcon())
    window.show()

    # Open a file passed on the command line, if any.
    if len(argv) > 1:
        window.open_document(argv[1])

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
