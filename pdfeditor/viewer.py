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

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

from .document import PdfDocument


class Tool(Enum):
    """The currently selected interaction tool."""

    HAND = auto()        # pan / scroll, no edit
    SELECT = auto()      # drag to select text (copy with Ctrl+C)
    TEXT = auto()        # click to place text
    NOTE = auto()        # click to place a sticky note
    HIGHLIGHT = auto()   # drag a rectangle to highlight
    RECT = auto()        # drag a rectangle annotation
    INK = auto()         # freehand drawing
    REDACT = auto()      # drag a rectangle to redact
    IMAGE = auto()       # drag a rectangle to place an image / signature
    CROP = auto()        # drag a rectangle to crop the page
    LINK = auto()        # drag a rectangle to add a hyperlink
    SNAPSHOT = auto()    # drag a rectangle to copy that area as an image


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
    # Emitted just before a drag-based edit is applied (for undo snapshots).
    edit_started = Signal()
    # Emitted after a snapshot (Copy Area as Image) completes.
    area_copied = Signal()
    # Emitted when a signature/image placement is committed:
    # (page_index, x0, y0, x1, y1) in PDF points.
    signature_placed = Signal(int, float, float, float, float)

    GAP = 18  # pixels of grey between stacked pages

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._doc: Optional[PdfDocument] = None
        self._zoom = 1.0
        self.tool = Tool.SELECT  # most PDFs have selectable text
        self.ink_color: tuple[float, float, float] = (1, 0, 0)

        # Text selection state.
        self._sel_page = -1
        self._sel_rects: list[tuple[float, float, float, float]] = []  # PDF coords
        self._sel_text = ""
        self._layout_cache: dict = {}  # page index -> (flat_words, lines)

        # Per-page layout + cached render: list of dicts
        #   {index, x, y, w, h, pixmap}
        self._pages: list[dict] = []
        self._last_current = -1

        # Transient interaction state (widget pixels).
        self._drag_start: Optional[QPoint] = None
        self._drag_now: Optional[QPoint] = None
        self._ink_stroke: list[QPoint] = []
        self._active: Optional[dict] = None  # page a drag started on

        # Interactive image/signature placement.
        self._place_pix: Optional[QPixmap] = None
        self._place_rect: Optional[QRect] = None      # widget pixels
        self._place_following = False                 # rides the cursor until dropped
        self._place_page: Optional[dict] = None
        self._place_mode: Optional[str] = None        # None | "move" | "resize"
        self._place_corner = -1
        self._place_grab: Optional[QPoint] = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- public API -----------------------------------------------------

    def set_document(self, doc: Optional[PdfDocument]) -> None:
        self._doc = doc
        self._last_current = -1
        self.clear_selection()
        self.rebuild()
        if doc:
            self.page_changed.emit(0)

    # -- text selection -------------------------------------------------

    def clear_selection(self) -> None:
        self._sel_page = -1
        self._sel_rects = []
        self._sel_text = ""

    def has_selection(self) -> bool:
        return bool(self._sel_text)

    def selection(self) -> tuple[int, list]:
        """Return (page_index, [word rects in PDF coords]) for the selection."""
        return self._sel_page, list(self._sel_rects)

    def selected_text(self) -> str:
        return self._sel_text

    def point_to_page(self, pos: QPoint):
        """Map a widget point to (page_index, pdf_x, pdf_y), or None if off-page."""
        page = self._hit_test(pos)
        if page is None:
            return None
        x, y = self._to_pdf_on(page, pos)
        return page["index"], x, y

    # -- interactive image / signature placement ------------------------

    HANDLE = 9  # corner handle size (px)

    def begin_placement(self, pixmap: QPixmap) -> None:
        """Start placing an image: it follows the cursor until clicked."""
        if pixmap.isNull():
            return
        self._place_pix = pixmap
        w = 200
        h = max(20, round(pixmap.height() * w / max(1, pixmap.width())))
        region = self.visibleRegion().boundingRect()
        cx = region.center().x() if not region.isEmpty() else self.width() // 2
        cy = region.center().y() if not region.isEmpty() else self.height() // 2
        self._place_rect = QRect(cx - w // 2, cy - h // 2, w, h)
        self._place_following = True
        self._place_mode = None
        self._place_page = None
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.update()

    def _placing(self) -> bool:
        return self._place_pix is not None and self._place_rect is not None

    def _place_handles(self) -> list:
        r = self._place_rect
        s = self.HANDLE
        return [
            QRect(r.left() - s // 2, r.top() - s // 2, s, s),
            QRect(r.right() - s // 2, r.top() - s // 2, s, s),
            QRect(r.right() - s // 2, r.bottom() - s // 2, s, s),
            QRect(r.left() - s // 2, r.bottom() - s // 2, s, s),
        ]

    def _corner_at(self, pt: QPoint) -> int:
        for i, h in enumerate(self._place_handles()):
            if h.adjusted(-3, -3, 3, 3).contains(pt):
                return i
        return -1

    def _resize_placement(self, pt: QPoint) -> None:
        r = self._place_rect
        corners = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]
        anchor = corners[(self._place_corner + 2) % 4]  # opposite corner stays put
        ar = self._place_pix.width() / max(1, self._place_pix.height())
        w = max(20, abs(pt.x() - anchor.x()))
        h = max(20, abs(pt.y() - anchor.y()))
        # Keep the signature's aspect ratio.
        if w / h > ar:
            w = int(h * ar)
        else:
            h = int(w / ar)
        sx = 1 if pt.x() >= anchor.x() else -1
        sy = 1 if pt.y() >= anchor.y() else -1
        self._place_rect = QRect(
            anchor, QPoint(anchor.x() + sx * w, anchor.y() + sy * h)
        ).normalized()

    def _commit_placement(self) -> None:
        if not self._placing():
            return
        r = self._place_rect
        page = self._place_page or self._hit_test(r.center())
        if page is None:
            # Fall back to the page nearest the box center vertically.
            cy = r.center().y()
            page = min(self._pages, key=lambda p: abs((p["y"] + p["h"] / 2) - cy)) if self._pages else None
        pix = self._place_pix
        self._cancel_placement()
        if page is None:
            return
        x0 = (r.left() - page["x"]) / self._zoom
        y0 = (r.top() - page["y"]) / self._zoom
        x1 = (r.right() - page["x"]) / self._zoom
        y1 = (r.bottom() - page["y"]) / self._zoom
        self.signature_placed.emit(page["index"], x0, y0, x1, y1)

    def _cancel_placement(self) -> None:
        self._place_pix = None
        self._place_rect = None
        self._place_following = False
        self._place_mode = None
        self._place_page = None
        self.setCursor(Qt.IBeamCursor if self.tool == Tool.SELECT else Qt.ArrowCursor)
        self.update()

    def copy_selection(self) -> bool:
        """Copy the selected text to the clipboard. Returns True if any."""
        if self._sel_text:
            QApplication.clipboard().setText(self._sel_text)
            return True
        return False

    def _page_layout(self, index: int) -> Optional[dict]:
        for pg in self._pages:
            if pg["index"] == index:
                return pg
        return None

    def _word_layout(self, idx: int):
        """Return (flat_words, lines) for a page, grouped into reading lines.

        ``lines`` are ordered top-to-bottom, each with words left-to-right and a
        ``start`` global index into ``flat_words``. Cached per page.
        """
        if idx in self._layout_cache:
            return self._layout_cache[idx]
        words = self._doc.get_words(idx)
        by_line: dict = {}
        for w in words:
            by_line.setdefault((w[5], w[6]), []).append(w)
        lines = []
        for ws in by_line.values():
            ws = sorted(ws, key=lambda w: w[0])
            lines.append({"y0": min(w[1] for w in ws),
                          "y1": max(w[3] for w in ws), "words": ws})
        lines.sort(key=lambda L: L["y0"])
        flat: list = []
        for L in lines:
            L["start"] = len(flat)
            flat.extend(L["words"])
        self._layout_cache[idx] = (flat, lines)
        return flat, lines

    def _anchor_index(self, lines: list, px: float, py: float) -> int:
        """Global word index nearest a PDF point (snaps to the point's line)."""
        line = next((L for L in lines if L["y0"] - 2 <= py <= L["y1"] + 2), None)
        if line is None:
            line = min(lines, key=lambda L: abs((L["y0"] + L["y1"]) / 2 - py))
        ws = line["words"]
        for j, w in enumerate(ws):
            if w[0] - 1 <= px <= w[2] + 1:
                return line["start"] + j
        if px <= ws[0][0]:
            return line["start"]
        if px >= ws[-1][2]:
            return line["start"] + len(ws) - 1
        j = min(range(len(ws)), key=lambda j: abs((ws[j][0] + ws[j][2]) / 2 - px))
        return line["start"] + j

    def _apply_range(self, idx: int, flat: list, gi: int, gj: int) -> None:
        lo, hi = sorted((gi, gj))
        chosen = flat[lo:hi + 1]
        self._sel_page = idx
        self._sel_rects = [(w[0], w[1], w[2], w[3]) for w in chosen]
        parts: list[str] = []
        prev_line = None
        for w in chosen:
            line = (w[5], w[6])
            if prev_line is not None:
                parts.append("\n" if line != prev_line else " ")
            parts.append(w[4])
            prev_line = line
        self._sel_text = "".join(parts)

    def _update_selection(self, page: dict, start: QPoint, end: QPoint) -> None:
        """Select whole words between two points (line-aware, word granularity)."""
        idx = page["index"]
        flat, lines = self._word_layout(idx)
        if not flat:
            self.clear_selection()
            return
        sx, sy = self._to_pdf_on(page, start)
        ex, ey = self._to_pdf_on(page, end)
        self._apply_range(idx, flat, self._anchor_index(lines, sx, sy),
                          self._anchor_index(lines, ex, ey))

    def _select_word(self, page: dict, pos: QPoint) -> None:
        """Select the single word under a point (for double-click)."""
        idx = page["index"]
        flat, lines = self._word_layout(idx)
        if not flat:
            self.clear_selection()
            return
        px, py = self._to_pdf_on(page, pos)
        gi = self._anchor_index(lines, px, py)
        self._apply_range(idx, flat, gi, gi)

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
        self._layout_cache.clear()  # page text layout may have changed
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

        # Text-selection highlight (blue over selected words).
        if self._sel_rects:
            pg = self._page_layout(self._sel_page)
            if pg:
                sel_color = QColor(51, 153, 255, 80)
                for x0, y0, x1, y1 in self._sel_rects:
                    painter.fillRect(
                        QRectF(
                            pg["x"] + x0 * self._zoom,
                            pg["y"] + y0 * self._zoom,
                            (x1 - x0) * self._zoom,
                            (y1 - y0) * self._zoom,
                        ),
                        sel_color,
                    )

        # In-progress marquee / ink overlay (not for pan/select tools).
        if self._drag_start and self._drag_now and self.tool not in (Tool.HAND, Tool.SELECT):
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

        # Image/signature placement overlay.
        if self._placing():
            painter.setOpacity(0.65 if self._place_following else 1.0)
            painter.drawPixmap(self._place_rect, self._place_pix)
            painter.setOpacity(1.0)
            if not self._place_following:
                painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self._place_rect)
                for h in self._place_handles():
                    painter.fillRect(h, QColor(0, 120, 215))

    # -- mouse handling -------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._doc or event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()

        # Placement mode intercepts all mouse handling.
        if self._placing():
            if self._place_following:
                self._place_following = False   # drop it here
                self._place_page = self._hit_test(self._place_rect.center())
                self.update()
                return
            corner = self._corner_at(pos)
            if corner >= 0:
                self._place_mode = "resize"
                self._place_corner = corner
            elif self._place_rect.contains(pos):
                self._place_mode = "move"
                self._place_grab = pos - self._place_rect.topLeft()
            else:
                self._commit_placement()   # click outside = place it for good
            return

        page = self._hit_test(pos)
        if page is None:
            return
        self._active = page

        if self.tool == Tool.HAND:
            self._active = None  # let the scroll area handle panning
            return
        if self.tool == Tool.SELECT:
            self.clear_selection()
            self._drag_start = pos
            self._drag_now = pos
            self.update()
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

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._placing():
            self._commit_placement()
            return
        if not self._doc or event.button() != Qt.LeftButton or self.tool != Tool.SELECT:
            super().mouseDoubleClickEvent(event)
            return
        pos = event.position().toPoint()
        page = self._hit_test(pos)
        if page is None:
            return
        self._active = page
        self._select_word(page, pos)
        # Keep the drag anchored at the double-clicked word so a subsequent drag
        # extends the selection word by word.
        self._drag_start = pos
        self._drag_now = pos
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._placing():
            pos = event.position().toPoint()
            if self._place_following:
                self._place_rect.moveCenter(pos)
                self._place_page = self._hit_test(pos)
                self.update()
            elif self._place_mode == "move":
                self._place_rect.moveTopLeft(pos - self._place_grab)
                self.update()
            elif self._place_mode == "resize":
                self._resize_placement(pos)
                self.update()
            return
        if self._drag_start is None:
            return
        pos = event.position().toPoint()
        self._drag_now = pos
        if self.tool == Tool.INK:
            self._ink_stroke.append(pos)
        elif self.tool == Tool.SELECT and self._active is not None:
            self._update_selection(self._active, self._drag_start, pos)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._placing():
            self._place_mode = None
            self._place_grab = None
            return
        if not self._doc or self._drag_start is None or self._active is None:
            return
        start = self._drag_start
        end = event.position().toPoint()
        self._drag_start = None
        self._drag_now = None
        stroke = self._ink_stroke
        self._ink_stroke = []

        if self.tool == Tool.SELECT:
            moved = abs(start.x() - end.x()) >= 3 or abs(start.y() - end.y()) >= 3
            if moved:
                # A drag: recompute the word range from anchor to release point.
                self._update_selection(self._active, start, end)
            # A plain click with no drag keeps whatever is selected (empty after
            # a single click's press-clear, or the word from a double-click).
            self.update()
            return

        if self.tool == Tool.SNAPSHOT:
            self._copy_area(start, end)
            self._drag_now = None
            self.update()
            self.area_copied.emit()
            return

        self.edit_started.emit()  # snapshot pre-edit state for undo
        try:
            self._apply_tool(start, end, stroke)
        finally:
            self.refresh_active_page()
            self.edited.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self._placing():
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._commit_placement()
                return
            if event.key() == Qt.Key_Escape:
                self._cancel_placement()
                return
        if event.matches(QKeySequence.Copy) and self.copy_selection():
            return
        if event.key() == Qt.Key_Escape and self._sel_rects:
            self.clear_selection()
            self.update()
            return
        super().keyPressEvent(event)

    def _copy_area(self, start: QPoint, end: QPoint) -> None:
        """Copy the dragged rectangle of the rendered page to the clipboard."""
        rect = QRect(start, end).normalized()
        if rect.width() < 4 or rect.height() < 4:
            return
        QApplication.clipboard().setPixmap(self.grab(rect))

    def _words_between(self, page: dict, start: QPoint, end: QPoint) -> list:
        """Word rectangles between two points (empty if the page has no text)."""
        idx = page["index"]
        flat, lines = self._word_layout(idx)
        if not flat:
            return []
        sx, sy = self._to_pdf_on(page, start)
        ex, ey = self._to_pdf_on(page, end)
        gi = self._anchor_index(lines, sx, sy)
        gj = self._anchor_index(lines, ex, ey)
        lo, hi = sorted((gi, gj))
        return [(w[0], w[1], w[2], w[3]) for w in flat[lo:hi + 1]]

    def _apply_tool(self, start: QPoint, end: QPoint, stroke: list[QPoint]) -> None:
        page = self._active
        idx = page["index"]
        if self.tool == Tool.HIGHLIGHT:
            # Word-aware: snap to words on a text page; rectangle on a scan.
            rects = self._words_between(page, start, end)
            if rects:
                for r in rects:
                    self._doc.add_highlight(idx, r)
            else:
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
