"""
DigestBuilder: transforms raw fetcher snapshot into structured context by category
for Groq to generate the daily digest. Stateless, sync-only (no I/O).
"""

from __future__ import annotations

import logging

from src.database.models import APPS_AVAILABLE

logger = logging.getLogger(__name__)

CATEGORY_LABELS: dict[str, str] = {
    "network": "🧱 Celo Network & Infra",
    "payments": "💳 Payments & Wallets",
    "defi": "🔄 DeFi & Swaps",
    "onramp": "🌍 On-ramp / Off-ramp",
    "nfts": "🎨 NFTs & Games",
    "refi": "🌱 ReFi & Carbon",
    "social": "🧑‍🤝‍🧑 Social & Identity",
}

# Priority order for token truncation — most important categories kept first
CATEGORY_PRIORITY: list[str] = [
    "network",
    "payments",
    "defi",
    "onramp",
    "refi",
    "social",
    "nfts",
]

MAX_ITEMS_PER_CATEGORY: int = 3
MAX_CONTEXT_TOKENS: int = 3000
CHARS_PER_TOKEN: float = 4.0


class DigestBuilder:
    """Stateless builder: snapshot + user app filters → context string for Groq."""

    def build_context(
        self,
        snapshot: dict,
        user_apps_by_category: dict[str, list[str]] | None = None,
    ) -> tuple[str, list[dict]]:
        """
        Build structured context string and sections from fetcher snapshot and user app filters.

        Sync-only: no I/O, processes data in memory. If user_apps_by_category is
        empty or None, all apps from APPS_AVAILABLE are considered enabled.

        Args:
            snapshot: Output of FetcherManager.fetch_all_sources() with keys
                rss, twitter, market, onchain.
            user_apps_by_category: Optional per-category list of enabled app names.
                If {} or None, all apps are enabled.

        Returns:
            Tuple of (context_str, sections_list).
            context_str: Multiline context string (category sections + Market Snapshot)
                for use as user_prompt in Groq. Never empty; at least Market Snapshot.
            sections_list: List of {"category": str, "items": [{"title", "url", "source", "source_app"}, ...]}
                for links callback and reporting.
        """
        def _estimate_tokens(text: str) -> int:
            return int(len(text) / CHARS_PER_TOKEN)

        # Step 1 — Consolidate content items (RSS + Twitter)
        all_items = snapshot.get("rss", []) + snapshot.get("twitter", [])

        # Step 2 — Determine enabled apps (always lowercase for case-insensitive matching)
        if not user_apps_by_category:
            enabled_apps: set[str] = set()
            for apps in APPS_AVAILABLE.values():
                enabled_apps.update(a.lower() for a in apps)
        else:
            enabled_apps = {
                app.lower()
                for apps in user_apps_by_category.values()
                for app in apps
            }

        # Step 3 — Filter and group by category
        sample_apps = list({item.get("source_app", "unknown") for item in all_items})[:10]
        logger.debug("[DIGEST] source_app values in snapshot: %s", sample_apps)
        logger.debug("[DIGEST] User enabled apps (lowercase): %s", sorted(enabled_apps))

        sections: dict[str, list[str]] = {cat: [] for cat in CATEGORY_PRIORITY}

        for item in all_items:
            cat = item.get("category", "")
            app = item.get("source_app", "").lower()
            if cat not in sections:
                continue
            if app not in enabled_apps:
                continue
            if len(sections[cat]) >= MAX_ITEMS_PER_CATEGORY:
                continue
            line = (
                f"-  {item['title'].strip()} ({item['source']}) — {item['url']}"
            )
            sections[cat].append(line)

        # Step 4 — Build section blocks
        parts: list[str] = []

        for cat in CATEGORY_PRIORITY:
            items_in_cat = sections.get(cat, [])
            if not items_in_cat:
                continue
            label = CATEGORY_LABELS[cat]
            block = f"## {label}\n" + "\n".join(items_in_cat)
            parts.append(block)

        # Step 5 — Truncate by token limit
        context_parts: list[str] = []
        running_tokens = 0

        for part in parts:
            part_tokens = _estimate_tokens(part)
            if running_tokens + part_tokens > MAX_CONTEXT_TOKENS:
                logger.debug(
                    "[DIGEST] Token limit reached — dropping remaining sections"
                )
                break
            context_parts.append(part)
            running_tokens += part_tokens

        # Step 6 — Always append Market Snapshot (independent of token limit)
        market_section = self._build_market_section(snapshot)
        context_parts.append(market_section)

        # Step 7 — Log and return
        context = "\n\n".join(context_parts)
        total_tokens = _estimate_tokens(context)
        n_sections = len(context_parts)
        n_items = sum(len(sections.get(c, [])) for c in CATEGORY_PRIORITY)

        logger.info(
            f"[DIGEST] Context built: {n_sections} sections "
            f"| {n_items} items | ~{total_tokens} tokens"
        )

        # 4.E1 fallback — if context has no content sections, return market-only
        if n_sections == 1:
            logger.warning(
                "[DIGEST] No content items matched user filters — "
                "market-only context"
            )

        sections_list = self.build_sections(snapshot, user_apps_by_category)
        return context, sections_list

    def build_sections(
        self,
        snapshot: dict,
        user_apps_by_category: dict[str, list[str]] | None = None,
    ) -> list[dict]:
        """
        Build structured sections (category + items with title, url, source) for cache.
        Same filtering as build_context; used so links callback can extract URLs.

        Returns:
            List of {"category": str, "items": [{"title", "url", "source", "source_app"}, ...]}.
        """
        all_items = snapshot.get("rss", []) + snapshot.get("twitter", [])

        if not user_apps_by_category:
            enabled_apps = set()
            for apps in APPS_AVAILABLE.values():
                enabled_apps.update(a.lower() for a in apps)
        else:
            enabled_apps = {
                app.lower()
                for apps in user_apps_by_category.values()
                for app in apps
            }

        sections_dict: dict[str, list[dict]] = {cat: [] for cat in CATEGORY_PRIORITY}

        for item in all_items:
            cat = item.get("category", "")
            app = (item.get("source_app") or "").lower()
            if cat not in sections_dict:
                continue
            if app not in enabled_apps:
                continue
            if len(sections_dict[cat]) >= MAX_ITEMS_PER_CATEGORY:
                continue
            sections_dict[cat].append({
                "title": (item.get("title") or "").strip(),
                "url": (item.get("url") or item.get("link") or "").strip(),
                "source": (item.get("source") or "").strip(),
                "source_app": (item.get("source_app") or "").strip(),
            })

        return [
            {"category": cat, "items": sections_dict[cat]}
            for cat in CATEGORY_PRIORITY
            if sections_dict[cat]
        ]

    def _build_market_section(self, snapshot: dict) -> str:
        """
        Build Market Snapshot section from market and onchain data.

        Args:
            snapshot: Dict with optional keys market, onchain.

        Returns:
            Formatted multiline string (header + bullet lines).
        """
        market = snapshot.get("market", {})
        onchain = snapshot.get("onchain", {})

        price = market.get("price", 0.0)
        pct_24h = market.get("pct_24h", 0.0)
        market_cap = market.get("market_cap", 0.0)
        tvl = market.get("tvl", 0.0)
        volume = market.get("volume", 0.0)

        block_number = onchain.get("block_number", 0)
        cusd_supply = onchain.get("cusd_supply", 0.0)
        ceur_supply = onchain.get("ceur_supply", 0.0)
        creal_supply = onchain.get("creal_supply", 0.0)

        lines = [
            "## 📊 Market Snapshot",
            f"-  CELO Price: ${price:.4f} ({pct_24h:+.2f}% 24h)",
            f"-  Market Cap: ${market_cap:,.0f}",
            f"-  Volume 24h: ${volume:,.0f}",
            f"-  TVL (Celo chain): ${tvl:,.0f}",
            f"-  Latest Block: #{block_number:,}",
            f"-  cUSD Supply: {cusd_supply:,.0f}",
            f"-  cEUR Supply: {ceur_supply:,.0f}",
            f"-  cREAL Supply: {creal_supply:,.0f}",
        ]
        return "\n".join(lines)
