# app/ingest.py
"""Photo ingest API: one picture plus its tags becomes one blog post.

Called by the image tagging tool's "post on blog" button. nginx passes
/api/ingest/ without the proxy auth; the caller authenticates with a bearer token
whose SHA-256 is INGEST_TOKEN_SHA256. The token can create posts through this
endpoint and nothing else: no edit, delete, listing or analytics.

    POST /api/ingest/photo   multipart/form-data
      image   file, required (JPEG, PNG, WebP, AVIF, GIF, ...)
      tags    repeatable field and/or comma-separated
      title   optional, defaults to the date
      body    optional plain text, blank lines separate paragraphs
      status  "private" (unlisted, the default) or "public"

    201 {"result": "created", ...}  new post
    200 {"result": "exists", ...}   this exact image was posted before
    4xx {"error": "..."}            always JSON

The server picks the slug and file name; the image is re-encoded without
metadata (app/images.py) and downscaled to INGEST_MAX_EDGE.
"""

import asyncio
import hashlib
import hmac
import html
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.images import ImageRejected, sanitize_to_webp
from app.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

STATUSES = {"private", "public"}
MAX_TAGS = 30
MAX_TAG_LENGTH = 40
MAX_TITLE_LENGTH = 200
MAX_BODY_LENGTH = 20_000

# One post at a time: the duplicate check and the write must not interleave.
_lock = asyncio.Lock()


class IngestError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def check_token(authorization: str) -> None:
    expected = settings.ingest_token_sha256.strip().lower()
    if not expected:
        raise IngestError(503, "Ingest is not configured")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise IngestError(401, "Bearer token required")
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(digest, expected):
        raise IngestError(401, "Invalid token")


def normalize_tags(values: Iterable[str]) -> List[str]:
    """Lowercase, spaces to dashes, letters/digits/_/- only, deduplicated."""
    tags: List[str] = []
    for value in values:
        for part in str(value).split(","):
            tag = re.sub(r"\s+", "-", part.strip().lower())
            tag = re.sub(r"[^\w-]", "", tag)
            tag = re.sub(r"-{2,}", "-", tag).strip("-_")[:MAX_TAG_LENGTH]
            if tag and tag not in tags:
                tags.append(tag)
    if len(tags) > MAX_TAGS:
        raise IngestError(400, f"At most {MAX_TAGS} tags")
    return tags


def clean_title(value: str, now: datetime) -> str:
    """One line, no characters that break the quoted YAML title."""
    title = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")
    title = re.sub(r'["\\]', "", title)
    title = re.sub(r"\s+", " ", title).strip()[:MAX_TITLE_LENGTH]
    return title or f"{now.day} {now:%B %Y}"


