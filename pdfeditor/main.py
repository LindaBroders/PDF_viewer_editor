"""Application entry point."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QProxyStyle,
    QStyle,
)

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


class _MinimalStyle(QProxyStyle):
    """Wraps the platform style to drop the colorful icons on dialog buttons,
    so OK/Cancel etc. match the app's clean, flat look."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_DialogButtonBox_ButtonsHaveIcons:
            return 0
        return super().styleHint(hint, option, widget, returnData)


class _FixedDialogFilter(QObject):
    """Make small pop-up dialogs non-resizable, sized to their content.

    Excludes native file dialogs and any dialog that opts out by setting the
    ``resizable`` property (e.g. the scrollable text-report viewer)."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QDialog):
            if not isinstance(obj, QFileDialog) and not obj.property("resizable"):
                # Defer to the next tick: some dialogs (QInputDialog) finish
                # laying out their width just after Show, so locking now would
                # capture the wrong size.
                QTimer.singleShot(0, lambda o=obj: self._lock(o))
        return False

    @staticmethod
    def _lock(dialog) -> None:
        try:
            if dialog is None or not dialog.isVisible():
                return
            if hasattr(dialog, "setSizeGripEnabled"):
                dialog.setSizeGripEnabled(False)
            size = dialog.size()
            # Make sure the window is at least wide enough to show its title:
            # the title bar also needs room for the icon and the close/min/max
            # buttons, so add a generous allowance beyond the text width.
            title = dialog.windowTitle()
            if title:
                needed = dialog.fontMetrics().horizontalAdvance(title) + 160
                if size.width() < needed:
                    size.setWidth(needed)
            dialog.setFixedSize(size)
        except RuntimeError:
            pass  # dialog already closed


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
    app.setStyle(_MinimalStyle(app.style()))  # flat dialog buttons (no icons)
    app.setWindowIcon(_app_icon())
    app.setStyleSheet(DARK_QSS)

    # Keep a reference so the filter isn't garbage-collected.
    app._dialog_filter = _FixedDialogFilter()
    app.installEventFilter(app._dialog_filter)

    window = MainWindow()
    window.setWindowIcon(app.windowIcon())
    window.show()

    # Open a file passed on the command line, or start with a blank document.
    if len(argv) > 1:
        window.open_document(argv[1])
    else:
        window.new_document()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
