"""Visual channel: screenshots plus a perceptual hash.

Screenshots are stored as artifacts and reduced to a difference hash so that
state similarity can use visual evidence without keeping images in the database.
Images are a supplement to DOM and accessibility evidence, never the sole truth.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from blackbox.browser.context import PageContext

try:  # Pillow is used when present; hashing degrades to a content digest otherwise.
    from PIL import Image
except Exception:  # noqa: BLE001 - optional dependency
    Image = None  # type: ignore[assignment]


@dataclass
class ScreenshotResult:
    ref: str | None
    path: Path | None
    hash: str
    width: int
    height: int
    available: bool
    note: str = ""


def hamming(a: str, b: str) -> int:
    """Bit distance between two hex-encoded hashes of equal length."""
    if not a or not b or len(a) != len(b):
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _dhash(image: "Image.Image", size: int = 8) -> str:
    small = image.convert("L").resize((size + 1, size), Image.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    index = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            bits |= (1 if left > right else 0) << index
            index += 1
    return f"{bits:016x}"


def fingerprint_image(data: bytes) -> tuple[str, int, int, str]:
    if not data:
        return "", 0, 0, "empty screenshot"
    if Image is None:
        return hashlib.sha1(data).hexdigest()[:16], 0, 0, "pillow unavailable: content digest used"
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            # A completely uniform frame carries no structural information.
            extrema = image.convert("L").getextrema()
            uniform = extrema[0] == extrema[1]
            return _dhash(image), width, height, ("uniform frame" if uniform else "")
    except Exception as exc:  # noqa: BLE001 - corrupt frame must not break observation
        return hashlib.sha1(data).hexdigest()[:16], 0, 0, f"image decode failed: {exc}"


async def capture_screenshot(
    page: PageContext,
    artifacts_dir: Path,
    *,
    full_page: bool = False,
    prefix: str = "obs",
) -> ScreenshotResult:
    """Capture, persist and hash a screenshot of the current page."""
    artifacts_dir = Path(artifacts_dir)
    shots = artifacts_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    try:
        data = await page.screenshot(full_page=full_page)
    except Exception as exc:  # noqa: BLE001 - visual channel is optional
        return ScreenshotResult(None, None, "", 0, 0, False, f"screenshot unavailable: {exc}")

    digest = hashlib.sha1(data).hexdigest()[:16]
    path = shots / f"{prefix}-{digest}.png"
    if not path.exists():
        path.write_bytes(data)
    image_hash, width, height, note = fingerprint_image(data)
    return ScreenshotResult(
        ref=str(path),
        path=path,
        hash=image_hash,
        width=width,
        height=height,
        available=True,
        note=note,
    )
