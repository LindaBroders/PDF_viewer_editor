"""Application entry point."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QProxyStyle,
    QStyle,
)

from .main_window import MainWindow
from .theme import DARK_QSS

# Named pipe/socket used to keep the app to a single instance, so a second
# launch (e.g. double-clicking another PDF) opens a new tab in the running
# window instead of starting a whole new window.
_IPC_NAME = "pdf-viewer-editor.single-instance"


def _forward_to_running_instance(files: list[str]) -> bool:
    """If another instance is already running, hand it our files. Returns
    True when the message was delivered (so this process should exit)."""
    socket = QLocalSocket()
    socket.connectToServer(_IPC_NAME)
    if not socket.waitForConnected(250):
        socket.abort()
        return False
    payload = ("OPEN\n" + "\n".join(files)).encode("utf-8")
    socket.write(payload)
    socket.flush()
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        socket.waitForDisconnected(500)
    return True


def _target_window(default: MainWindow) -> MainWindow:
    """Pick a live window to receive forwarded files (prefer the active one)."""
    active = QApplication.activeWindow()
    if isinstance(active, MainWindow):
        return active
    if default is not None and default.isVisible():
        return default
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MainWindow) and widget.isVisible():
            return widget
    win = MainWindow()
    win.show()
    return win


def _start_ipc_server(app: QApplication, window: MainWindow) -> None:
    """Listen for future launches and open the files they forward."""
    QLocalServer.removeServer(_IPC_NAME)  # clear a stale socket, if any
    server = QLocalServer()
    if not server.listen(_IPC_NAME):
        return

    def handle_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return
        if conn.waitForReadyRead(1000):
            text = bytes(conn.readAll().data()).decode("utf-8", "ignore")
            lines = text.split("\n")
            paths = [ln for ln in lines[1:] if ln.strip()] if lines and lines[0] == "OPEN" else []
            _target_window(window).open_files_and_raise(paths)
        conn.disconnectFromServer()

    server.newConnection.connect(handle_connection)
    app._ipc_server = server  # keep a reference alive


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
    files = [os.path.abspath(a) for a in argv[1:] if not a.startswith("-")]

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

    # Single instance: if the app is already running, forward our files to it
    # (they open as new tabs there) and exit instead of opening a new window.
    if _forward_to_running_instance(files):
        return 0

    app.setStyle(_MinimalStyle(app.style()))  # flat dialog buttons (no icons)
    app.setWindowIcon(_app_icon())
    app.setStyleSheet(DARK_QSS)

    # Keep a reference so the filter isn't garbage-collected.
    app._dialog_filter = _FixedDialogFilter()
    app.installEventFilter(app._dialog_filter)

    window = MainWindow()
    window.setWindowIcon(app.windowIcon())
    window.show()

    # Listen for future launches so their files open here as tabs.
    _start_ipc_server(app, window)

    # Open files passed on the command line, or start with a blank document.
    if files:
        for path in files:
            window.open_document(path)
    else:
        window.new_document()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
