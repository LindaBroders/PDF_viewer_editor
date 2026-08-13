"""Detection of optional external tools and their install hints.

Several advanced features (Office conversion, OCR, PDF/A, signing, AI) depend
on tools or Python packages that aren't part of the base install. This module
centralizes "is it available?" checks and the Fedora/pip commands to get each
one, so the UI can show a helpful message instead of a raw traceback.
"""

from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Dependency:
    """An optional dependency: how to detect it and how to install it."""

    name: str
    kind: str  # "binary" or "python"
    probe: str  # executable name or importable module
    install_hint: str

    def available(self) -> bool:
        if self.kind == "binary":
            return shutil.which(self.probe) is not None
        return importlib.util.find_spec(self.probe) is not None


# The optional dependencies used across the app.
LIBREOFFICE = Dependency(
    "LibreOffice", "binary", "soffice",
    "Install with:  sudo dnf install libreoffice",
)
OCRMYPDF = Dependency(
    "OCRmyPDF", "binary", "ocrmypdf",
    "Install with:  sudo dnf install ocrmypdf tesseract",
)
TESSERACT = Dependency(
    "Tesseract", "binary", "tesseract",
    "Install with:  sudo dnf install tesseract",
)
GHOSTSCRIPT = Dependency(
    "Ghostscript", "binary", "gs",
    "Install with:  sudo dnf install ghostscript",
)
PYHANKO = Dependency(
    "pyHanko", "python", "pyhanko",
    "Install with:  pip install 'pyhanko[pkcs11,image-support]'",
)
CRYPTOGRAPHY = Dependency(
    "cryptography", "python", "cryptography",
    "Install with:  pip install cryptography",
)
ANTHROPIC = Dependency(
    "Anthropic SDK", "python", "anthropic",
    "Install with:  pip install anthropic  (and set ANTHROPIC_API_KEY)",
)


class DependencyError(Exception):
    """Raised when an optional dependency is missing; message includes the hint."""

    def __init__(self, dep: Dependency) -> None:
        super().__init__(
            f"{dep.name} is required for this feature but was not found.\n\n{dep.install_hint}"
        )
        self.dependency = dep


def require(dep: Dependency) -> None:
    """Raise :class:`DependencyError` if ``dep`` is unavailable."""
    if not dep.available():
        raise DependencyError(dep)
