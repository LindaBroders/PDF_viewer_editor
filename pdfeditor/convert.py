"""Convert Office documents (and other formats) to PDF via LibreOffice.

Shells out to a headless ``soffice`` instance. This covers Word, Excel,
PowerPoint, ODF, RTF, and plain text — anything LibreOffice can open.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

from .external import LIBREOFFICE, require

# Extensions LibreOffice handles well. Not exhaustive, but covers the common set.
OFFICE_EXTS = {
    ".doc", ".docx", ".odt", ".rtf", ".txt",
    ".xls", ".xlsx", ".ods", ".csv",
    ".ppt", ".pptx", ".odp",
}


def office_to_pdf(input_path: str, out_dir: str | None = None) -> str:
    """Convert an Office document to PDF. Returns the output PDF path.

    ``out_dir`` defaults to the input file's directory. Raises
    :class:`~pdfeditor.external.DependencyError` if LibreOffice is missing.
    """
    require(LIBREOFFICE)
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    out_dir = out_dir or os.path.dirname(os.path.abspath(input_path))
    os.makedirs(out_dir, exist_ok=True)

    # Use an isolated profile dir so a running LibreOffice doesn't block us.
    with tempfile.TemporaryDirectory() as profile:
        cmd = [
            "soffice",
            "--headless",
            "--norestore",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to", "pdf",
            "--outdir", out_dir,
            input_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice conversion failed:\n{result.stderr or result.stdout}"
        )

    base = os.path.splitext(os.path.basename(input_path))[0]
    out_path = os.path.join(out_dir, base + ".pdf")
    if not os.path.exists(out_path):
        # soffice exits 0 even on failure, so trust the output file, not the code.
        detail = (result.stdout or "") + (result.stderr or "")
        raise RuntimeError(
            "LibreOffice did not produce a PDF. It reported:\n"
            + (detail.strip() or "(no output)")
        )
    return out_path