def make_slug(title: str, now: datetime, digest: str) -> str:
    """Same shape as the editor's file names: date prefix, then the title."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:60].strip("-")
    return f"{now:%Y-%m-%d}-{words or 'photo'}-{digest[:8]}"


def render_content(image_url: str, title: str, body: str, width: int, height: int) -> str:
    alt = html.escape(title, quote=True)
    parts = [
        '<div class="medium-insert-images"><figure>'
        f'<img src="{html.escape(image_url, quote=True)}" alt="{alt}" '
        f'width="{width}" height="{height}">'
        "</figure></div>"
    ]
    for paragraph in re.split(r"\n\s*\n", body.strip()):
        if paragraph.strip():
            lines = [html.escape(line.strip()) for line in paragraph.strip().splitlines()]
            parts.append("<p>" + "<br>".join(lines) + "</p>")
    return "\n".join(parts)


def find_by_digest(content_manager, digest: str):
    marker = f"sha256:{digest}"
    for page in content_manager.pages.values():
        if page.metadata.source_sha256 == marker:
            return page
    return None


def post_payload(result: str, path: str, status: str, image_url: str) -> dict:
    site_url = settings.site_url.rstrip("/")
    return {
        "result": result,
        "path": path,
        "url": f"{site_url}/p/{path}/",
        "status": status,
        "image_url": f"{site_url}{image_url}" if image_url.startswith("/") else image_url,
    }


def _form_text(form, key: str, limit: int) -> str:
    value = form.get(key) or ""
    if not isinstance(value, str):
        raise IngestError(400, f"'{key}' must be a text field")
    if len(value) > limit:
        raise IngestError(400, f"'{key}' is longer than {limit} characters")
    return value


def _image_url(filename: str) -> str:
    prefix = settings.site_path_prefix.rstrip("/")
    if prefix and not prefix.startswith("/"):
        prefix = "/" + prefix
    return f"{prefix}/upload/{filename}"


@router.post("/photo")
async def ingest_photo(request: Request):
    try:
        check_token(request.headers.get("authorization", ""))
        return await _ingest(request)
    except IngestError as exc:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse({"error": exc.message}, status_code=exc.status_code, headers=headers)


async def _ingest(request: Request) -> JSONResponse:
    try:
        form = await request.form(max_files=1, max_fields=60)
    except Exception as exc:  # Malformed multipart, too many parts
        raise IngestError(400, f"Invalid form: {exc}")

    upload = form.get("image")
    if not isinstance(upload, UploadFile):
        raise IngestError(400, "'image' file is required")
    raw = await upload.read(settings.ingest_max_bytes + 1)
    if len(raw) > settings.ingest_max_bytes:
        raise IngestError(413, f"Image larger than {settings.ingest_max_bytes} bytes")
    if not raw:
        raise IngestError(400, "'image' is empty")

    now = datetime.now().replace(microsecond=0)
    tags = normalize_tags(v for v in form.getlist("tags") if isinstance(v, str))
    title = clean_title(_form_text(form, "title", MAX_TITLE_LENGTH * 2), now)
    body = _form_text(form, "body", MAX_BODY_LENGTH)
    status = (_form_text(form, "status", 20) or settings.ingest_default_status).strip().lower()
    if status not in STATUSES:
        raise IngestError(400, f"'status' must be one of {sorted(STATUSES)}")

    digest = hashlib.sha256(raw).hexdigest()
    content_manager = request.app.state.content_manager

    async with _lock:
        existing = find_by_digest(content_manager, digest)
        if existing is not None:
            image_url = _first_image_src(existing.content) or ""
            return JSONResponse(
                post_payload("exists", existing.path, existing.metadata.status, image_url)
            )

        try:
            webp, width, height = await run_in_threadpool(
                sanitize_to_webp, raw, quality=85, max_edge=settings.ingest_max_edge
            )
        except ImageRejected as exc:
            raise IngestError(415, str(exc))

        slug = make_slug(title, now, digest)
        if content_manager.get_page(slug) is not None:
            raise IngestError(409, f"A page named {slug} already exists")

        upload_dir = Path(settings.local_upload_path)
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{slug}.webp"
        image_path = upload_dir / filename
        try:
            with open(image_path, "xb") as handle:
                handle.write(webp)
        except FileExistsError:
            raise IngestError(409, f"Upload {filename} already exists")

        image_url = _image_url(filename)
        metadata = {
            "title": title,
            "date": now,
            "status": status,
            "source_sha256": f"sha256:{digest}",
        }
        if tags:
            metadata["tags"] = tags
        try:
            content_manager.save_page(
                slug, render_content(image_url, title, body, width, height), metadata
            )
        except Exception:
            image_path.unlink(missing_ok=True)
            logger.exception("Ingest: saving page %s failed", slug)
            raise IngestError(500, "Saving the post failed")

        request.app.state.ftp_uploader.delete_build_files(slug, set(tags))

    logger.info("Ingest: created %s (%s, %d tags)", slug, status, len(tags))
    return JSONResponse(post_payload("created", slug, status, image_url), status_code=201)


def _first_image_src(content: str) -> Optional[str]:
    match = re.search(r'<img[^>]+src="([^"]+)"', content)
    return html.unescape(match.group(1)) if match else None
