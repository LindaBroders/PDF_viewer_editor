"""PDF document model.

Wraps a PyMuPDF (``fitz``) document and exposes the editing operations the
UI needs: rendering pages to images, page management (insert / delete /
rotate / move), text and image insertion, annotations, redaction, search,
and saving. This module is deliberately GUI-free so it can be unit-tested
headlessly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

import pymupdf as fitz  # PyMuPDF (modern import name; `fitz` alias kept for readability)


class DocumentError(Exception):
    """Raised for document-level failures (open, save, invalid page, ...)."""


@dataclass(frozen=True)
class SearchHit:
    """A single search match: the page it is on and its bounding rectangle."""

    page: int
    rect: fitz.Rect


class PdfDocument:
    """An editable, in-memory PDF document.

    All mutating operations set :attr:`dirty` so the UI can prompt before
    discarding unsaved work. Coordinates are in PDF points (1/72 inch) using
    PyMuPDF's top-left origin convention.
    """

    def __init__(self, doc: fitz.Document, path: Optional[str] = None) -> None:
        self._doc = doc
        self.path = path
        self.dirty = False

    # -- construction ---------------------------------------------------

    @classmethod
    def open(cls, path: str) -> "PdfDocument":
        """Open an existing PDF from disk."""
        if not os.path.exists(path):
            raise DocumentError(f"File not found: {path}")
        try:
            doc = fitz.open(path)
        except Exception as exc:  # pragma: no cover - passthrough of fitz errors
            raise DocumentError(f"Could not open {path!r}: {exc}") from exc
        if not doc.is_pdf:
            # Convert non-PDF (e.g. images, XPS) into a PDF so we can edit it.
            pdf_bytes = doc.convert_to_pdf()
            doc.close()
            doc = fitz.open("pdf", pdf_bytes)
            return cls(doc, path=None)
        return cls(doc, path=path)

    @classmethod
    def new(cls) -> "PdfDocument":
        """Create a new, empty single-page document (US Letter)."""
        doc = fitz.open()
        doc.new_page(width=612, height=792)  # 8.5in x 11in
        return cls(doc, path=None)

    # -- basic properties ----------------------------------------------

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    @property
    def is_encrypted(self) -> bool:
        return bool(self._doc.is_encrypted)

    def authenticate(self, password: str) -> bool:
        """Unlock an encrypted document. Returns True on success."""
        return bool(self._doc.authenticate(password))

    @property
    def metadata(self) -> dict:
        return dict(self._doc.metadata or {})

    def set_metadata(self, meta: dict) -> None:
        self._doc.set_metadata(meta)
        self.dirty = True

    def page_size(self, index: int) -> tuple[float, float]:
        """Return (width, height) in points for the given page."""
        page = self._page(index)
        rect = page.rect
        return (rect.width, rect.height)

    # -- rendering ------------------------------------------------------

    def render_page(self, index: int, zoom: float = 1.0) -> "RenderedPage":
        """Rasterize a page at the given zoom factor.

        Returns a :class:`RenderedPage` holding RGBA bytes plus dimensions,
        which the Qt layer turns into a ``QImage`` without importing Qt here.
        """
        page = self._page(index)
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=True)
        return RenderedPage(
            width=pix.width,
            height=pix.height,
            samples=pix.samples,
            stride=pix.stride,
        )

    def get_text(self, index: int) -> str:
        """Extract the plain text of a page."""
        return self._page(index).get_text("text")

    def get_words(self, index: int) -> list:
        """Return the page's words with positions.

        Each entry is ``(x0, y0, x1, y1, word, block_no, line_no, word_no)`` in
        PDF points — used by the viewer for interactive text selection.
        """
        return self._page(index).get_text("words")

    # -- search ---------------------------------------------------------

    def search(self, needle: str, page: Optional[int] = None) -> list[SearchHit]:
        """Find every occurrence of ``needle``.

        Searches a single page when ``page`` is given, otherwise the whole
        document.
        """
        if not needle:
            return []
        hits: list[SearchHit] = []
        pages: Iterable[int] = (
            [page] if page is not None else range(self.page_count)
        )
        for p in pages:
            for rect in self._page(p).search_for(needle):
                hits.append(SearchHit(page=p, rect=rect))
        return hits

    # -- page management ------------------------------------------------

    def rotate_page(self, index: int, degrees: int) -> None:
        """Rotate a page by a multiple of 90 degrees (relative)."""
        page = self._page(index)
        page.set_rotation((page.rotation + degrees) % 360)
        self.dirty = True

    def delete_page(self, index: int) -> None:
        if self.page_count <= 1:
            raise DocumentError("Cannot delete the last remaining page.")
        self._validate_index(index)
        self._doc.delete_page(index)
        self.dirty = True

    def move_page(self, from_index: int, to_index: int) -> None:
        self._validate_index(from_index)
        # to_index may equal page_count (append at the end); PyMuPDF uses -1
        # to mean "after the last page".
        if not (0 <= to_index <= self.page_count):
            raise DocumentError(f"Invalid target index: {to_index}")
        target = -1 if to_index >= self.page_count else to_index
        self._doc.move_page(from_index, target)
        self.dirty = True

    def insert_blank_page(
        self, index: Optional[int] = None, width: float = 612, height: float = 792
    ) -> int:
        """Insert a blank page. Returns the new page's index."""
        at = self.page_count if index is None else index
        self._doc.new_page(pno=at, width=width, height=height)
        self.dirty = True
        return at

    def append_pdf(self, other_path: str) -> None:
        """Append every page of another PDF to this document."""
        with fitz.open(other_path) as src:
            self._doc.insert_pdf(src)
        self.dirty = True

    def extract_pages(self, indices: list[int], out_path: str) -> None:
        """Save a subset of pages as a new PDF file."""
        out = fitz.open()
        for i in indices:
            self._validate_index(i)
            out.insert_pdf(self._doc, from_page=i, to_page=i)
        out.save(out_path)
        out.close()

    # -- content editing ------------------------------------------------

    def add_text(
        self,
        index: int,
        point: tuple[float, float],
        text: str,
        size: float = 11.0,
        color: tuple[float, float, float] = (0, 0, 0),
        fontname: str = "helv",
    ) -> None:
        """Insert real (selectable) text at a point on a page."""
        page = self._page(index)
        page.insert_text(
            fitz.Point(*point),
            text,
            fontsize=size,
            color=color,
            fontname=fontname,
        )
        self.dirty = True

    def add_image(
        self, index: int, rect: tuple[float, float, float, float], image_path: str
    ) -> None:
        """Place an image inside a rectangle on a page."""
        page = self._page(index)
        page.insert_image(fitz.Rect(*rect), filename=image_path)
        self.dirty = True

    def add_highlight(
        self, index: int, rect: tuple[float, float, float, float]
    ) -> None:
        page = self._page(index)
        page.add_highlight_annot(fitz.Rect(*rect))
        self.dirty = True

    def add_underline(
        self, index: int, rect: tuple[float, float, float, float]
    ) -> None:
        page = self._page(index)
        page.add_underline_annot(fitz.Rect(*rect))
        self.dirty = True

    def add_strikeout(
        self, index: int, rect: tuple[float, float, float, float]
    ) -> None:
        page = self._page(index)
        page.add_strikeout_annot(fitz.Rect(*rect))
        self.dirty = True

    def add_ink(
        self,
        index: int,
        strokes: list[list[tuple[float, float]]],
        color: tuple[float, float, float] = (1, 0, 0),
        width: float = 1.5,
    ) -> None:
        """Add a freehand (ink) annotation from one or more polylines."""
        page = self._page(index)
        # PyMuPDF wants a sequence of sequences of (x, y) float pairs.
        points = [[(float(p[0]), float(p[1])) for p in stroke] for stroke in strokes]
        annot = page.add_ink_annot(points)
        annot.set_colors(stroke=color)
        annot.set_border(width=width)
        annot.update()
        self.dirty = True

    def add_rect_annot(
        self,
        index: int,
        rect: tuple[float, float, float, float],
        color: tuple[float, float, float] = (1, 0, 0),
        width: float = 1.5,
    ) -> None:
        page = self._page(index)
        annot = page.add_rect_annot(fitz.Rect(*rect))
        annot.set_colors(stroke=color)
        annot.set_border(width=width)
        annot.update()
        self.dirty = True

    def add_note(
        self, index: int, point: tuple[float, float], text: str
    ) -> None:
        """Add a sticky-note (text) annotation."""
        page = self._page(index)
        page.add_text_annot(fitz.Point(*point), text)
        self.dirty = True

    def redact(
        self, index: int, rect: tuple[float, float, float, float]
    ) -> None:
        """Permanently remove content within a rectangle (true redaction)."""
        page = self._page(index)
        page.add_redact_annot(fitz.Rect(*rect), fill=(0, 0, 0))
        page.apply_redactions()
        self.dirty = True

    def search_and_redact(self, text: str) -> int:
        """Find every occurrence of ``text`` and permanently redact it.

        Returns the number of occurrences removed.
        """
        if not text:
            return 0
        count = 0
        for i in range(self.page_count):
            page = self._page(i)
            rects = page.search_for(text)
            for rect in rects:
                page.add_redact_annot(rect, fill=(0, 0, 0))
                count += 1
            if rects:
                page.apply_redactions()
        if count:
            self.dirty = True
        return count

    def add_stamp(
        self,
        index: int,
        point: tuple[float, float],
        text: str = "APPROVED",
        color: tuple[float, float, float] = (0.8, 0, 0),
        fontsize: float = 14.0,
    ) -> None:
        """Add a bordered rubber-stamp-style text mark."""
        page = self._page(index)
        x, y = point
        width = fitz.get_text_length(text, fontsize=fontsize) + 16
        rect = fitz.Rect(x, y, x + width, y + fontsize + 10)
        page.draw_rect(rect, color=color, width=1.5)
        page.insert_text((x + 8, y + fontsize + 2), text, fontsize=fontsize, color=color)
        self.dirty = True

    # -- page geometry --------------------------------------------------

    def crop_page(
        self, index: int, rect: tuple[float, float, float, float]
    ) -> None:
        """Set the visible crop box of a page (in points)."""
        page = self._page(index)
        page.set_cropbox(fitz.Rect(*rect))
        self.dirty = True

    def replace_page(self, index: int, src_path: str, src_index: int = 0) -> None:
        """Replace one page with a page from another PDF."""
        self._validate_index(index)
        with fitz.open(src_path) as src:
            self._doc.insert_pdf(
                src, from_page=src_index, to_page=src_index, start_at=index
            )
        # The original page shifted to index + 1; remove it.
        self._doc.delete_page(index + 1)
        self.dirty = True

    # -- watermarks / headers / footers / Bates -------------------------

    def add_text_watermark(
        self,
        text: str,
        opacity: float = 0.15,
        rotate: int = 45,
        fontsize: float = 48.0,
        color: tuple[float, float, float] = (0.5, 0.5, 0.5),
        pages: Optional[list[int]] = None,
    ) -> None:
        """Stamp a diagonal, semi-transparent text watermark on pages."""
        for i in pages if pages is not None else range(self.page_count):
            page = self._page(i)
            rect = page.rect
            pivot = fitz.Point(rect.width / 2, rect.height / 2)
            matrix = fitz.Matrix(rotate)
            text_len = fitz.get_text_length(text, fontsize=fontsize)
            point = fitz.Point((rect.width - text_len) / 2, rect.height / 2)
            writer = fitz.TextWriter(rect, opacity=opacity, color=color)
            writer.append(point, text, fontsize=fontsize)
            writer.write_text(page, morph=(pivot, matrix))
        self.dirty = True

    def add_image_watermark(
        self,
        image_path: str,
        opacity: float = 0.2,
        pages: Optional[list[int]] = None,
    ) -> None:
        """Place a centered, semi-transparent image watermark on pages."""
        for i in pages if pages is not None else range(self.page_count):
            page = self._page(i)
            rect = page.rect
            # Center a box covering ~60% of the page width.
            w = rect.width * 0.6
            h = rect.height * 0.6
            box = fitz.Rect(
                (rect.width - w) / 2,
                (rect.height - h) / 2,
                (rect.width + w) / 2,
                (rect.height + h) / 2,
            )
            page.insert_image(box, filename=image_path, overlay=True, keep_proportion=True)
        self.dirty = True

    def add_header_footer(
        self,
        text: str,
        position: str = "bottom-center",
        fontsize: float = 9.0,
        color: tuple[float, float, float] = (0, 0, 0),
        margin: float = 24.0,
        pages: Optional[list[int]] = None,
    ) -> None:
        """Add a header or footer line at a named position on each page.

        ``position`` is one of top/bottom + left/center/right, e.g.
        ``"top-right"`` or ``"bottom-center"``. The literal ``{page}`` and
        ``{pages}`` in ``text`` expand to the current and total page numbers.
        """
        total = self.page_count
        for i in pages if pages is not None else range(total):
            page = self._page(i)
            label = text.replace("{page}", str(i + 1)).replace("{pages}", str(total))
            self._place_text(page, label, position, margin, fontsize, color)
        self.dirty = True

    def add_bates_numbering(
        self,
        prefix: str = "",
        suffix: str = "",
        start: int = 1,
        digits: int = 6,
        position: str = "bottom-right",
        fontsize: float = 9.0,
        color: tuple[float, float, float] = (0, 0, 0),
    ) -> None:
        """Apply sequential Bates numbers (e.g. ``ABC000001``) to every page."""
        number = start
        for i in range(self.page_count):
            page = self._page(i)
            label = f"{prefix}{number:0{digits}d}{suffix}"
            self._place_text(page, label, position, 24.0, fontsize, color)
            number += 1
        self.dirty = True

    def _place_text(self, page, text, position, margin, fontsize, color) -> None:
        """Insert ``text`` at a named corner/edge of a page."""
        rect = page.rect
        text_len = fitz.get_text_length(text, fontsize=fontsize)
        vert, _, horiz = position.partition("-")
        if horiz == "left":
            x = margin
        elif horiz == "right":
            x = rect.width - text_len - margin
        else:  # center
            x = (rect.width - text_len) / 2
        y = margin + fontsize if vert == "top" else rect.height - margin
        page.insert_text((x, y), text, fontsize=fontsize, color=color)

    # -- bookmarks / links / attachments --------------------------------

    def get_bookmarks(self) -> list[list]:
        """Return the table of contents as ``[level, title, page_number]`` rows."""
        return self._doc.get_toc()

    def set_bookmarks(self, toc: list[list]) -> None:
        self._doc.set_toc(toc)
        self.dirty = True

    def add_bookmark(self, title: str, page: int, level: int = 1) -> None:
        """Append a bookmark pointing at a page (0-based)."""
        toc = self._doc.get_toc()
        toc.append([level, title, page + 1])
        self._doc.set_toc(toc)
        self.dirty = True

    def add_link_uri(
        self, index: int, rect: tuple[float, float, float, float], uri: str
    ) -> None:
        """Add a clickable external (web) link over a rectangle."""
        self._page(index).insert_link(
            {"kind": fitz.LINK_URI, "from": fitz.Rect(*rect), "uri": uri}
        )
        self.dirty = True

    def add_link_goto(
        self,
        index: int,
        rect: tuple[float, float, float, float],
        target_page: int,
    ) -> None:
        """Add an internal link jumping to another page."""
        self._page(index).insert_link(
            {"kind": fitz.LINK_GOTO, "from": fitz.Rect(*rect), "page": target_page}
        )
        self.dirty = True

    def attach_file(self, file_path: str, name: Optional[str] = None) -> None:
        """Embed an arbitrary file inside the PDF."""
        with open(file_path, "rb") as fh:
            data = fh.read()
        name = name or os.path.basename(file_path)
        self._doc.embfile_add(name, data, filename=name)
        self.dirty = True

    def list_attachments(self) -> list[str]:
        return list(self._doc.embfile_names())

    def extract_attachment(self, name: str, out_path: str) -> None:
        data = self._doc.embfile_get(name)
        with open(out_path, "wb") as fh:
            fh.write(data)

    # -- images ---------------------------------------------------------

    def extract_images(self, out_dir: str) -> list[str]:
        """Save every embedded raster image to ``out_dir``. Returns paths."""
        os.makedirs(out_dir, exist_ok=True)
        saved: list[str] = []
        seen: set[int] = set()
        for i in range(self.page_count):
            for img in self._page(i).get_images(full=True):
                xref = img[0]
                if xref in seen:
                    continue
                seen.add(xref)
                info = self._doc.extract_image(xref)
                out = os.path.join(out_dir, f"image_{xref}.{info['ext']}")
                with open(out, "wb") as fh:
                    fh.write(info["image"])
                saved.append(out)
        return saved

    # -- forms ----------------------------------------------------------

    def get_form_fields(self) -> list[dict]:
        """List all form fields with their page, name, type, and value."""
        fields: list[dict] = []
        for i in range(self.page_count):
            for widget in self._page(i).widgets() or []:
                fields.append(
                    {
                        "page": i,
                        "name": widget.field_name,
                        "type": widget.field_type_string,
                        "value": widget.field_value,
                    }
                )
        return fields

    def fill_form_field(self, name: str, value) -> bool:
        """Set the value of every field named ``name``. Returns True if found."""
        found = False
        for i in range(self.page_count):
            for widget in self._page(i).widgets() or []:
                if widget.field_name == name:
                    widget.field_value = value
                    widget.update()
                    found = True
        if found:
            self.dirty = True
        return found

    def add_text_field(
        self,
        index: int,
        rect: tuple[float, float, float, float],
        name: str,
        value: str = "",
    ) -> None:
        """Add a fillable text form field to a page."""
        page = self._page(index)
        widget = fitz.Widget()
        widget.rect = fitz.Rect(*rect)
        widget.field_name = name
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value = value
        page.add_widget(widget)
        self.dirty = True

    def flatten(self) -> None:
        """Bake annotations and form fields into the page content (flatten)."""
        self._doc.bake()
        self.dirty = True

    # -- sanitize / security -------------------------------------------

    def sanitize(self) -> None:
        """Remove hidden metadata, JavaScript, and XML metadata."""
        self._doc.scrub(metadata=True, javascript=True, xml_metadata=True)
        self._doc.set_metadata({})
        self.dirty = True

    def save_encrypted(
        self,
        path: str,
        user_pw: str = "",
        owner_pw: str = "",
        allow_print: bool = True,
        allow_copy: bool = True,
        allow_modify: bool = True,
        allow_annotate: bool = True,
    ) -> str:
        """Save a 256-bit AES encrypted copy with the given permissions.

        ``user_pw`` is required to open the file; ``owner_pw`` (defaults to the
        user password) governs permission changes.
        """
        perm = int(fitz.PDF_PERM_ACCESSIBILITY)
        if allow_print:
            perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
        if allow_copy:
            perm |= fitz.PDF_PERM_COPY
        if allow_modify:
            perm |= fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ASSEMBLE
        if allow_annotate:
            perm |= fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM
        self._doc.save(
            path,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw=owner_pw or user_pw,
            user_pw=user_pw,
            permissions=perm,
            garbage=4,
            deflate=True,
        )
        return path

    # -- persistence ----------------------------------------------------

    def save(self, path: Optional[str] = None) -> str:
        """Save the document. Saves in place when ``path`` is omitted.

        Uses incremental save when writing back to the same file and a full
        (garbage-collected) save otherwise.
        """
        target = path or self.path
        if not target:
            raise DocumentError("No path given and document has no path.")

        same_file = self.path is not None and os.path.abspath(target) == os.path.abspath(
            self.path
        )
        try:
            if same_file:
                self._doc.save(target, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            else:
                self._doc.save(target, garbage=4, deflate=True)
        except Exception as exc:
            # Incremental save can fail (e.g. file grew); retry as a full save.
            self._doc.save(target, garbage=4, deflate=True)
        self.path = target
        self.dirty = False
        return target

    # -- undo/redo snapshots --------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialize the whole document to bytes (for an undo snapshot)."""
        return self._doc.tobytes(deflate=True, garbage=3)

    def restore_bytes(self, data: bytes) -> None:
        """Replace the current document with a previously saved snapshot."""
        new = fitz.open("pdf", data)
        old = self._doc
        self._doc = new
        try:
            old.close()
        except Exception:
            pass
        self.dirty = True

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:
            pass

    # -- context manager ------------------------------------------------

    def __enter__(self) -> "PdfDocument":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- internals ------------------------------------------------------

    def _validate_index(self, index: int) -> None:
        if not (0 <= index < self.page_count):
            raise DocumentError(
                f"Page index {index} out of range (0..{self.page_count - 1})."
            )

    def _page(self, index: int) -> fitz.Page:
        self._validate_index(index)
        return self._doc.load_page(index)


@dataclass(frozen=True)
class RenderedPage:
    """A rasterized page as raw RGBA bytes plus geometry.

    Kept Qt-free so :mod:`pdfeditor.document` has no GUI dependency; the
    viewer widget converts ``samples`` into a ``QImage``.
    """

    width: int
    height: int
    samples: bytes
    stride: int
