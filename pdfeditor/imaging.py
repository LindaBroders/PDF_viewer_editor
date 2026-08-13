"""Image processing for signatures and initials.

Turns a photo or scan of a handwritten signature (dark ink on light paper)
into a clean transparent PNG that can be stamped onto a PDF. The background is
removed by keying out near-white pixels, with a soft edge so the strokes stay
smooth.
"""

from __future__ import annotations

import os

from .external import Dependency, require

PILLOW = Dependency(
    "Pillow", "python", "PIL",
    "Install with:  pip install Pillow",
)


def remove_background(
    input_path: str,
    output_path: str,
    threshold: int = 245,
    softness: int = 45,
    recolor: tuple[int, int, int] | None = None,
) -> str:
    """Make a signature's light background transparent.

    - ``threshold``: brightness (0-255) at/above which a pixel is treated as
      background and made fully transparent. Higher keeps more of the image.
    - ``softness``: width of the anti-aliasing band below the threshold, so
      stroke edges fade smoothly instead of looking jagged.
    - ``recolor``: optional (r, g, b) to force the ink color (e.g. pure black
      or blue). ``None`` keeps the original colors.

    Returns the output path (always a PNG so transparency is preserved).
    """
    require(PILLOW)
    from PIL import Image

    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)

    img = Image.open(input_path).convert("RGBA")
    gray = img.convert("L")

    high = max(1, min(255, threshold))
    low = max(0, high - max(1, softness))

    # Build the alpha channel from brightness: dark ink -> opaque, light -> clear.
    def to_alpha(p: int) -> int:
        if p >= high:
            return 0
        if p <= low:
            return 255
        return int(255 * (high - p) / (high - low))

    alpha = gray.point(to_alpha)

    if recolor is not None:
        solid = Image.new("RGBA", img.size, (*recolor, 0))
        solid.putalpha(alpha)
        result = solid
    else:
        result = img.copy()
        result.putalpha(alpha)

    # Trim fully-transparent margins so the stamp sits tight to the ink.
    bbox = result.getbbox()
    if bbox:
        result = result.crop(bbox)

    if not output_path.lower().endswith(".png"):
        output_path += ".png"
    result.save(output_path, "PNG")
    return output_path


def signatures_dir() -> str:
    """Return (creating if needed) the folder where saved signatures live."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    path = os.path.join(base, "pdfeditor", "signatures")
    os.makedirs(path, exist_ok=True)
    return path


def list_signatures() -> list[str]:
    """List saved signature PNGs, newest first."""
    d = signatures_dir()
    files = [
        os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".png")
    ]
    return sorted(files, key=os.path.getmtime, reverse=True)
