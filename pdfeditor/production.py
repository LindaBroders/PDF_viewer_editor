"""Print-production preflight checks.

A lightweight, dependency-free preflight built on PyMuPDF: it inspects fonts,
images, and page geometry and reports issues that matter for printing and
archiving (non-embedded fonts, low-resolution images, inconsistent page sizes,
encryption). This is not a full PDF/X validator, but it catches the common
problems before a file goes to print.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf as fitz


@dataclass
class PreflightReport:
    page_count: int = 0
    pdf_version: str = ""
    encrypted: bool = False
    non_embedded_fonts: set = field(default_factory=set)
    low_res_images: list = field(default_factory=list)  # (page, dpi)
    rgb_images: int = 0
    page_sizes: set = field(default_factory=set)  # (w_mm, h_mm)
    warnings: list = field(default_factory=list)

    def as_text(self) -> str:
        lines = ["PREFLIGHT REPORT", "=" * 40, ""]
        lines.append(f"Pages: {self.page_count}")
        lines.append(f"PDF version: {self.pdf_version or 'unknown'}")
        lines.append(f"Encrypted: {'yes' if self.encrypted else 'no'}")
        lines.append("")

        if self.non_embedded_fonts:
            lines.append("⚠ Non-embedded fonts (may not print correctly):")
            for f in sorted(self.non_embedded_fonts):
                lines.append(f"    - {f}")
        else:
            lines.append("✓ All fonts embedded")

        if self.low_res_images:
            lines.append("")
            lines.append("⚠ Low-resolution images (< 150 DPI):")
            for page, dpi in self.low_res_images:
                lines.append(f"    - page {page + 1}: ~{dpi} DPI")
        else:
            lines.append("✓ No low-resolution images detected")

        if self.rgb_images:
            lines.append("")
            lines.append(
                f"ℹ {self.rgb_images} RGB image(s) — convert to CMYK for offset printing"
            )

        lines.append("")
        if len(self.page_sizes) > 1:
            sizes = ", ".join(f"{w}×{h}mm" for w, h in sorted(self.page_sizes))
            lines.append(f"⚠ Mixed page sizes: {sizes}")
        elif self.page_sizes:
            w, h = next(iter(self.page_sizes))
            lines.append(f"✓ Uniform page size: {w}×{h}mm")

        for w in self.warnings:
            lines.append(f"⚠ {w}")

        problems = (
            len(self.non_embedded_fonts)
            + len(self.low_res_images)
            + (1 if len(self.page_sizes) > 1 else 0)
        )
        lines.append("")
        lines.append("=" * 40)
        lines.append("No blocking issues found." if problems == 0
                     else f"{problems} issue(s) to review before printing.")
        return "\n".join(lines)


def preflight(path: str, min_dpi: int = 150) -> PreflightReport:
    """Run a preflight inspection on a PDF file."""
    report = PreflightReport()
    with fitz.open(path) as doc:
        report.page_count = doc.page_count
        report.encrypted = bool(doc.is_encrypted)
        meta = doc.metadata or {}
        report.pdf_version = meta.get("format", "")

        for i in range(doc.page_count):
            page = doc.load_page(i)
            rect = page.rect
            # Points -> mm (1 pt = 25.4/72 mm), rounded for grouping.
            w_mm = round(rect.width * 25.4 / 72)
            h_mm = round(rect.height * 25.4 / 72)
            report.page_sizes.add((w_mm, h_mm))

            for font in page.get_fonts(full=True):
                # font tuple: (xref, ext, type, basefont, name, encoding, ...)
                basefont = font[3]
                is_embedded = bool(font[1])  # ext is empty when not embedded
                if not is_embedded:
                    report.non_embedded_fonts.add(basefont or "(unnamed)")

            for img in page.get_images(full=True):
                xref = img[0]
                width_px = img[2]
                try:
                    info = doc.extract_image(xref)
                except Exception:
                    continue
                cs = info.get("colorspace", 0)
                if cs and cs >= 3:
                    report.rgb_images += 1
                # Estimate DPI from displayed size vs pixel width.
                rects = page.get_image_rects(xref)
                if rects and width_px:
                    display_w_in = rects[0].width / 72.0
                    if display_w_in > 0:
                        dpi = round(width_px / display_w_in)
                        if dpi < min_dpi:
                            report.low_res_images.append((i, dpi))
    return report
