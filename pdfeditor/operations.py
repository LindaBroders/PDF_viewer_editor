"""File-level PDF operations that work on paths rather than an open document.

These cover the "Create & convert" and "Organise" batch features: combining
files, converting images to PDF, splitting, optimizing, and comparing. Kept
GUI-free and importable without Qt so they can be unit-tested headlessly.
"""

from __future__ import annotations

import difflib
import os
import re

import pymupdf as fitz

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif", ".pnm", ".webp"}


def _insert_any(out: fitz.Document, path: str) -> None:
    """Append a file (PDF, image, or anything fitz can open) into ``out``."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        with fitz.open(path) as img:
            pdf_bytes = img.convert_to_pdf()
        with fitz.open("pdf", pdf_bytes) as src:
            out.insert_pdf(src)
        return
    with fitz.open(path) as src:
        if src.is_pdf:
            out.insert_pdf(src)
        else:
            pdf_bytes = src.convert_to_pdf()
            with fitz.open("pdf", pdf_bytes) as converted:
                out.insert_pdf(converted)


def merge_files(paths: list[str], out_path: str) -> str:
    """Combine multiple files (PDFs and/or images) into a single PDF."""
    if not paths:
        raise ValueError("No input files given.")
    out = fitz.open()
    try:
        for path in paths:
            _insert_any(out, path)
        out.save(out_path, garbage=4, deflate=True)
    finally:
        out.close()
    return out_path


def images_to_pdf(image_paths: list[str], out_path: str) -> str:
    """Create a PDF with one image per page."""
    if not image_paths:
        raise ValueError("No images given.")
    out = fitz.open()
    try:
        for path in image_paths:
            with fitz.open(path) as img:
                rect = img[0].rect
                pdf_bytes = img.convert_to_pdf()
            with fitz.open("pdf", pdf_bytes) as src:
                out.insert_pdf(src)
        out.save(out_path, garbage=4, deflate=True)
    finally:
        out.close()
    return out_path


def optimize(in_path: str, out_path: str) -> tuple[int, int]:
    """Rewrite a PDF with maximum compression. Returns (old_size, new_size)."""
    old_size = os.path.getsize(in_path)
    with fitz.open(in_path) as doc:
        doc.save(
            out_path,
            garbage=4,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            clean=True,
        )
    return old_size, os.path.getsize(out_path)


def split_by_page_count(path: str, pages_per_file: int, out_dir: str) -> list[str]:
    """Split into chunks of ``pages_per_file`` pages each."""
    if pages_per_file < 1:
        raise ValueError("pages_per_file must be >= 1")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    outputs: list[str] = []
    with fitz.open(path) as src:
        total = src.page_count
        part = 1
        for start in range(0, total, pages_per_file):
            end = min(start + pages_per_file - 1, total - 1)
            chunk = fitz.open()
            chunk.insert_pdf(src, from_page=start, to_page=end)
            out = os.path.join(out_dir, f"{base}_part{part}.pdf")
            chunk.save(out)
            chunk.close()
            outputs.append(out)
            part += 1
    return outputs


def split_by_bookmarks(path: str, out_dir: str) -> list[str]:
    """Split at each top-level (level 1) bookmark boundary."""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    outputs: list[str] = []
    with fitz.open(path) as src:
        tops = [t for t in src.get_toc() if t[0] == 1]
        if not tops:
            return []
        starts = [(t[1], t[2] - 1) for t in tops]  # (title, start page index)
        for idx, (title, start) in enumerate(starts):
            end = starts[idx + 1][1] - 1 if idx + 1 < len(starts) else src.page_count - 1
            if end < start:
                end = start
            chunk = fitz.open()
            chunk.insert_pdf(src, from_page=start, to_page=end)
            safe = re.sub(r"[^\w\-. ]", "_", title).strip() or f"section{idx + 1}"
            out = os.path.join(out_dir, f"{base}_{idx + 1:02d}_{safe}.pdf")
            chunk.save(out)
            chunk.close()
            outputs.append(out)
    return outputs


def split_by_size(path: str, max_bytes: int, out_dir: str) -> list[str]:
    """Split so each output file stays under ``max_bytes`` (best effort).

    A single page larger than the limit becomes its own file.
    """
    if max_bytes < 1024:
        raise ValueError("max_bytes is unreasonably small.")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    outputs: list[str] = []
    with fitz.open(path) as src:
        total = src.page_count
        i = 0
        part = 1
        while i < total:
            chunk = fitz.open()
            j = i
            while j < total:
                chunk.insert_pdf(src, from_page=j, to_page=j)
                too_big = len(chunk.tobytes(garbage=1, deflate=True)) > max_bytes
                if too_big and j > i:
                    chunk.delete_page(chunk.page_count - 1)
                    break
                j += 1
            out = os.path.join(out_dir, f"{base}_part{part}.pdf")
            chunk.save(out)
            chunk.close()
            outputs.append(out)
            i = j if j > i else i + 1
            part += 1
    return outputs


def compare_text(path_a: str, path_b: str) -> str:
    """Produce a unified-diff change report between two PDFs' text."""
    def text_of(p: str) -> list[str]:
        with fitz.open(p) as doc:
            joined = "\n".join(
                doc.load_page(i).get_text() for i in range(doc.page_count)
            )
        return joined.splitlines()

    diff = difflib.unified_diff(
        text_of(path_a),
        text_of(path_b),
        fromfile=os.path.basename(path_a),
        tofile=os.path.basename(path_b),
        lineterm="",
    )
    report = "\n".join(diff)
    return report or "No text differences found."
