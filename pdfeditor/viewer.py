"""Interactive page viewer widget.

Renders the current page at a chosen zoom, and captures mouse input for the
active editing tool (highlight, ink, rectangle, text, note, redact). Screen
coordinates are converted back to PDF points before being handed to
:class:`pdfeditor.document.PdfDocument`.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Optional

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from .document import PdfDocument


class Tool(Enum):
    """The currently selected interaction tool."""

    HAND = auto()        # pan / no edit
    TEXT = auto()        # click to place text
    NOTE = auto()        # click to place a sticky note
    HIGHLIGHT = auto()   # drag a rectangle to highlight
    RECT = auto()        # drag a rectangle annotation
    INK = auto()         # freehand drawing
    REDACT = auto()      # drag a rectangle to redact
    IMAGE = auto()       # drag a rectangle to place an image


class PageView(QWidget):
    """Displays a single rendered PDF page and routes edits to the document."""

    # Emitted when the user completes an edit that changed the document.
    edited = Signal()
    # Emitted with a (page, pdf_x, pdf_y) request to place text/note/image.
    place_requested = Signal(int, float, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._doc: Optional[PdfDocument] = None
        self._page_index = 0
        self._zoom = 1.0
        self._pixmap: Optional[QPixmap] = None
        self.tool = Tool.HAND
        self.ink_color: tuple[float, float, float] = (1, 0, 0)

        # Transient interaction state (in widget pixels).
        self._drag_start: Optional[QPoint] = None
        self._drag_now: Optional[QPoint] = None
        self._ink_stroke: list[QPoint] = []

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- public API -----------------------------------------------------

    def set_document(self, doc: Optional[PdfDocument]) -> None:
        self._doc = doc
        self._page_index = 0
        self.refresh()

    def set_page(self, index: int) -> None:
        if self._doc and 0 <= index < self._doc.page_count:
            self._page_index = index
            self.refresh()

    @property
    def page_index(self) -> int:
        return self._page_index

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.1, min(zoom, 8.0))
        self.refresh()

    @property
    def zoom(self) -> float:
        return self._zoom

    def fit_width(self, available_width: int) -> None:
        if not self._doc:
            return
        w, _ = self._doc.page_size(self._page_index)
        if w > 0:
            self.set_zoom(available_width / w)

    def refresh(self) -> None:
        """Re-render the current page into a pixmap and repaint."""
        if not self._doc:
            self._pixmap = None
            self.update()
            return
        rp = self._doc.render_page(self._page_index, self._zoom)
        image = QImage(
            rp.samples, rp.width, rp.height, rp.stride, QImage.Format_RGBA8888
        )
        # Copy so the pixmap owns its buffer (samples is a transient bytes obj).
        self._pixmap = QPixmap.fromImage(image.copy())
        self.setMinimumSize(rp.width, rp.height)
        self.resize(rp.width, rp.height)
        self.update()

    # -- coordinate mapping --------------------------------------------

    def _to_pdf(self, pt: QPoint) -> tuple[float, float]:
        return (pt.x() / self._zoom, pt.y() / self._zoom)

    def _to_pdf_rect(self, a: QPoint, b: QPoint) -> tuple[float, float, float, float]:
        x0, y0 = self._to_pdf(a)
        x1, y1 = self._to_pdf(b)
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    # -- painting -------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(82, 86, 89))
        if not self._pixmap:
            return
        painter.drawPixmap(0, 0, self._pixmap)

        # Draw the in-progress selection / stroke overlay.
        if self._drag_start and self._drag_now:
            pen = QPen(QColor(0, 120, 215), 1, Qt.DashLine)
            painter.setPen(pen)
            if self.tool == Tool.INK and len(self._ink_stroke) > 1:
                for i in range(1, len(self._ink_stroke)):
                    painter.drawLine(self._ink_stroke[i - 1], self._ink_stroke[i])
            else:
                rect = QRectF(self._drag_start, self._drag_now).normalized()
                if self.tool == Tool.HIGHLIGHT:
                    painter.fillRect(rect, QColor(255, 235, 60, 90))
                painter.drawRect(rect)

    # -- mouse handling -------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._doc or event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()

        if self.tool in (Tool.TEXT, Tool.NOTE):
            x, y = self._to_pdf(pos)
            self.place_requested.emit(self._page_index, x, y)
            return

        self._drag_start = pos
        self._drag_now = pos
        if self.tool == Tool.INK:
            self._ink_stroke = [pos]
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_start is None:
            return
        pos = event.position().toPoint()
        self._drag_now = pos
        if self.tool == Tool.INK:
            self._ink_stroke.append(pos)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._doc or self._drag_start is None:
            return
        start = self._drag_start
        end = event.position().toPoint()
        self._drag_start = None
        self._drag_now = None
        stroke = self._ink_stroke
        self._ink_stroke = []

        try:
            self._apply_tool(start, end, stroke)
        finally:
            self.refresh()
            self.edited.emit()

    def _apply_tool(self, start: QPoint, end: QPoint, stroke: list[QPoint]) -> None:
        idx = self._page_index
        if self.tool == Tool.HIGHLIGHT:
            self._doc.add_highlight(idx, self._to_pdf_rect(start, end))
        elif self.tool == Tool.RECT:
            self._doc.add_rect_annot(idx, self._to_pdf_rect(start, end))
        elif self.tool == Tool.REDACT:
            self._doc.redact(idx, self._to_pdf_rect(start, end))
        elif self.tool == Tool.INK and len(stroke) > 1:
            pdf_stroke = [self._to_pdf(p) for p in stroke]
            self._doc.add_ink(idx, [pdf_stroke], color=self.ink_color)
        elif self.tool == Tool.IMAGE:
            # Image placement is handled by the window (needs a file dialog).
            x0, y0, x1, y1 = self._to_pdf_rect(start, end)
            self.place_requested.emit(idx, x0, y0)
