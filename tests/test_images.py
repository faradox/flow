import io

import pytest
from PIL import Image

from app.images import ImageRejected, sanitize_to_webp


def reopen(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def test_strips_exif_and_comment(jpeg):
    out, _, _ = sanitize_to_webp(jpeg())
    assert b"SECRET-MARKER" not in out
    image = reopen(out)
    assert image.format == "WEBP"
    assert not image.getexif()
    assert "exif" not in image.info and "xmp" not in image.info


def test_applies_orientation_before_stripping(jpeg):
    out, width, height = sanitize_to_webp(jpeg(size=(300, 200), orientation=6))
    assert (width, height) == (200, 300)
    assert reopen(out).size == (200, 300)


def test_downscales_to_max_edge(jpeg):
    out, width, height = sanitize_to_webp(jpeg(size=(3000, 1000)), max_edge=2400)
    assert (width, height) == (2400, 800)
    assert reopen(out).size == (2400, 800)


def test_webp_input_is_reencoded_too(jpeg):
    source = Image.open(io.BytesIO(jpeg()))
    exif = source.getexif()
    buffer = io.BytesIO()
    source.save(buffer, format="WEBP", exif=exif.tobytes())
    assert b"SECRET-MARKER" in buffer.getvalue()

    out, _, _ = sanitize_to_webp(buffer.getvalue())
    assert b"SECRET-MARKER" not in out


def test_keeps_real_transparency_lossless():
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    out, _, _ = sanitize_to_webp(buffer.getvalue())
    assert reopen(out).mode == "RGBA"


def test_rejects_garbage():
    with pytest.raises(ImageRejected):
        sanitize_to_webp(b"definitely not an image")


def test_rejects_huge_dimensions(monkeypatch, jpeg):
    monkeypatch.setattr("app.images.MAX_PIXELS", 1000)
    with pytest.raises(ImageRejected):
        sanitize_to_webp(jpeg(size=(100, 100)))
