"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("PDF Viewer & Editor")
    app.setOrganizationName("pdfeditor")

    window = MainWindow()
    window.show()

    # Open a file passed on the command line, if any.
    if len(argv) > 1:
        window.open_document(argv[1])

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
