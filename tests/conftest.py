"""Test setup: settings come from the environment, so set it before app imports.

Run from the blog directory (the app uses cwd-relative template paths):
    python -m pytest tests
"""

import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

TMP = Path(tempfile.mkdtemp(prefix="blog-tests-"))
for name in ("content", "upload", "build"):
    (TMP / name).mkdir()

os.environ.update(
    {
        "CONTENT_DIR": str(TMP / "content"),
        "LOCAL_UPLOAD_PATH": str(TMP / "upload"),
        "LOCAL_BUILD_PATH": str(TMP / "build"),
        "ABOUT_PAGE_FROM_BIO_ENABLED": "false",
        "SITE_URL": "https://blog.example",
        "UPLOAD_METHOD": "local",
        "SERVER_FTP_ENABLED": "false",
        "ENABLE_FILE_MONITORING": "false",
        "PROXY_SECRET": "",
        "INGEST_TOKEN_SHA256": "",
    }
)


def make_jpeg(
    size=(300, 200), color=(200, 30, 30), orientation=None, marker="SECRET-MARKER"
) -> bytes:
    """A JPEG with EXIF (description marker, optional orientation) and a comment."""
    from PIL import Image

    image = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010E] = marker  # ImageDescription
    exif[0x8298] = marker  # Copyright
    if orientation:
        exif[0x0112] = orientation
    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif.tobytes(), comment=marker.encode())
    return output.getvalue()


@pytest.fixture
def jpeg():
    return make_jpeg
