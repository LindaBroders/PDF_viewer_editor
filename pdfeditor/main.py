"""Application entry point."""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import DARK_QSS


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

    # Set the app identity BEFORE constructing QApplication, so Qt registers the
    # right app ID with the desktop portal the first time (setting it afterwards
    # triggers a harmless "Connection already associated with an application ID"
    # warning on Wayland). Must match the installed pdf-viewer-editor.desktop
    # basename and its StartupWMClass so the taskbar shows our name and icon.
    QGuiApplication.setDesktopFileName("pdf-viewer-editor")
    QApplication.setApplicationName("PDF Viewer & Editor")
    # Force an EMPTY display name. Otherwise Qt falls back to the application
    # name and appends it to every dialog's title bar (e.g.
    # "Save signature — PDF Viewer & Editor"), making short dialogs too wide.
    # The taskbar name still comes from the .desktop launcher (desktopFileName).
    QApplication.setApplicationDisplayName("")
    QApplication.setOrganizationName("pdfeditor")

    app = QApplication(argv)
    app.setWindowIcon(_app_icon())
    app.setStyleSheet(DARK_QSS)

    window = MainWindow()
    window.setWindowIcon(app.windowIcon())
    window.show()

    # Open a file passed on the command line, if any.
    if len(argv) > 1:
        window.open_document(argv[1])

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
