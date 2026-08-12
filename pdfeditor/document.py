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
