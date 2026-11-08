# app/images.py
"""Re-encoding for every image that ends up in upload/.

Each image is decoded and written again as WebP: the EXIF orientation is
applied first, then all metadata (EXIF including GPS, XMP, comments) is
dropped. Only the ICC colour profile is kept, so colours stay right. Input
that does not decode is rejected and never stored as-is.
"""

import io
from typing import Optional, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PIXELS = 100_000_000


class ImageRejected(ValueError):
    """The upload is not an image we can (or want to) decode."""


def sanitize_to_webp(
    raw: bytes, *, quality: int = 90, max_edge: Optional[int] = None
) -> Tuple[bytes, int, int]:
    """Return (webp_bytes, width, height) with orientation applied, metadata gone."""
    try:
        source = Image.open(io.BytesIO(raw))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ImageRejected("Not a readable image") from exc

    with source:
        width, height = source.size
        if width * height > MAX_PIXELS:
            raise ImageRejected(f"Image too large ({width}x{height})")
        try:
            source.seek(0)  # First frame of animations
            source.load()
            icc_profile = source.info.get("icc_profile") if source.mode != "CMYK" else None
            image = ImageOps.exif_transpose(source)
            has_alpha = image.mode in ("RGBA", "LA", "PA") or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            if has_alpha and image.getchannel("A").getextrema()[0] == 255:
                image = image.convert("RGB")
                has_alpha = False
            if max_edge:
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        except (OSError, ValueError, SyntaxError) as exc:
            raise ImageRejected("Image could not be decoded") from exc

    # Converted images copy info (exif, xmp, comments); none of it may reach disk.
    image.info = {}
    params = {"format": "WEBP", "method": 6}
    if has_alpha:
        params["lossless"] = True
    else:
        params["quality"] = quality
    if icc_profile:
        params["icc_profile"] = icc_profile

    output = io.BytesIO()
    image.save(output, **params)
    return output.getvalue(), image.width, image.height
