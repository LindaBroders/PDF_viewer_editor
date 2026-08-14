"""Headless tests for the new document and file-level operations."""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest

from pdfeditor import operations as ops
from pdfeditor.document import PdfDocument


@pytest.fixture()
def multi_pdf(tmp_path):
    """A 5-page PDF with searchable text and top-level bookmarks."""
    doc = fitz.open()
    for i in range(5):
        page = doc.new_page(width=612, height=792)
        page.insert_text(fitz.Point(72, 72), f"Page {i + 1} SECRET-{i}")
    doc.set_toc([[1, f"Chapter {i + 1}", i + 1] for i in range(5)])
    path = os.path.join(tmp_path, "multi.pdf")
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def image_file(tmp_path):
    """A small PNG created via a fitz pixmap."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 60))
    pix.clear_with(200)
    path = os.path.join(tmp_path, "img.png")
    pix.save(path)
    return path


# -- watermarks / headers / Bates ---------------------------------------

def test_text_watermark(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "wm.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.add_text_watermark("CONFIDENTIAL", opacity=0.2)
        assert doc.dirty
        doc.save(out)
    assert os.path.getsize(out) > 0


def test_header_footer_page_numbers(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "hf.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.add_header_footer("Page {page} of {pages}", position="bottom-center")
        doc.save(out)
    with PdfDocument.open(out) as check:
        assert "Page 1 of 5" in check.get_text(0)
        assert "Page 5 of 5" in check.get_text(4)


def test_bates_numbering(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "bates.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.add_bates_numbering(prefix="ABC", start=1, digits=6)
        doc.save(out)
    with PdfDocument.open(out) as check:
        assert "ABC000001" in check.get_text(0)
        assert "ABC000005" in check.get_text(4)


# -- search & redact / sanitize -----------------------------------------

def test_search_and_redact(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "red.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        n = doc.search_and_redact("SECRET-0")
        assert n == 1
        doc.save(out)
    with PdfDocument.open(out) as check:
        assert "SECRET-0" not in check.get_text(0)


def test_sanitize_clears_metadata(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "clean.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.set_metadata({"title": "Secret", "author": "Someone"})
        doc.sanitize()
        doc.save(out)
    with PdfDocument.open(out) as check:
        assert not check.metadata.get("title")
        assert not check.metadata.get("author")


# -- bookmarks / links / attachments ------------------------------------

def test_add_bookmark(multi_pdf):
    with PdfDocument.open(multi_pdf) as doc:
        before = len(doc.get_bookmarks())
        doc.add_bookmark("Appendix", page=4)
        assert len(doc.get_bookmarks()) == before + 1


def test_attachments_roundtrip(multi_pdf, tmp_path):
    payload = os.path.join(tmp_path, "note.txt")
    with open(payload, "w") as fh:
        fh.write("hello attachment")
    out = os.path.join(tmp_path, "att.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.attach_file(payload, name="note.txt")
        doc.save(out)
    with PdfDocument.open(out) as check:
        assert "note.txt" in check.list_attachments()
        extracted = os.path.join(tmp_path, "extracted.txt")
        check.extract_attachment("note.txt", extracted)
        assert open(extracted).read() == "hello attachment"


def test_add_link_uri(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "link.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.add_link_uri(0, (72, 72, 200, 90), "https://example.com")
        doc.save(out)
    with fitz.open(out) as check:
        links = check.load_page(0).get_links()
        assert any(l.get("uri") == "https://example.com" for l in links)


# -- forms --------------------------------------------------------------

def test_form_field_add_and_fill(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "form.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.add_text_field(0, (100, 200, 300, 220), "full_name")
        doc.save(out)
    with PdfDocument.open(out) as doc2:
        fields = doc2.get_form_fields()
        assert any(f["name"] == "full_name" for f in fields)
        assert doc2.fill_form_field("full_name", "Linda Broders")
        out2 = os.path.join(tmp_path, "form2.pdf")
        doc2.save(out2)
    with PdfDocument.open(out2) as doc3:
        vals = {f["name"]: f["value"] for f in doc3.get_form_fields()}
        assert vals["full_name"] == "Linda Broders"


# -- security -----------------------------------------------------------

def test_save_encrypted_requires_password(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "enc.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.save_encrypted(out, user_pw="secret123")
    reopened = fitz.open(out)
    assert reopened.is_encrypted
    assert reopened.authenticate("wrong") == 0
    assert reopened.authenticate("secret123") > 0
    reopened.close()


# -- crop / images ------------------------------------------------------

def test_crop_page(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "crop.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.crop_page(0, (100, 100, 400, 500))
        doc.save(out)
    with PdfDocument.open(out) as check:
        w, h = check.page_size(0)
        assert round(w) == 300 and round(h) == 400


def test_extract_images(image_file, tmp_path):
    # Build a PDF that embeds the image, then extract it back out.
    pdf = os.path.join(tmp_path, "withimg.pdf")
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.insert_image(fitz.Rect(20, 20, 220, 140), filename=image_file)
    doc.save(pdf)
    doc.close()
    with PdfDocument.open(pdf) as d:
        saved = d.extract_images(os.path.join(tmp_path, "imgs"))
    assert len(saved) >= 1
    assert all(os.path.getsize(p) > 0 for p in saved)


# -- file-level operations ----------------------------------------------

def test_merge_files(multi_pdf, image_file, tmp_path):
    out = os.path.join(tmp_path, "merged.pdf")
    ops.merge_files([multi_pdf, image_file, multi_pdf], out)
    with fitz.open(out) as check:
        # 5 pages + 1 image page + 5 pages
        assert check.page_count == 11


def test_images_to_pdf(image_file, tmp_path):
    out = os.path.join(tmp_path, "imgs.pdf")
    ops.images_to_pdf([image_file, image_file], out)
    with fitz.open(out) as check:
        assert check.page_count == 2


def test_split_by_page_count(multi_pdf, tmp_path):
    outs = ops.split_by_page_count(multi_pdf, 2, os.path.join(tmp_path, "split"))
    # 5 pages -> 2 + 2 + 1
    assert len(outs) == 3
    counts = []
    for p in outs:
        with fitz.open(p) as d:
            counts.append(d.page_count)
    assert counts == [2, 2, 1]


def test_split_by_bookmarks(multi_pdf, tmp_path):
    outs = ops.split_by_bookmarks(multi_pdf, os.path.join(tmp_path, "bm"))
    assert len(outs) == 5  # one per chapter


def test_split_by_size(multi_pdf, tmp_path):
    outs = ops.split_by_size(multi_pdf, 4096, os.path.join(tmp_path, "sz"))
    assert len(outs) >= 1
    for p in outs:
        with fitz.open(p) as d:
            assert d.page_count >= 1


def test_optimize_returns_sizes(multi_pdf, tmp_path):
    out = os.path.join(tmp_path, "opt.pdf")
    old, new = ops.optimize(multi_pdf, out)
    assert old > 0 and new > 0
    assert os.path.exists(out)


def test_compare_text_detects_change(multi_pdf, tmp_path):
    # Make a modified copy.
    modified = os.path.join(tmp_path, "modified.pdf")
    with PdfDocument.open(multi_pdf) as doc:
        doc.add_text(0, (72, 200), "AN EXTRA LINE OF TEXT")
        doc.save(modified)
    report = ops.compare_text(multi_pdf, modified)
    assert "AN EXTRA LINE OF TEXT" in report


def test_compare_text_identical(multi_pdf):
    report = ops.compare_text(multi_pdf, multi_pdf)
    assert "No text differences" in report


def test_snapshot_restore_roundtrip(multi_pdf):
    """to_bytes()/restore_bytes() power undo/redo."""
    with PdfDocument.open(multi_pdf) as doc:
        snapshot = doc.to_bytes()
        assert doc.page_count == 5
        doc.delete_page(0)
        doc.add_text_watermark("DRAFT")
        assert doc.page_count == 4
        # Restore the earlier snapshot (undo).
        doc.restore_bytes(snapshot)
        assert doc.page_count == 5
        assert "DRAFT" not in doc.get_text(0)


def test_get_words_positions(multi_pdf):
    with PdfDocument.open(multi_pdf) as doc:
        words = doc.get_words(0)
        assert words
        # Each entry: (x0, y0, x1, y1, word, block, line, word_no)
        assert len(words[0]) >= 8
        assert any("SECRET" in w[4] for w in words)
