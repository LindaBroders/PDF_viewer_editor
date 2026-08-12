"""Main application window: menus, toolbar, thumbnails, and status bar."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QToolBar,
    QWidget,
)

from .document import DocumentError, PdfDocument
from .viewer import PageView, Tool

_PDF_FILTER = "PDF files (*.pdf);;All files (*)"


class MainWindow(QMainWindow):
    """Top-level window tying the document model to the viewer widget."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Viewer & Editor")
        self.resize(1200, 800)

        self._doc: Optional[PdfDocument] = None

        self._view = PageView()
        self._view.edited.connect(self._on_edited)
        self._view.place_requested.connect(self._on_place_requested)

        self._scroll = QScrollArea()
        self._scroll.setWidget(self._view)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setWidgetResizable(False)

        self._thumbs = QListWidget()
        self._thumbs.setFixedWidth(180)
        self._thumbs.setIconSize(QPixmap(140, 180).size())
        self._thumbs.currentRowChanged.connect(self._on_thumb_selected)

        central = QWidget()
        from PySide6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._thumbs)
        layout.addWidget(self._scroll, 1)
        self.setCentralWidget(central)

        self._page_label = QLabel("No document")
        self.statusBar().addPermanentWidget(self._page_label)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._update_enabled()

    # -- UI construction ------------------------------------------------

    def _build_actions(self) -> None:
        self.act_new = QAction("&New", self, shortcut=QKeySequence.New, triggered=self.new_document)
        self.act_open = QAction("&Open…", self, shortcut=QKeySequence.Open, triggered=self.open_document)
        self.act_save = QAction("&Save", self, shortcut=QKeySequence.Save, triggered=self.save_document)
        self.act_save_as = QAction("Save &As…", self, shortcut=QKeySequence.SaveAs, triggered=self.save_document_as)
        self.act_quit = QAction("&Quit", self, shortcut=QKeySequence.Quit, triggered=self.close)

        self.act_zoom_in = QAction("Zoom &In", self, shortcut=QKeySequence.ZoomIn, triggered=lambda: self._zoom_by(1.25))
        self.act_zoom_out = QAction("Zoom &Out", self, shortcut=QKeySequence.ZoomOut, triggered=lambda: self._zoom_by(0.8))
        self.act_fit_width = QAction("&Fit Width", self, triggered=self._fit_width)

        self.act_prev = QAction("&Previous Page", self, shortcut="PgUp", triggered=lambda: self._go_page(self._view.page_index - 1))
        self.act_next = QAction("&Next Page", self, shortcut="PgDown", triggered=lambda: self._go_page(self._view.page_index + 1))
        self.act_goto = QAction("&Go to Page…", self, shortcut="Ctrl+G", triggered=self._goto_page)

        self.act_insert_page = QAction("Insert &Blank Page", self, triggered=self._insert_blank_page)
        self.act_delete_page = QAction("&Delete Page", self, triggered=self._delete_page)
        self.act_rotate_cw = QAction("Rotate &Right", self, triggered=lambda: self._rotate(90))
        self.act_rotate_ccw = QAction("Rotate &Left", self, triggered=lambda: self._rotate(-90))
        self.act_append = QAction("&Append PDF…", self, triggered=self._append_pdf)
        self.act_extract = QAction("E&xtract Current Page…", self, triggered=self._extract_page)

        self.act_search = QAction("&Find…", self, shortcut=QKeySequence.Find, triggered=self._search)
        self.act_add_image = QAction("Insert &Image…", self, triggered=lambda: self._set_tool(Tool.IMAGE))
        self.act_ink_color = QAction("Drawing &Color…", self, triggered=self._pick_ink_color)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("&File")
        m_file.addActions([self.act_new, self.act_open, self.act_save, self.act_save_as])
        m_file.addSeparator()
        m_file.addActions([self.act_append, self.act_extract])
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_view = mb.addMenu("&View")
        m_view.addActions([self.act_zoom_in, self.act_zoom_out, self.act_fit_width])
        m_view.addSeparator()
        m_view.addActions([self.act_prev, self.act_next, self.act_goto])

        m_page = mb.addMenu("&Page")
        m_page.addActions([self.act_insert_page, self.act_delete_page])
        m_page.addSeparator()
        m_page.addActions([self.act_rotate_cw, self.act_rotate_ccw])

        m_edit = mb.addMenu("&Tools")
        m_edit.addActions([self.act_search, self.act_add_image, self.act_ink_color])

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addActions([self.act_open, self.act_save])
        tb.addSeparator()
        tb.addActions([self.act_zoom_out, self.act_fit_width, self.act_zoom_in])
        tb.addSeparator()
        tb.addActions([self.act_prev, self.act_next])
        tb.addSeparator()

        # Tool selector.
        self._tool_box = QComboBox()
        for label, tool in [
            ("Hand", Tool.HAND),
            ("Text", Tool.TEXT),
            ("Sticky Note", Tool.NOTE),
            ("Highlight", Tool.HIGHLIGHT),
            ("Rectangle", Tool.RECT),
            ("Draw", Tool.INK),
            ("Redact", Tool.REDACT),
        ]:
            self._tool_box.addItem(label, tool)
        self._tool_box.currentIndexChanged.connect(
            lambda i: self._set_tool(self._tool_box.itemData(i))
        )
        tb.addWidget(QLabel(" Tool: "))
        tb.addWidget(self._tool_box)
        tb.addSeparator()
        tb.addActions([self.act_rotate_ccw, self.act_rotate_cw, self.act_delete_page])
        tb.addAction(self.act_search)

    # -- document lifecycle --------------------------------------------

    def new_document(self) -> None:
        if not self._confirm_discard():
            return
        self._set_document(PdfDocument.new())

    def open_document(self, path: Optional[str] = None) -> None:
        if not self._confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", _PDF_FILTER)
        if not path:
            return
        try:
            doc = PdfDocument.open(path)
        except DocumentError as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        if doc.is_encrypted:
            pw, ok = QInputDialog.getText(self, "Password", "This PDF is encrypted:")
            if not ok or not doc.authenticate(pw):
                QMessageBox.warning(self, "Locked", "Could not unlock the document.")
                doc.close()
                return
        self._set_document(doc)

    def save_document(self) -> bool:
        if not self._doc:
            return False
        if not self._doc.path:
            return self.save_document_as()
        try:
            self._doc.save()
        except DocumentError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._update_title()
        self.statusBar().showMessage("Saved.", 3000)
        return True

    def save_document_as(self) -> bool:
        if not self._doc:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", "", _PDF_FILTER)
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            self._doc.save(path)
        except DocumentError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._update_title()
        self.statusBar().showMessage(f"Saved to {os.path.basename(path)}.", 3000)
        return True

    def _set_document(self, doc: PdfDocument) -> None:
        if self._doc:
            self._doc.close()
        self._doc = doc
        self._view.set_document(doc)
        self._view.fit_width(self._scroll.viewport().width())
        self._rebuild_thumbnails()
        self._update_enabled()
        self._update_title()
        self._update_page_label()

    # -- editing actions ------------------------------------------------

    def _rotate(self, degrees: int) -> None:
        if not self._doc:
            return
        self._doc.rotate_page(self._view.page_index, degrees)
        self._view.refresh()
        self._refresh_current_thumbnail()

    def _delete_page(self) -> None:
        if not self._doc:
            return
        try:
            self._doc.delete_page(self._view.page_index)
        except DocumentError as exc:
            QMessageBox.warning(self, "Delete page", str(exc))
            return
        new_index = min(self._view.page_index, self._doc.page_count - 1)
        self._view.set_page(new_index)
        self._rebuild_thumbnails()
        self._update_page_label()

    def _insert_blank_page(self) -> None:
        if not self._doc:
            return
        at = self._doc.insert_blank_page(self._view.page_index + 1)
        self._rebuild_thumbnails()
        self._go_page(at)

    def _append_pdf(self) -> None:
        if not self._doc:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Append PDF", "", _PDF_FILTER)
        if not path:
            return
        try:
            self._doc.append_pdf(path)
        except Exception as exc:
            QMessageBox.critical(self, "Append failed", str(exc))
            return
        self._rebuild_thumbnails()
        self._update_page_label()

    def _extract_page(self) -> None:
        if not self._doc:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Extract Page As", "", _PDF_FILTER)
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self._doc.extract_pages([self._view.page_index], path)
        self.statusBar().showMessage(f"Extracted page to {os.path.basename(path)}.", 3000)

    def _search(self) -> None:
        if not self._doc:
            return
        needle, ok = QInputDialog.getText(self, "Find", "Search text:")
        if not ok or not needle:
            return
        hits = self._doc.search(needle)
        if not hits:
            self.statusBar().showMessage("No matches.", 3000)
            return
        first = hits[0]
        self._go_page(first.page)
        self.statusBar().showMessage(
            f"{len(hits)} match(es); first on page {first.page + 1}.", 5000
        )

    def _pick_ink_color(self) -> None:
        color = QColorDialog.getColor(QColor(255, 0, 0), self, "Drawing color")
        if color.isValid():
            self._view.ink_color = (color.redF(), color.greenF(), color.blueF())

    def _on_place_requested(self, page: int, x: float, y: float) -> None:
        if not self._doc:
            return
        if self._view.tool == Tool.TEXT:
            text, ok = QInputDialog.getMultiLineText(self, "Insert Text", "Text:")
            if ok and text:
                self._doc.add_text(page, (x, y + 11), text)
        elif self._view.tool == Tool.NOTE:
            text, ok = QInputDialog.getMultiLineText(self, "Sticky Note", "Note:")
            if ok and text:
                self._doc.add_note(page, (x, y), text)
        elif self._view.tool == Tool.IMAGE:
            path, _ = QFileDialog.getOpenFileName(
                self, "Insert Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)"
            )
            if path:
                w, h = 200, 200
                self._doc.add_image(page, (x, y, x + w, y + h), path)
        self._view.refresh()
        self._on_edited()

    # -- navigation & view ---------------------------------------------

    def _go_page(self, index: int) -> None:
        if not self._doc:
            return
        index = max(0, min(index, self._doc.page_count - 1))
        self._view.set_page(index)
        self._thumbs.blockSignals(True)
        self._thumbs.setCurrentRow(index)
        self._thumbs.blockSignals(False)
        self._update_page_label()

    def _goto_page(self) -> None:
        if not self._doc:
            return
        n, ok = QInputDialog.getInt(
            self, "Go to Page", "Page number:", self._view.page_index + 1, 1, self._doc.page_count
        )
        if ok:
            self._go_page(n - 1)

    def _zoom_by(self, factor: float) -> None:
        self._view.set_zoom(self._view.zoom * factor)

    def _fit_width(self) -> None:
        self._view.fit_width(self._scroll.viewport().width())

    def _set_tool(self, tool: Tool) -> None:
        self._view.tool = tool

    def _on_thumb_selected(self, row: int) -> None:
        if row >= 0:
            self._go_page(row)

    def _on_edited(self) -> None:
        self._update_title()
        self._refresh_current_thumbnail()

    # -- thumbnails -----------------------------------------------------

    def _rebuild_thumbnails(self) -> None:
        self._thumbs.blockSignals(True)
        self._thumbs.clear()
        if self._doc:
            for i in range(self._doc.page_count):
                item = QListWidgetItem(self._thumbnail_icon(i), f"{i + 1}")
                item.setTextAlignment(Qt.AlignHCenter)
                self._thumbs.addItem(item)
            self._thumbs.setCurrentRow(self._view.page_index)
        self._thumbs.blockSignals(False)

    def _refresh_current_thumbnail(self) -> None:
        if not self._doc:
            return
        i = self._view.page_index
        item = self._thumbs.item(i)
        if item:
            item.setIcon(self._thumbnail_icon(i))

    def _thumbnail_icon(self, index: int) -> QIcon:
        w, _ = self._doc.page_size(index)
        zoom = 140 / w if w else 0.2
        rp = self._doc.render_page(index, zoom)
        img = QImage(rp.samples, rp.width, rp.height, rp.stride, QImage.Format_RGBA8888)
        return QIcon(QPixmap.fromImage(img.copy()))

    # -- window state ---------------------------------------------------

    def _update_enabled(self) -> None:
        has = self._doc is not None
        for act in (
            self.act_save, self.act_save_as, self.act_zoom_in, self.act_zoom_out,
            self.act_fit_width, self.act_prev, self.act_next, self.act_goto,
            self.act_insert_page, self.act_delete_page, self.act_rotate_cw,
            self.act_rotate_ccw, self.act_append, self.act_extract, self.act_search,
            self.act_add_image, self.act_ink_color,
        ):
            act.setEnabled(has)
        self._tool_box.setEnabled(has)

    def _update_title(self) -> None:
        name = "Untitled"
        if self._doc and self._doc.path:
            name = os.path.basename(self._doc.path)
        dirty = "*" if (self._doc and self._doc.dirty) else ""
        self.setWindowTitle(f"{dirty}{name} — PDF Viewer & Editor")

    def _update_page_label(self) -> None:
        if self._doc:
            self._page_label.setText(
                f"Page {self._view.page_index + 1} / {self._doc.page_count}"
            )
        else:
            self._page_label.setText("No document")

    # -- close handling -------------------------------------------------

    def _confirm_discard(self) -> bool:
        """Return True if it's safe to discard the current document."""
        if not self._doc or not self._doc.dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes before continuing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if choice == QMessageBox.Save:
            return self.save_document()
        return choice == QMessageBox.Discard

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._confirm_discard():
            if self._doc:
                self._doc.close()
            event.accept()
        else:
            event.ignore()
