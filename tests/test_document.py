"""Headless unit tests for the PDF document model.

These exercise the editing engine (:mod:`pdfeditor.document`) without any
GUI, so they run in CI on Fedora without a display server.
"""

from __future__ import annotations

import os

import fitz
import pytest

from pdfeditor.document import DocumentError, PdfDocument


@pytest.fixture()
def sample_pdf(tmp_path):
    """Create a small 3-page PDF on disk and return its path."""
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text(fitz.Point(72, 72), f"Page {i + 1} hello world")
    path = os.path.join(tmp_path, "sample.pdf")
    doc.save(path)
    doc.close()
    return path


def test_new_document_has_one_page():
    with PdfDocument.new() as doc:
        assert doc.page_count == 1
        assert doc.dirty is False


def test_open_and_page_count(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        assert doc.page_count == 3
        assert doc.path == sample_pdf


def test_open_missing_file_raises():
    with pytest.raises(DocumentError):
        PdfDocument.open("/no/such/file.pdf")


def test_get_text(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        assert "hello world" in doc.get_text(0)


def test_search_whole_document(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        hits = doc.search("hello")
        assert len(hits) == 3
        assert {h.page for h in hits} == {0, 1, 2}


def test_search_single_page(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        hits = doc.search("hello", page=1)
        assert len(hits) == 1
        assert hits[0].page == 1


def test_render_page_dimensions(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        rp = doc.render_page(0, zoom=2.0)
        # Letter page at 2x: 612*2 x 792*2.
        assert rp.width == 1224
        assert rp.height == 1584
        assert len(rp.samples) == rp.stride * rp.height


def test_rotate_marks_dirty(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        doc.rotate_page(0, 90)
        assert doc.dirty is True


def test_delete_page(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        doc.delete_page(1)
        assert doc.page_count == 2


def test_cannot_delete_last_page():
    with PdfDocument.new() as doc:
        with pytest.raises(DocumentError):
            doc.delete_page(0)


def test_insert_blank_page(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        idx = doc.insert_blank_page(1)
        assert idx == 1
        assert doc.page_count == 4


def test_move_page(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        first_text = doc.get_text(0)
        doc.move_page(0, 3)  # move first page to the end
        assert doc.get_text(doc.page_count - 1).strip() == first_text.strip()


def test_add_text_roundtrip(sample_pdf, tmp_path):
    out = os.path.join(tmp_path, "out.pdf")
    with PdfDocument.open(sample_pdf) as doc:
        doc.add_text(0, (100, 200), "INSERTED-TEXT-XYZ")
        doc.save(out)
    with PdfDocument.open(out) as doc2:
        assert "INSERTED-TEXT-XYZ" in doc2.get_text(0)


def test_highlight_annotation(sample_pdf, tmp_path):
    out = os.path.join(tmp_path, "hl.pdf")
    with PdfDocument.open(sample_pdf) as doc:
        doc.add_highlight(0, (72, 60, 300, 90))
        doc.save(out)
    with fitz.open(out) as check:
        annots = list(check.load_page(0).annots() or [])
        assert len(annots) >= 1


def test_ink_annotation(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        doc.add_ink(0, [[(10, 10), (20, 20), (30, 15)]])
        assert doc.dirty is True


def test_redaction_removes_text(sample_pdf, tmp_path):
    out = os.path.join(tmp_path, "red.pdf")
    with PdfDocument.open(sample_pdf) as doc:
        # The page text sits near (72, 72); redact a box around it.
        doc.redact(0, (60, 60, 400, 90))
        doc.save(out)
    with PdfDocument.open(out) as doc2:
        assert "hello world" not in doc2.get_text(0)


def test_extract_pages(sample_pdf, tmp_path):
    out = os.path.join(tmp_path, "extract.pdf")
    with PdfDocument.open(sample_pdf) as doc:
        doc.extract_pages([0, 2], out)
    with PdfDocument.open(out) as ex:
        assert ex.page_count == 2


def test_append_pdf(sample_pdf):
    with PdfDocument.open(sample_pdf) as doc:
        doc.append_pdf(sample_pdf)
        assert doc.page_count == 6


def test_save_as_updates_path(sample_pdf, tmp_path):
    out = os.path.join(tmp_path, "saved.pdf")
    with PdfDocument.open(sample_pdf) as doc:
        doc.add_text(0, (100, 100), "x")
        doc.save(out)
        assert doc.path == out
        assert doc.dirty is False


def test_metadata_roundtrip(sample_pdf, tmp_path):
    out = os.path.join(tmp_path, "meta.pdf")
    with PdfDocument.open(sample_pdf) as doc:
        meta = doc.metadata
        meta["title"] = "My Title"
        meta["author"] = "Tester"
        doc.set_metadata(meta)
        doc.save(out)
    with PdfDocument.open(out) as doc2:
        assert doc2.metadata.get("title") == "My Title"
        assert doc2.metadata.get("author") == "Tester"
