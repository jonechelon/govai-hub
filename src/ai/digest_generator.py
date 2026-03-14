"""
DigestGenerator: singleton that orchestrates the full digest pipeline —
raw snapshot → context → prompt → Groq → result dict and cache.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.ai.digest_builder import DigestBuilder
from src.ai.groq_client import groq_client
from src.ai.prompt_builder import prompt_builder
from src.fetchers.fetcher_manager import fetcher_manager

logger = logging.getLogger(__name__)

DIGEST_CACHE_DIR = Path("data/cache")
MAX_TOKENS_DIGEST = 600


class DigestGenerator:
    """Singleton orchestrator: fetch snapshot → build context → prompt → Groq → cache.

    The only module that knows the full pipeline. Notifier (P21), /digest (P22)
    and /ask (P26) import digest_generator only — they never touch fetchers or
    builders directly.
    """

    _instance: "DigestGenerator | None" = None

    def __new__(cls) -> "DigestGenerator":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._builder = DigestBuilder()
        return cls._instance

    async def generate_digest(
        self,
        template: str = "daily",
        user_apps_by_category: dict[str, list[str]] | None = None,
    ) -> dict:
        """Run the full pipeline: fetch → context → prompt → Groq → cache and return result.

        Args:
            template: Reserved for future use (e.g. "weekly", "defi_only").
                     Only "daily" is supported for now; value is ignored.
            user_apps_by_category: Optional per-category app filter. If None, all apps.

        Returns:
            dict with keys: text, digest_id, sections, tokens, fetched_at, generated_at.

        Raises:
            Re-raises any exception after logging — Notifier (P21) must catch to avoid
            sending empty digest to subscribers.
        """
        try:
            logger.info("[DIGEST] Step 1/4 — fetching snapshot")
            snapshot = await fetcher_manager.fetch_all_sources()

            logger.info("[DIGEST] Step 2/4 — building context")
            context = self._builder.build_context(snapshot, user_apps_by_category)

            if not context or len(context.strip()) < 100:
                logger.warning("[DIGEST] Context too small — generating market-only digest")
                context = self._builder.build_context(snapshot, user_apps_by_category=None)

            logger.info("[DIGEST] Step 3/4 — building prompt")
            messages = prompt_builder.build_digest_prompt(context)

            logger.info("[DIGEST] Step 4/4 — calling Groq")
            text = await groq_client.generate(messages, max_tokens=MAX_TOKENS_DIGEST)

            digest_id = uuid4().hex[:8]
            n_sections = context.count("## ")
            result = {
                "text": text,
                "digest_id": digest_id,
                "sections": n_sections,
                "tokens": MAX_TOKENS_DIGEST,
                "fetched_at": snapshot.get("fetched_at", ""),
                "generated_at": datetime.utcnow().isoformat(),
            }

            self._save_digest(digest_id, result)
            logger.info(
                f"[DIGEST] Generated digest_id={digest_id} "
                f"| sections={n_sections} | tokens={MAX_TOKENS_DIGEST}"
            )

            return result

        except Exception as exc:
            logger.exception(f"[DIGEST] Generation failed: {exc}")
            raise

    def _save_digest(self, digest_id: str, data: dict) -> None:
        """Persist digest JSON to data/cache/digest_{digest_id}.json."""
        try:
            DIGEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path = DIGEST_CACHE_DIR / f"digest_{digest_id}.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            logger.debug(f"[DIGEST] Saved to {path}")
        except OSError as exc:
            logger.warning(f"[DIGEST] Failed to save digest cache: {exc}")

    def load_digest(self, digest_id: str) -> dict | None:
        """Load a previously generated digest from cache by its ID.

        Used by callback_router (P8) for 'details' and 'links' callbacks.
        """
        try:
            path = DIGEST_CACHE_DIR / f"digest_{digest_id}.json"
            if not path.exists():
                return None
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[DIGEST] Failed to load digest {digest_id}: {exc}")
            return None


# Module-level singleton — imported by Notifier (P21), digest_handler (P22), ask_handler (P26)
digest_generator = DigestGenerator()
