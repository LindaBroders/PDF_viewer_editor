"""OCR and scan cleanup via OCRmyPDF (Tesseract), plus PDF/A conversion.

OCRmyPDF wraps Tesseract and Ghostscript to add an invisible, searchable text
layer to scanned PDFs and can also deskew, clean, and rotate pages. When
``output_type="pdfa"`` it additionally produces an archival PDF/A file.
"""

from __future__ import annotations

import os
import subprocess

from .external import OCRMYPDF, require


def ocr_pdf(
    input_path: str,
    output_path: str,
    language: str = "eng",
    deskew: bool = False,
    clean: bool = False,
    rotate: bool = False,
    force: bool = False,
    output_type: str = "pdf",
) -> str:
    """Add a searchable text layer to a PDF (optionally cleaning the scan).

    - ``language``: Tesseract language code(s), e.g. ``"eng"`` or ``"eng+deu"``.
    - ``deskew``: straighten skewed pages.
    - ``clean``: clean the image before OCR (does not alter the output image).
    - ``rotate``: auto-rotate pages to the correct orientation.
    - ``force``: re-OCR even if the PDF already has text.
    - ``output_type``: ``"pdf"`` or ``"pdfa"`` (archival).

    Raises :class:`~pdfeditor.external.DependencyError` if OCRmyPDF is missing.
    """
    require(OCRMYPDF)
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    cmd = ["ocrmypdf", "--language", language, "--output-type", output_type]
    if deskew:
        cmd.append("--deskew")
    if clean:
        cmd.append("--clean")
    if rotate:
        cmd.append("--rotate-pages")
    # Skip pages that already have text unless forced; otherwise OCRmyPDF errors.
    cmd.append("--force-ocr" if force else "--skip-text")
    cmd += [input_path, output_path]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"OCR failed:\n{result.stderr or result.stdout}")
    return output_path


def to_pdfa(input_path: str, output_path: str, language: str = "eng") -> str:
    """Convert a PDF to archival PDF/A (runs OCR only where text is missing)."""
    return ocr_pdf(
        input_path, output_path, language=language, output_type="pdfa", force=False
    )
