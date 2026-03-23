# src/ai/digest_titles.py
# Batch Groq pass to shorten long headlines before caching digest sections.

from __future__ import annotations

import copy
import logging

from src.ai.groq_client import groq_client
from src.ai.prompt_builder import build_digest_title_shorten_prompt, parse_shortened_titles
from src.utils.config_loader import CONFIG

logger = logging.getLogger(__name__)

_BATCH_SIZE = 8


def _title_threshold_chars() -> int:
    raw = CONFIG.get("digest", {}).get("title_char_threshold", 120)
    try:
        return max(40, min(int(raw), 500))
    except (TypeError, ValueError):
        return 120


async def shorten_long_titles_in_sections(sections: list[dict]) -> list[dict]:
    """Add ``display_title`` to items whose ``title`` exceeds the configured length.

    Calls Groq once per batch of long titles (same digest run). On failure, returns
    sections unchanged (still deep-copied).

    Args:
        sections: Output of ``DigestBuilder.build_sections`` (list of category blocks).

    Returns:
        New list with optional ``display_title`` on long-title items.
    """
    threshold = _title_threshold_chars()
    out = copy.deepcopy(sections)

    long_entries: list[tuple[int, int, str]] = []
    for si, sec in enumerate(out):
        for ii, item in enumerate(sec.get("items", [])):
            t = (item.get("title") or "").strip()
            if len(t) > threshold:
                long_entries.append((si, ii, t))

    if not long_entries:
        return out

    batches = (len(long_entries) + _BATCH_SIZE - 1) // _BATCH_SIZE
    for batch_idx in range(batches):
        start = batch_idx * _BATCH_SIZE
        chunk = long_entries[start : start + _BATCH_SIZE]
        numbered = [(j + 1, title) for j, (_, _, title) in enumerate(chunk)]
        messages = [
            {
                "role": "system",
                "content": (
                    "You shorten news headlines for a Telegram digest on the Celo blockchain "
                    "ecosystem. Respond only with valid JSON as instructed."
                ),
            },
            {"role": "user", "content": build_digest_title_shorten_prompt(numbered)},
        ]
        try:
            raw = await groq_client.generate(messages, max_tokens=768)
        except Exception as exc:
            logger.warning(
                "[DIGEST_TITLES] Groq batch %d/%d failed: %s",
                batch_idx + 1,
                batches,
                exc,
                exc_info=True,
            )
            continue

        parsed = parse_shortened_titles(raw)
        for j, (si, ii, _orig) in enumerate(chunk):
            lid = j + 1
            short = parsed.get(lid)
            if short and isinstance(short, str):
                short = short.strip().replace("\n\n", "\n")[:240]
                if short:
                    out[si]["items"][ii]["display_title"] = short

    shortened = sum(
        1
        for sec in out
        for it in sec.get("items", [])
        if it.get("display_title")
    )
    logger.info(
        "[DIGEST_TITLES] Processed %d long headline(s) in %d batch(es) | %d shortened",
        len(long_entries),
        batches,
        shortened,
    )
    return out
