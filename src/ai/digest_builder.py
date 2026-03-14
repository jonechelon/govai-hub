"""
DigestBuilder: transforms raw fetcher snapshot into structured context by category
for Groq to generate the daily digest. Stateless, sync-only (no I/O).
"""

from __future__ import annotations

import logging

from src.bot.keyboards import APPS_BY_CATEGORY

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
    ) -> str:
        """
        Build structured context string from fetcher snapshot and user app filters.

        Sync-only: no I/O, processes data in memory. If user_apps_by_category is
        empty or None, all apps from APPS_BY_CATEGORY are considered enabled.

        Args:
            snapshot: Output of FetcherManager.fetch_all_sources() with keys
                rss, twitter, market, onchain.
            user_apps_by_category: Optional per-category list of enabled app names.
                If {} or None, all apps are enabled.

        Returns:
            Multiline context string (category sections + Market Snapshot) for
            use as user_prompt in Groq. Never empty; at least Market Snapshot.
        """
        def _estimate_tokens(text: str) -> int:
            return int(len(text) / CHARS_PER_TOKEN)

        # Step 1 — Consolidate content items (RSS + Twitter)
        all_items = snapshot.get("rss", []) + snapshot.get("twitter", [])

        # Step 2 — Determine enabled apps
        if not user_apps_by_category:
            enabled_apps: set[str] = set()
            for apps in APPS_BY_CATEGORY.values():
                enabled_apps.update(apps)
        else:
            enabled_apps = {
                app
                for apps in user_apps_by_category.values()
                for app in apps
            }

        # Step 3 — Filter and group by category
        sections: dict[str, list[str]] = {cat: [] for cat in CATEGORY_PRIORITY}

        for item in all_items:
            cat = item.get("category", "")
            app = item.get("source_app", "")
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

        return context

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
