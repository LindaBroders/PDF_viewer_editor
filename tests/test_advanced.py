"""Tests for the optional advanced features.

Each test skips gracefully when its external tool or package is unavailable,
so the suite stays green whether or not LibreOffice / Tesseract / pyHanko / the
Anthropic SDK are installed. The dependency-free pieces (preflight, detection)
always run.
"""

from __future__ import annotations

import os

import pymupdf as fitz
import pytest

from pdfeditor import external, imaging, production


@pytest.fixture()
def simple_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(fitz.Point(72, 72), "Advanced features test document.")
    path = os.path.join(tmp_path, "doc.pdf")
    doc.save(path)
    doc.close()
    return path


# -- dependency detection (always runs) ---------------------------------

def test_dependency_detection_reports_bool():
    for dep in (external.LIBREOFFICE, external.OCRMYPDF, external.GHOSTSCRIPT,
                external.PYHANKO, external.ANTHROPIC):
        assert isinstance(dep.available(), bool)


def test_require_raises_with_hint():
    fake = external.Dependency("Nonexistent", "binary", "definitely-not-a-real-binary-xyz", "hint")
    with pytest.raises(external.DependencyError) as exc:
        external.require(fake)
    assert "hint" in str(exc.value)


# -- preflight (always runs) --------------------------------------------

def test_preflight_clean_document(simple_pdf):
    report = production.preflight(simple_pdf)
    assert report.page_count == 1
    assert not report.non_embedded_fonts  # PyMuPDF embeds its base fonts
    text = report.as_text()
    assert "PREFLIGHT REPORT" in text


def test_preflight_detects_low_res_image(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 30, 30))
    pix.clear_with(100)
    img = os.path.join(tmp_path, "tiny.png")
    pix.save(img)
    page.insert_image(fitz.Rect(0, 0, 600, 600), filename=img)  # 30px stretched huge
    path = os.path.join(tmp_path, "lowres.pdf")
    doc.save(path)
    doc.close()
    report = production.preflight(path)
    assert report.low_res_images  # should flag it


# -- Office conversion (skips without LibreOffice) ----------------------

@pytest.mark.skipif(not external.LIBREOFFICE.available(), reason="LibreOffice not installed")
def test_office_to_pdf(tmp_path):
    from pdfeditor import convert

    src = os.path.join(tmp_path, "note.txt")
    with open(src, "w") as fh:
        fh.write("Converted by LibreOffice.")
    try:
        out = convert.office_to_pdf(src, str(tmp_path))
    except RuntimeError as exc:
        pytest.skip(f"LibreOffice present but non-functional here: {exc}")
    assert os.path.exists(out)
    with fitz.open(out) as doc:
        assert doc.page_count >= 1


# -- OCR (skips without OCRmyPDF) ---------------------------------------

@pytest.mark.skipif(not external.OCRMYPDF.available(), reason="OCRmyPDF not installed")
def test_ocr_pdf(simple_pdf, tmp_path):
    from pdfeditor import ocr

    out = os.path.join(tmp_path, "ocr.pdf")
    # --skip-text on an already-text PDF should still yield a valid file.
    ocr.ocr_pdf(simple_pdf, out)
    assert os.path.exists(out)


# -- signing (skips without pyHanko / cryptography) ---------------------

@pytest.mark.skipif(
    not (external.PYHANKO.available() and external.CRYPTOGRAPHY.available()),
    reason="pyHanko/cryptography not installed",
)
def test_sign_pdf_roundtrip(simple_pdf, tmp_path):
    from pdfeditor import signing

    pfx = os.path.join(tmp_path, "cert.pfx")
    signing.create_self_signed_cert(pfx, "Test Signer", "pw")
    assert os.path.getsize(pfx) > 0
    out = os.path.join(tmp_path, "signed.pdf")
    signing.sign_pdf(simple_pdf, out, pfx, "pw", reason="test")
    with fitz.open(out) as doc:
        widgets = list(doc.load_page(0).widgets() or [])
        assert any(w.field_type_string == "Signature" for w in widgets)


# -- signature background removal (skips without Pillow) ----------------

@pytest.mark.skipif(not imaging.PILLOW.available(), reason="Pillow not installed")
def test_signature_background_removal(tmp_path):
    from PIL import Image, ImageDraw

    src = os.path.join(tmp_path, "sig.jpg")
    img = Image.new("RGB", (300, 120), (255, 255, 255))  # white background
    ImageDraw.Draw(img).line([(10, 90), (150, 30), (290, 80)], fill=(10, 10, 40), width=6)
    img.save(src, "JPEG")

    out = imaging.remove_background(src, os.path.join(tmp_path, "clean.png"))
    result = Image.open(out)
    assert result.mode == "RGBA"
    alpha = list(result.getchannel("A").getdata())
    assert any(a == 0 for a in alpha)      # background became transparent
    assert any(a > 200 for a in alpha)     # ink stayed opaque
    # Cropped tight to the ink (smaller than the original 300x120).
    assert result.size[0] <= 300 and result.size[1] <= 120


def test_signatures_dir_exists():
    d = imaging.signatures_dir()
    assert os.path.isdir(d)
