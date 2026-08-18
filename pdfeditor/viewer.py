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
    QFont,
    QFontMetrics,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QWidget

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
    # Emitted whenever the editable object layer changes (add/move/resize/delete).
    objects_changed = Signal()
    # Emitted (object index) when a text/note object is double-clicked to edit.
    object_edit_requested = Signal(int)

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
        self._pan: Optional[dict] = None     # active hand-tool pan gesture

        # New image/signature riding the cursor until it is dropped.
        self._place_pix: Optional[QPixmap] = None
        self._place_path: Optional[str] = None
        self._place_rect: Optional[QRect] = None      # widget pixels
        self._place_following = False
        self._place_page: Optional[dict] = None

        # Persistent, editable placed objects. Every inserted item lives here so
        # it can be selected, moved, resized, edited and deleted, then flattened
        # into the PDF on save. Each object is a dict with:
        #   kind:  "image" | "text" | "note"
        #   page:  page index
        #   rect:  [x0, y0, x1, y1] in PDF points
        #   pixmap: on-canvas preview
        #   image -> path
        #   text  -> text, size, color, base_w, base_h
        #   note  -> text, base_w, base_h
        self._objects: list[dict] = []
        self._sel_obj = -1
        self._obj_mode: Optional[str] = None          # None | "move" | "resize"
        self._obj_corner = -1
        self._obj_grab: Optional[QPoint] = None        # move offset (widget px)

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- public API -----------------------------------------------------

    def set_document(self, doc: Optional[PdfDocument]) -> None:
        self._doc = doc
        self._last_current = -1
        self.clear_selection()
        self.clear_objects()
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

    # -- image / signature objects (editable overlay) -------------------

    HANDLE = 9  # corner handle size (px)

    def begin_placement(self, pixmap: QPixmap, path: str) -> None:
        """Start placing an image: it rides the cursor until clicked to drop."""
        if pixmap.isNull():
            return
        self._sel_obj = -1
        self._place_pix = pixmap
        self._place_path = path
        w = 200
        h = max(20, round(pixmap.height() * w / max(1, pixmap.width())))
        region = self.visibleRegion().boundingRect()
        cx = region.center().x() if not region.isEmpty() else self.width() // 2
        cy = region.center().y() if not region.isEmpty() else self.height() // 2
        self._place_rect = QRect(cx - w // 2, cy - h // 2, w, h)
        self._place_following = True
        self._place_page = None
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.update()

    def _placing(self) -> bool:
        return self._place_pix is not None and self._place_following

    def _cancel_placement(self) -> None:
        self._place_pix = None
        self._place_path = None
        self._place_rect = None
        self._place_following = False
        self._place_page = None
        self.update()

    def _drop_placement(self, pt: QPoint) -> None:
        """Turn the cursor-riding image into a persistent, editable object."""
        r = self._place_rect
        page = self._hit_test(pt) or self._hit_test(r.center())
        if page is None and self._pages:
            page = min(self._pages, key=lambda p: abs((p["y"] + p["h"] / 2) - r.center().y()))
        if page is not None:
            x0 = (r.left() - page["x"]) / self._zoom
            y0 = (r.top() - page["y"]) / self._zoom
            x1 = (r.right() - page["x"]) / self._zoom
            y1 = (r.bottom() - page["y"]) / self._zoom
            self._objects.append({
                "page": page["index"], "rect": [x0, y0, x1, y1],
                "pixmap": self._place_pix, "path": self._place_path,
            })
            self._sel_obj = len(self._objects) - 1
            self.objects_changed.emit()
        self._cancel_placement()

    def overlay_objects(self) -> list:
        """Objects for saving/flattening, tagged by kind so the document layer
        can stamp images, write real text, or add note annotations."""
        out = []
        for o in self._objects:
            kind = o.get("kind", "image")
            rect = list(o["rect"])
            if kind == "text":
                # Font scales with the box: derive the effective point size from
                # how much the object was resized relative to its natural height.
                base_h = o.get("base_h") or (rect[3] - rect[1]) or 1
                eff = o["size"] * ((rect[3] - rect[1]) / base_h)
                out.append({"kind": "text", "page": o["page"], "rect": rect,
                            "text": o["text"], "size": eff, "color": list(o["color"])})
            elif kind == "note":
                out.append({"kind": "note", "page": o["page"], "rect": rect,
                            "text": o["text"]})
            else:
                out.append({"kind": "image", "page": o["page"], "rect": rect,
                            "path": o["path"]})
        return out

    def has_objects(self) -> bool:
        return bool(self._objects)

    def clear_objects(self) -> None:
        self._objects = []
        self._sel_obj = -1
        self.update()

    def delete_selected_object(self) -> None:
        if 0 <= self._sel_obj < len(self._objects):
            del self._objects[self._sel_obj]
            self._sel_obj = -1
            self.objects_changed.emit()
            self.update()

    # -- text / note objects (editable, flattened to real PDF content) --

    _TEXT_SS = 4  # supersample: canvas pixels rendered per PDF point

    def _render_text_pixmap(self, text: str, size: float,
                            color: tuple) -> tuple[QPixmap, float, float]:
        """Render text to a transparent pixmap. Returns (pixmap, w_pts, h_pts)."""
        font = QFont("Helvetica")
        font.setPixelSize(max(4, round(size * self._TEXT_SS)))
        fm = QFontMetrics(font)
        lines = text.split("\n") or [""]
        w = max((fm.horizontalAdvance(ln) for ln in lines), default=1) + 6
        line_h = fm.height()
        h = line_h * len(lines) + 6
        pix = QPixmap(max(1, w), max(1, h))
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setFont(font)
        p.setPen(QColor.fromRgbF(*color))
        y = fm.ascent() + 3
        for ln in lines:
            p.drawText(3, y, ln)
            y += line_h
        p.end()
        return pix, w / self._TEXT_SS, h / self._TEXT_SS

    def _render_note_pixmap(self) -> tuple[QPixmap, float, float]:
        """Render a small sticky-note icon. Returns (pixmap, w_pts, h_pts)."""
        s = 22  # points
        px = round(s * self._TEXT_SS)
        pix = QPixmap(px, px)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(255, 214, 0))
        p.setPen(QPen(QColor(180, 150, 0), self._TEXT_SS))
        r = self._TEXT_SS
        p.drawRoundedRect(r, r, px - 2 * r, px - 2 * r, 2 * r, 2 * r)
        p.setPen(QPen(QColor(120, 100, 0), max(1, self._TEXT_SS // 2)))
        for i in range(1, 4):
            yy = round(px * 0.25 * i)
            p.drawLine(round(px * 0.25), yy, round(px * 0.75), yy)
        p.end()
        return pix, float(s), float(s)

    def add_text_object(self, page_index: int, x: float, y: float, text: str,
                        size: float = 14.0, color: tuple = (0, 0, 0)) -> None:
        """Insert editable text as a selectable overlay object."""
        pix, bw, bh = self._render_text_pixmap(text, size, color)
        self._objects.append({
            "kind": "text", "page": page_index, "rect": [x, y, x + bw, y + bh],
            "pixmap": pix, "text": text, "size": size, "color": list(color),
            "base_w": bw, "base_h": bh,
        })
        self._sel_obj = len(self._objects) - 1
        self.objects_changed.emit()
        self.update()

    def add_note_object(self, page_index: int, x: float, y: float, text: str) -> None:
        """Insert an editable sticky note as a selectable overlay object."""
        pix, bw, bh = self._render_note_pixmap()
        self._objects.append({
            "kind": "note", "page": page_index, "rect": [x, y, x + bw, y + bh],
            "pixmap": pix, "text": text, "base_w": bw, "base_h": bh,
        })
        self._sel_obj = len(self._objects) - 1
        self.objects_changed.emit()
        self.update()

    def object_kind(self, i: int) -> Optional[str]:
        if 0 <= i < len(self._objects):
            return self._objects[i].get("kind", "image")
        return None

    def object_text(self, i: int) -> str:
        if 0 <= i < len(self._objects):
            return self._objects[i].get("text", "")
        return ""

    def update_text_object(self, i: int, new_text: str) -> None:
        """Replace the text of a text/note object (empty text deletes it)."""
        if not (0 <= i < len(self._objects)):
            return
        o = self._objects[i]
        if not new_text:
            del self._objects[i]
            self._sel_obj = -1
            self.objects_changed.emit()
            self.update()
            return
        if o.get("kind") == "text":
            pix, bw, bh = self._render_text_pixmap(new_text, o["size"], tuple(o["color"]))
            x0, y0 = o["rect"][0], o["rect"][1]
            o.update(text=new_text, pixmap=pix, base_w=bw, base_h=bh,
                     rect=[x0, y0, x0 + bw, y0 + bh])
        else:
            o["text"] = new_text
        self.objects_changed.emit()
        self.update()

    def _object_widget_rect(self, o: dict) -> Optional[QRect]:
        pg = self._page_layout(o["page"])
        if pg is None:
            return None
        x0, y0, x1, y1 = o["rect"]
        return QRect(
            round(pg["x"] + x0 * self._zoom), round(pg["y"] + y0 * self._zoom),
            round((x1 - x0) * self._zoom), round((y1 - y0) * self._zoom),
        )

    def _obj_handles(self, wr: QRect) -> list:
        s = self.HANDLE
        return [
            QRect(wr.left() - s // 2, wr.top() - s // 2, s, s),
            QRect(wr.right() - s // 2, wr.top() - s // 2, s, s),
            QRect(wr.right() - s // 2, wr.bottom() - s // 2, s, s),
            QRect(wr.left() - s // 2, wr.bottom() - s // 2, s, s),
        ]

    def _obj_corner_at(self, pt: QPoint) -> int:
        if not (0 <= self._sel_obj < len(self._objects)):
            return -1
        wr = self._object_widget_rect(self._objects[self._sel_obj])
        if wr is None:
            return -1
        for i, h in enumerate(self._obj_handles(wr)):
            if h.adjusted(-3, -3, 3, 3).contains(pt):
                return i
        return -1

    def _hit_object(self, pt: QPoint) -> int:
        for i in range(len(self._objects) - 1, -1, -1):
            wr = self._object_widget_rect(self._objects[i])
            if wr is not None and wr.contains(pt):
                return i
        return -1

    def _set_object_rect_from_widget(self, o: dict, wr: QRect) -> None:
        pg = self._page_layout(o["page"])
        if pg is None:
            return
        o["rect"] = [
            (wr.left() - pg["x"]) / self._zoom, (wr.top() - pg["y"]) / self._zoom,
            (wr.right() - pg["x"]) / self._zoom, (wr.bottom() - pg["y"]) / self._zoom,
        ]

    def _move_object(self, o: dict, top_left: QPoint) -> None:
        wr = self._object_widget_rect(o)
        if wr is None:
            return
        moved = QRect(top_left, wr.size())
        # Re-home the object to whichever page its center now sits on.
        page = self._hit_test(moved.center())
        if page is not None:
            o["page"] = page["index"]
        self._set_object_rect_from_widget(o, moved)

    def _resize_object(self, o: dict, pt: QPoint) -> None:
        wr = self._object_widget_rect(o)
        if wr is None:
            return
        corners = [wr.topLeft(), wr.topRight(), wr.bottomRight(), wr.bottomLeft()]
        anchor = corners[(self._obj_corner + 2) % 4]
        pm = o["pixmap"]
        ar = pm.width() / max(1, pm.height())
        w = max(20, abs(pt.x() - anchor.x()))
        h = max(20, abs(pt.y() - anchor.y()))
        if w / h > ar:
            w = int(h * ar)
        else:
            h = int(w / ar)
        sx = 1 if pt.x() >= anchor.x() else -1
        sy = 1 if pt.y() >= anchor.y() else -1
        new = QRect(anchor, QPoint(anchor.x() + sx * w, anchor.y() + sy * h)).normalized()
        self._set_object_rect_from_widget(o, new)

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

    def _scroll_area(self) -> Optional[QAbstractScrollArea]:
        """The QScrollArea this view lives in (for hand-tool panning)."""
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QAbstractScrollArea):
                return w
            w = w.parentWidget()
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

        # Persistent placed objects (signatures/images).
        for i, o in enumerate(self._objects):
            wr = self._object_widget_rect(o)
            if wr is None:
                continue
            painter.drawPixmap(wr, o["pixmap"])
            if i == self._sel_obj:
                painter.setPen(QPen(QColor(0, 120, 215), 1, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(wr)
                for h in self._obj_handles(wr):
                    painter.fillRect(h, QColor(0, 120, 215))

        # New image riding the cursor (before it is dropped).
        if self._placing() and self._place_rect is not None:
            painter.setOpacity(0.65)
            painter.drawPixmap(self._place_rect, self._place_pix)
            painter.setOpacity(1.0)

    # -- mouse handling -------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._doc or event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()

        # 1) A new image riding the cursor -> drop it as an editable object.
        if self._placing():
            self._drop_placement(pos)
            return

        # 2) Editable placed-object interaction (move/resize) — in ANY tool, so
        #    a just-inserted signature/image can be repositioned by hovering
        #    over it and dragging, no need to switch back to the Select tool.
        corner = self._obj_corner_at(pos)
        if corner >= 0:
            self._obj_mode = "resize"
            self._obj_corner = corner
            return
        hit = self._hit_object(pos)
        if hit >= 0:
            self._sel_obj = hit
            self._obj_mode = "move"
            wr = self._object_widget_rect(self._objects[hit])
            self._obj_grab = pos - wr.topLeft()
            self.update()
            return
        if self._sel_obj != -1:   # clicked empty space -> deselect object
            self._sel_obj = -1
            self.update()

        # 3) Hand tool: drag anywhere to pan the page (works over grey too).
        if self.tool == Tool.HAND:
            area = self._scroll_area()
            if area is not None:
                self._pan = {
                    "start": event.globalPosition().toPoint(),
                    "hbar": area.horizontalScrollBar(),
                    "vbar": area.verticalScrollBar(),
                    "h0": area.horizontalScrollBar().value(),
                    "v0": area.verticalScrollBar().value(),
                }
                self.setCursor(Qt.ClosedHandCursor)
            return

        page = self._hit_test(pos)
        if page is None:
            return
        self._active = page

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
            self._drop_placement(event.position().toPoint())
            return
        pos = event.position().toPoint()
        # Double-clicking a text/note object opens it for editing (any tool).
        hit = self._hit_object(pos)
        if hit >= 0:
            self._sel_obj = hit
            self.update()
            if self._objects[hit].get("kind") in ("text", "note"):
                self.object_edit_requested.emit(hit)
            return
        if not self._doc or event.button() != Qt.LeftButton or self.tool != Tool.SELECT:
            super().mouseDoubleClickEvent(event)
            return
        page = self._hit_test(pos)
        if page is None:
            return
        self._active = page
        self._select_word(page, pos)
        self._drag_start = pos
        self._drag_now = pos
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()

        if self._placing():
            self._place_rect.moveCenter(pos)
            self._place_page = self._hit_test(pos)
            self.update()
            return
        if self._pan is not None:
            delta = event.globalPosition().toPoint() - self._pan["start"]
            self._pan["hbar"].setValue(self._pan["h0"] - delta.x())
            self._pan["vbar"].setValue(self._pan["v0"] - delta.y())
            return
        if self._obj_mode == "move" and 0 <= self._sel_obj < len(self._objects):
            self._move_object(self._objects[self._sel_obj], pos - self._obj_grab)
            self.update()
            return
        if self._obj_mode == "resize" and 0 <= self._sel_obj < len(self._objects):
            self._resize_object(self._objects[self._sel_obj], pos)
            self.update()
            return
        if self._drag_start is not None:
            self._drag_now = pos
            if self.tool == Tool.INK:
                self._ink_stroke.append(pos)
            elif self.tool == Tool.SELECT and self._active is not None:
                self._update_selection(self._active, self._drag_start, pos)
            self.update()
            return

        self._update_hover_cursor(pos)  # idle hover: context-sensitive cursor

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.OpenHandCursor if self.tool == Tool.HAND else Qt.ArrowCursor)
            return
        if self._obj_mode is not None:
            self._obj_mode = None
            self._obj_grab = None
            self.objects_changed.emit()
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

    def _text_at(self, pos: QPoint) -> bool:
        """True if a widget point sits on selectable text."""
        page = self._hit_test(pos)
        if page is None:
            return False
        flat, _ = self._word_layout(page["index"])
        if not flat:
            return False
        px, py = self._to_pdf_on(page, pos)
        for w in flat:
            if w[0] - 1 <= px <= w[2] + 1 and w[1] - 1 <= py <= w[3] + 1:
                return True
        return False

    def _update_hover_cursor(self, pos: QPoint) -> None:
        """Set the cursor based on what's under it (idle hover)."""
        # A placed object (signature/image) is draggable in every tool, so its
        # move/resize cursors take priority over the tool's own cursor.
        corner = self._obj_corner_at(pos)
        if corner in (0, 2):
            self.setCursor(Qt.SizeFDiagCursor)
            return
        if corner in (1, 3):
            self.setCursor(Qt.SizeBDiagCursor)
            return
        if self._hit_object(pos) >= 0:
            self.setCursor(Qt.SizeAllCursor)          # move a placed object
            return
        if self.tool == Tool.HAND:
            self.setCursor(Qt.OpenHandCursor)
            return
        if self.tool != Tool.SELECT:
            self.setCursor(Qt.CrossCursor)
            return
        if self._text_at(pos):
            self.setCursor(Qt.IBeamCursor)            # over selectable text
        else:
            self.setCursor(Qt.ArrowCursor)            # plain mouse pointer

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self._placing() and event.key() == Qt.Key_Escape:
            self._cancel_placement()
            return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and 0 <= self._sel_obj < len(self._objects):
            self.delete_selected_object()
            return
        if event.key() == Qt.Key_Escape and self._sel_obj != -1:
            self._sel_obj = -1
            self.update()
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
