"""Continuous-scroll page viewer widget.

Renders every page stacked vertically so the user can scroll through the whole
document with the mouse wheel or scrollbar. Editing tools (highlight, ink,
rectangle, text, note, redact, crop, link, image/signature) operate on whichever
page a drag starts on; widget coordinates are converted back to that page's PDF
points before being handed to :class:`pdfeditor.document.PdfDocument`.
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

    HAND = auto()        # pan / scroll, no edit
    TEXT = auto()        # click to place text
    NOTE = auto()        # click to place a sticky note
    HIGHLIGHT = auto()   # drag a rectangle to highlight
    RECT = auto()        # drag a rectangle annotation
    INK = auto()         # freehand drawing
    REDACT = auto()      # drag a rectangle to redact
    IMAGE = auto()       # drag a rectangle to place an image / signature
    CROP = auto()        # drag a rectangle to crop the page
    LINK = auto()        # drag a rectangle to add a hyperlink


class PageView(QWidget):
    """Displays all PDF pages in a scrollable column and routes edits."""

    # Emitted when the user completes an edit that changed the document.
    edited = Signal()
    # Emitted with a (page, pdf_x, pdf_y) request to place text/note.
    place_requested = Signal(int, float, float)
    # Emitted with a (page, x0, y0, x1, y1) rectangle for tools that need the
    # window to gather more input (crop, link, image placement).
    rect_selected = Signal(int, float, float, float, float)
    # Emitted when the visible page changes (from scrolling or navigation).
    page_changed = Signal(int)

    GAP = 18  # pixels of grey between stacked pages

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._doc: Optional[PdfDocument] = None
        self._zoom = 1.0
        self.tool = Tool.HAND
        self.ink_color: tuple[float, float, float] = (1, 0, 0)

        # Per-page layout + cached render: list of dicts
        #   {index, x, y, w, h, pixmap}
        self._pages: list[dict] = []
        self._last_current = -1

        # Transient interaction state (widget pixels).
        self._drag_start: Optional[QPoint] = None
        self._drag_now: Optional[QPoint] = None
        self._ink_stroke: list[QPoint] = []
        self._active: Optional[dict] = None  # page a drag started on

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- public API -----------------------------------------------------

    def set_document(self, doc: Optional[PdfDocument]) -> None:
        self._doc = doc
        self._last_current = -1
        self.rebuild()
        if doc:
            self.page_changed.emit(0)

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.1, min(zoom, 8.0))
        self.rebuild()

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def page_index(self) -> int:
        """The page currently centered in the viewport."""
        return self.current_page()

    def fit_width(self, available_width: int) -> None:
        if not self._doc:
            return
        w, _ = self._doc.page_size(self.current_page())
        if w > 0:
            # Leave room for the page gap/margins.
            self.set_zoom((available_width - 2 * self.GAP) / w)

    def page_top(self, index: int) -> int:
        """Return the widget y-offset of a page's top (for scrolling to it)."""
        if 0 <= index < len(self._pages):
            return max(0, self._pages[index]["y"] - self.GAP)
        return 0

    def current_page(self) -> int:
        """Determine which page is centered in the visible viewport."""
        if not self._pages:
            return 0
        region = self.visibleRegion().boundingRect()
        center_y = region.center().y() if not region.isEmpty() else 0
        best = 0
        for pg in self._pages:
            if pg["y"] <= center_y <= pg["y"] + pg["h"]:
                return pg["index"]
            if pg["y"] <= center_y:
                best = pg["index"]
        return best

    def notify_scrolled(self) -> None:
        """Called by the window when the scroll position changes."""
        cur = self.current_page()
        if cur != self._last_current:
            self._last_current = cur
            self.page_changed.emit(cur)

    # -- rendering ------------------------------------------------------

    def rebuild(self) -> None:
        """Render every page at the current zoom and lay them out vertically."""
        self._pages = []
        if not self._doc:
            self.setMinimumSize(0, 0)
            self.resize(0, 0)
            self.update()
            return

        pixmaps = [self._render(i) for i in range(self._doc.page_count)]
        max_w = max((p.width() for p in pixmaps), default=0)
        total_w = max_w + 2 * self.GAP

        y = self.GAP
        for i, pix in enumerate(pixmaps):
            x = (total_w - pix.width()) // 2
            self._pages.append(
                {"index": i, "x": x, "y": y, "w": pix.width(), "h": pix.height(), "pixmap": pix}
            )
            y += pix.height() + self.GAP

        self.setMinimumSize(total_w, y)
        self.resize(total_w, y)
        self.update()

    def refresh(self) -> None:
        """Full re-render (used after structural changes / zoom)."""
        self.rebuild()

    def refresh_active_page(self) -> None:
        """Re-render just the page a drag acted on (fast path for edits)."""
        if self._active is None:
            self.rebuild()
            return
        idx = self._active["index"]
        if 0 <= idx < len(self._pages):
            self._pages[idx]["pixmap"] = self._render(idx)
            self.update()

    def _render(self, index: int) -> QPixmap:
        rp = self._doc.render_page(index, self._zoom)
        image = QImage(rp.samples, rp.width, rp.height, rp.stride, QImage.Format_RGBA8888)
        return QPixmap.fromImage(image.copy())

    # -- coordinate mapping --------------------------------------------

    def _hit_test(self, pt: QPoint) -> Optional[dict]:
        """Return the page dict at a widget point, or None."""
        for pg in self._pages:
            if pg["x"] <= pt.x() <= pg["x"] + pg["w"] and pg["y"] <= pt.y() <= pg["y"] + pg["h"]:
                return pg
        return None

    def _to_pdf_on(self, page: dict, pt: QPoint) -> tuple[float, float]:
        return ((pt.x() - page["x"]) / self._zoom, (pt.y() - page["y"]) / self._zoom)

    def _to_pdf_rect_on(self, page: dict, a: QPoint, b: QPoint) -> tuple[float, float, float, float]:
        x0, y0 = self._to_pdf_on(page, a)
        x1, y1 = self._to_pdf_on(page, b)
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    # -- painting -------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(64, 71, 81))  # matches theme PAGE_BG
        for pg in self._pages:
            x, y, w, h = pg["x"], pg["y"], pg["w"], pg["h"]
            painter.fillRect(x + 3, y + 4, w, h, QColor(0, 0, 0, 60))   # drop shadow
            painter.fillRect(x, y, w, h, QColor(255, 255, 255))          # paper
            painter.drawPixmap(x, y, pg["pixmap"])

        # In-progress selection / stroke overlay (widget coordinates).
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
        page = self._hit_test(pos)
        if page is None:
            return
        self._active = page

        if self.tool == Tool.HAND:
            self._active = None  # let the scroll area handle panning
            return
        if self.tool in (Tool.TEXT, Tool.NOTE):
            x, y = self._to_pdf_on(page, pos)
            self.place_requested.emit(page["index"], x, y)
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
        if not self._doc or self._drag_start is None or self._active is None:
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
            self.refresh_active_page()
            self.edited.emit()

    def _apply_tool(self, start: QPoint, end: QPoint, stroke: list[QPoint]) -> None:
        page = self._active
        idx = page["index"]
        if self.tool == Tool.HIGHLIGHT:
            self._doc.add_highlight(idx, self._to_pdf_rect_on(page, start, end))
        elif self.tool == Tool.RECT:
            self._doc.add_rect_annot(idx, self._to_pdf_rect_on(page, start, end))
        elif self.tool == Tool.REDACT:
            self._doc.redact(idx, self._to_pdf_rect_on(page, start, end))
        elif self.tool == Tool.INK and len(stroke) > 1:
            pdf_stroke = [self._to_pdf_on(page, p) for p in stroke]
            self._doc.add_ink(idx, [pdf_stroke], color=self.ink_color)
        elif self.tool in (Tool.IMAGE, Tool.CROP, Tool.LINK):
            x0, y0, x1, y1 = self._to_pdf_rect_on(page, start, end)
            self.rect_selected.emit(idx, x0, y0, x1, y1)
