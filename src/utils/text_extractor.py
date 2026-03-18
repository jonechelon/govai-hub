from __future__ import annotations

from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.utils.logger import logger


FALLBACK_TEXT = "Description text unavailable. Please visit the URL for details."
# Hard limit to protect LLM context windows (see P54).
MAX_TEXT_LENGTH = 8000


def _to_github_raw_url(url: str) -> str:
    """Convert GitHub blob URLs to raw URLs when possible.

    This avoids downloading the GitHub HTML UI (CSS/JS-heavy) and fetches the
    underlying file content instead.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    if host not in {"github.com", "www.github.com"}:
        return url

    parts = [p for p in path.split("/") if p]
    # Expected: /{owner}/{repo}/blob/{ref}/{path...}
    # We look for the first "blob" segment to be resilient to extra segments.
    try:
        blob_idx = parts.index("blob")
    except ValueError:
        return url

    # Need at least: owner, repo, blob, ref, file_path...
    if blob_idx < 2 or blob_idx + 2 >= len(parts):
        return url

    owner = parts[0]
    repo = parts[1]
    ref = parts[blob_idx + 1]
    remaining_path = "/".join(parts[blob_idx + 2 :])
    if not remaining_path:
        return url

    scheme = parsed.scheme or "https"
    raw_url = f"{scheme}://raw.githubusercontent.com/{owner}/{repo}/{ref}/{remaining_path}"
    return raw_url


def _is_github_raw_url(url: str) -> bool:
    """Return True if the URL points to raw GitHub content."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return (parsed.netloc or "").lower() == "raw.githubusercontent.com"


def _extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML using BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def extract_proposal_text(url: str) -> str:
    """Fetch and return a cleaned proposal description text.

    The function is designed to be resilient: on any HTTP or parsing error it logs
    the issue and returns a safe fallback string instead of raising.

    Args:
        url: The URL pointing to the on-chain proposal description (forum, GitHub, IPFS, etc.).

    Returns:
        A cleaned, human-readable text limited to MAX_TEXT_LENGTH characters.
    """
    if not url:
        logger.warning("[TEXT_EXTRACTOR] Empty URL received for proposal text extraction")
        return FALLBACK_TEXT

    normalized_url = _to_github_raw_url(url)
    if normalized_url != url:
        logger.info(
            "[TEXT_EXTRACTOR] Converted GitHub blob URL to raw content URL | from=%s | to=%s",
            url,
            normalized_url,
        )

    try:
        response = requests.get(normalized_url, timeout=5)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning(
            "[TEXT_EXTRACTOR] Failed to fetch proposal description from URL: %s | error: %s",
            normalized_url,
            exc,
        )
        return FALLBACK_TEXT

    raw_text = response.text or ""
    if not raw_text.strip():
        cleaned_text = ""
    elif _is_github_raw_url(normalized_url):
        cleaned_text = raw_text.strip()
    else:
        cleaned_text = _extract_text_from_html(raw_text)

    if not cleaned_text:
        logger.info(
            "[TEXT_EXTRACTOR] Empty or unreadable content from URL, returning fallback | url=%s",
            normalized_url,
        )
        return FALLBACK_TEXT

    if len(cleaned_text) > MAX_TEXT_LENGTH:
        truncated_text = cleaned_text[:MAX_TEXT_LENGTH].rstrip()
        return f"{truncated_text}..."

    return cleaned_text

