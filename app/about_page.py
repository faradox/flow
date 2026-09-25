from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


def _candidate_roots() -> Iterable[Path]:
    """Return likely project roots for local checkout and container runtime."""
    seen: set[Path] = set()
    starts = [Path.cwd(), Path(__file__).resolve()]

    for start in starts:
        for root in [start, *start.parents]:
            if root not in seen:
                seen.add(root)
                yield root


def _resolve_source_path(source: Path) -> Optional[Path]:
    if source.is_absolute():
        return source if source.exists() else None

    for root in _candidate_roots():
        candidate = root / source
        if candidate.exists():
            return candidate

    return None


def _render_about_markdown(settings: Any, bio: str) -> str:
    author_name = getattr(settings, "author_name", "")
    author = getattr(settings, "author", "")

    frontmatter = [
        "---",
        f"title: {settings.about_page_title}",
        f"date: {settings.about_page_date}",
        "status: public",
        "listed: false",
        "render_as_markdown: true",
    ]

    if author_name:
        frontmatter.append(f"author_name: {author_name}")
    if author:
        frontmatter.append(f"author: {author}")

    body = [bio.strip()]
    home_url = getattr(settings, "home_url", "")
    if home_url:
        home_host = urlparse(home_url).netloc or home_url
        body.append(f"More of my projects and ways to reach me: [{home_host}]({home_url})")

    frontmatter.extend(["---", "", "\n\n".join(body), ""])
    return "\n".join(frontmatter)


def sync_about_page_from_bio(settings: Any) -> Optional[Path]:
    """Generate the blog about page from the shared landing-page bio text."""
    if not getattr(settings, "about_page_from_bio_enabled", False):
        return None

    source = _resolve_source_path(Path(settings.about_page_source))
    if source is None:
        logger.warning("About bio source not found: %s", settings.about_page_source)
        return None

    content_dir = Path(settings.content_dir)
    content_dir.mkdir(parents=True, exist_ok=True)

    target = content_dir / f"{settings.about_page_slug}.md"
    bio = source.read_text(encoding="utf-8")
    markdown = _render_about_markdown(settings, bio)

    if target.exists() and target.read_text(encoding="utf-8") == markdown:
        return target

    target.write_text(markdown, encoding="utf-8")
    logger.info("Synced about page from shared bio: %s", target)
    return target
