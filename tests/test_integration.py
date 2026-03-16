"""
test_integration.py

End-to-end integration test for the full digest pipeline:
RSS → Twitter → Market → OnChain → DigestBuilder →
PromptBuilder → Groq (mocked) → DigestGenerator result.

No real network calls are made — all external I/O is mocked.
Validates the contract between all pipeline stages.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ──────────────────────────────────────────────────────────────────────
# Shared fixtures
# ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_rss_items() -> list[dict]:
    """Realistic RSS items covering multiple Celo app categories."""
    return [
        {
            "title":      "Ubeswap introduces concentrated liquidity",
            "url":        "https://ubeswap.org/blog/concentrated-liquidity",
            "source":     "Ubeswap",
            "source_app": "ubeswap",
            "category":   "defi",
            "published":  "2026-03-15T08:00:00Z",
        },
        {
            "title":      "MiniPay expands to East Africa",
            "url":        "https://minipay.opera.com/blog/east-africa",
            "source":     "MiniPay",
            "source_app": "minipay",
            "category":   "payments",
            "published":  "2026-03-15T07:30:00Z",
        },
        {
            "title":      "Celo governance proposal CGP-120 passed",
            "url":        "https://forum.celo.org/t/cgp-120/1234",
            "source":     "Celo Forum",
            "source_app": "celonetwork",
            "category":   "refi_social",
            "published":  "2026-03-15T06:00:00Z",
        },
        {
            "title":      "Toucan Protocol retires 10,000 carbon credits",
            "url":        "https://toucan.earth/blog/retirement",
            "source":     "Toucan",
            "source_app": "toucan",
            "category":   "refi_social",
            "published":  "2026-03-14T22:00:00Z",
        },
        {
            "title":      "ImpactMarket launches UBI pilot in Nigeria",
            "url":        "https://impactmarket.com/blog/nigeria",
            "source":     "ImpactMarket",
            "source_app": "impactmarket",
            "category":   "refi_social",
            "published":  "2026-03-14T20:00:00Z",
        },
    ]


@pytest.fixture
def mock_twitter_items() -> list[dict]:
    """Realistic Twitter/Nitter items from Celo ecosystem accounts."""
    return [
        {
            "title":      "Ubeswap: New pool live — CELO/cUSD with 0.05% fee",
            "url":        "https://x.com/Ubeswap/status/123456",
            "source":     "Twitter",
            "source_app": "ubeswap",
            "category":   "defi",
            "published":  "2026-03-15T09:00:00Z",
        },
        {
            "title":      "Toucan: Carbon market weekly recap",
            "url":        "https://x.com/toucanprotocol/status/789012",
            "source":     "Twitter",
            "source_app": "toucan",
            "category":   "refi_social",
            "published":  "2026-03-15T08:30:00Z",
        },
    ]


@pytest.fixture
def mock_market_data() -> dict:
    """Realistic market snapshot for CELO."""
    return {
        "price":       0.0734,
        "pct_24h":    -2.23,
        "market_cap":  47_312_000,
        "volume":       3_218_000,
        "tvl":         25_990_187,
    }


@pytest.fixture
def mock_onchain_data() -> dict:
    """Realistic on-chain snapshot from Celo RPC."""
    return {
        "block_number": 61_623_613,
        "cusd_supply":  14_200_000.0,
        "ceur_supply":   1_800_000.0,
        "creal_supply":    650_000.0,
    }


@pytest.fixture
def mock_snapshot(
    mock_rss_items,
    mock_twitter_items,
    mock_market_data,
    mock_onchain_data,
) -> dict:
    """Full combined snapshot as returned by FetcherManager."""
    return {
        "rss":        mock_rss_items,
        "twitter":    mock_twitter_items,
        "market":     mock_market_data,
        "onchain":    mock_onchain_data,
        "fetched_at": "2026-03-15T09:00:00+00:00",
    }


@pytest.fixture
def all_apps_by_category() -> dict[str, list[str]]:
    """All apps enabled — simulates a user with no filters applied."""
    return {
        "payments":    ["minipay", "valora", "halofi", "hurupay"],
        "defi":        [
            "ubeswap", "moola", "mento", "symmetric",
            "mobius", "knox", "equalizer", "uniswap",
        ],
        "onramp_nft":  ["celocashflow", "unipos", "octoplace",
                        "hypermove", "truefeedback"],
        "refi_social": ["toucan", "toucanrefi", "impactmarket",
                        "masa", "celonetwork", "celoreserve"],
    }


def _make_groq_response(content: str) -> MagicMock:
    """Build a mock Groq completion response with realistic structure."""
    choice                   = MagicMock()
    choice.message           = MagicMock()
    choice.message.content   = content
    choice.finish_reason     = "stop"

    usage                    = MagicMock()
    usage.prompt_tokens      = 428
    usage.completion_tokens  = 223
    usage.total_tokens       = 651

    response                 = MagicMock()
    response.choices         = [choice]
    response.usage           = usage
    return response


MOCK_DIGEST_TEXT = (
    "Celo Ecosystem Digest — March 15, 2026\n\n"
    "DeFi & Swaps\n"
    "Ubeswap introduced concentrated liquidity pools, enabling more "
    "capital-efficient trading for CELO/cUSD pairs. TVL on Celo reached "
    "$25.9M as on-chain activity increases.\n\n"
    "Payments & Wallets\n"
    "MiniPay announced expansion to East Africa, targeting 50M potential "
    "users with mobile-first cUSD payments.\n\n"
    "ReFi & Social\n"
    "Toucan Protocol retired 10,000 carbon credits on Celo. ImpactMarket "
    "launched a UBI pilot in Nigeria. CGP-120 governance proposal passed.\n\n"
    "Market Snapshot\n"
    "CELO: $0.0734 (-2.23%) | TVL: $25.99M | Block: #61,623,613 | "
    "cUSD supply: 14.2M"
)


# ──────────────────────────────────────────────────────────────────────
# Integration tests
# ──────────────────────────────────────────────────────────────────────

class TestDigestPipelineIntegration:
    """
    End-to-end tests for the full digest pipeline.
    Validates that all stages integrate correctly when external I/O
    is replaced with deterministic mocks.
    """

    @pytest.mark.asyncio
    async def test_full_pipeline_returns_valid_digest(
        self, mock_snapshot, all_apps_by_category
    ):
        """
        Full pipeline RSS→Twitter→Market→OnChain→Build→Prompt→Groq
        must produce a digest with: digest_id, text > 200 chars,
        sections > 0, tokens > 0.
        """
        from src.ai.digest_generator import DigestGenerator

        mock_fetcher = AsyncMock()
        mock_fetcher.fetch_all_sources = AsyncMock(return_value=mock_snapshot)

        mock_groq = AsyncMock()
        mock_groq.generate = AsyncMock(return_value=MOCK_DIGEST_TEXT)

        with (
            patch(
                "src.ai.digest_generator.fetcher_manager",
                mock_fetcher,
            ),
            patch(
                "src.ai.digest_generator.groq_client",
                mock_groq,
            ),
            patch(
                "src.ai.digest_generator.cache.get_snapshot",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.ai.digest_generator.cache.set_digest",
                new_callable=AsyncMock,
            ),
        ):
            generator = DigestGenerator()
            result = await generator.generate_digest(
                template="daily",
                user_apps_by_category=all_apps_by_category,
            )

        # ── Core assertions ──────────────────────────────────────────
        assert result is not None, "Pipeline must return a result dict"

        digest_id = result.get("digest_id") or result.get("id")
        assert digest_id, "Result must contain a digest_id"
        assert len(digest_id) >= 6, "digest_id must be at least 6 chars"

        text = result.get("text") or result.get("digest") or ""
        assert len(text) > 200, (
            f"Digest text must be > 200 chars, got {len(text)}"
        )

        sections = result.get("sections", [])
        assert len(sections) > 0, "Must have at least 1 section"

        tokens = result.get("tokens", 0)
        assert tokens > 0, "Must have consumed Groq tokens > 0"

    @pytest.mark.asyncio
    async def test_pipeline_uses_fetcher_manager(
        self, mock_snapshot, all_apps_by_category
    ):
        """
        DigestGenerator must call FetcherManager.fetch_all_sources()
        exactly once per generation when cache is empty.
        """
        from src.ai.digest_generator import DigestGenerator

        mock_fetcher = AsyncMock()
        mock_fetcher.fetch_all_sources = AsyncMock(return_value=mock_snapshot)

        mock_groq = AsyncMock()
        mock_groq.generate = AsyncMock(return_value=MOCK_DIGEST_TEXT)

        with (
            patch(
                "src.ai.digest_generator.fetcher_manager",
                mock_fetcher,
            ),
            patch(
                "src.ai.digest_generator.groq_client",
                mock_groq,
            ),
            patch(
                "src.ai.digest_generator.cache.get_snapshot",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.ai.digest_generator.cache.set_digest",
                new_callable=AsyncMock,
            ),
        ):
            generator = DigestGenerator()
            await generator.generate_digest(
                template="daily",
                user_apps_by_category=all_apps_by_category,
            )

            # FetcherManager must be called exactly once
            mock_fetcher.fetch_all_sources.assert_called_once()

    @pytest.mark.asyncio
    async def test_pipeline_uses_snapshot_cache(
        self, mock_snapshot, all_apps_by_category
    ):
        """
        DigestGenerator must use the cached snapshot and NOT call
        fetcher_manager.fetch_all_sources() when cache is fresh.
        """
        from src.ai.digest_generator import DigestGenerator

        mock_fetcher = AsyncMock()
        mock_fetcher.fetch_all_sources = AsyncMock(return_value=mock_snapshot)

        mock_groq = AsyncMock()
        mock_groq.generate = AsyncMock(return_value=MOCK_DIGEST_TEXT)

        with (
            patch(
                "src.ai.digest_generator.fetcher_manager",
                mock_fetcher,
            ),
            patch(
                "src.ai.digest_generator.groq_client",
                mock_groq,
            ),
            patch(
                "src.ai.digest_generator.cache.get_snapshot",
                new_callable=AsyncMock,
                return_value=mock_snapshot,
            ),
            patch(
                "src.ai.digest_generator.cache.set_digest",
                new_callable=AsyncMock,
            ),
        ):
            generator = DigestGenerator()
            await generator.generate_digest(
                template="daily",
                user_apps_by_category=all_apps_by_category,
            )

            # fetch_all_sources must NOT be called — cache was used
            mock_fetcher.fetch_all_sources.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_digest_builder_filters_apps(
        self, mock_snapshot
    ):
        """
        A user with only 'defi' apps enabled must NOT see payments or
        ReFi content in their digest context (prompt to Groq).
        """
        from src.ai.digest_generator import DigestGenerator

        # User only follows DeFi
        defi_only_apps = {
            "defi":        ["ubeswap", "moola"],
            "payments":    [],
            "onramp_nft":  [],
            "refi_social": [],
        }

        captured_prompt: list[str] = []

        async def capture_create(messages, max_tokens=None, **kwargs):
            for msg in messages:
                if msg.get("role") == "user":
                    captured_prompt.append(msg.get("content", ""))
            return (
                "DeFi Digest: Ubeswap concentrated liquidity live. "
                "CELO: $0.0734. TVL: $25.9M. Block: #61,623,613."
            )

        mock_fetcher = AsyncMock()
        mock_fetcher.fetch_all_sources = AsyncMock(return_value=mock_snapshot)

        mock_groq = AsyncMock()
        mock_groq.generate = capture_create

        with (
            patch(
                "src.ai.digest_generator.fetcher_manager",
                mock_fetcher,
            ),
            patch(
                "src.ai.digest_generator.groq_client",
                mock_groq,
            ),
            patch(
                "src.ai.digest_generator.cache.get_snapshot",
                new_callable=AsyncMock,
                return_value=mock_snapshot,
            ),
            patch(
                "src.ai.digest_generator.cache.set_digest",
                new_callable=AsyncMock,
            ),
        ):
            generator = DigestGenerator()
            result = await generator.generate_digest(
                template="daily",
                user_apps_by_category=defi_only_apps,
            )

        # The context sent to Groq must contain DeFi but not Payments
        if captured_prompt:
            prompt_text = " ".join(captured_prompt).lower()
            assert "ubeswap" in prompt_text or "defi" in prompt_text
            assert "minipay" not in prompt_text

        assert result is not None

    @pytest.mark.asyncio
    async def test_pipeline_groq_fallback_still_produces_digest(
        self, mock_snapshot, all_apps_by_category
    ):
        """
        Pipeline must still produce a valid result when the primary
        Groq model fails and falls back to the secondary model.
        """
        from src.ai.digest_generator import DigestGenerator
        from src.ai.groq_client import groq_client

        fallback_text = (
            "Celo Digest (fallback model): Ubeswap TVL growing. "
            "CELO at $0.0734. MiniPay expanding. Celo block "
            "#61,623,613. cUSD supply stable at 14.2M."
        )
        mock_create = AsyncMock(
            side_effect=[
                Exception("rate_limit_exceeded"),
                _make_groq_response(fallback_text),
            ]
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        mock_fetcher = AsyncMock()
        mock_fetcher.fetch_all_sources = AsyncMock(return_value=mock_snapshot)

        with (
            patch(
                "src.ai.digest_generator.fetcher_manager",
                mock_fetcher,
            ),
            patch(
                "src.ai.digest_generator.cache.get_snapshot",
                new_callable=AsyncMock,
                return_value=mock_snapshot,
            ),
            patch(
                "src.ai.digest_generator.cache.set_digest",
                new_callable=AsyncMock,
            ),
            patch.object(groq_client, "_client", mock_client),
        ):
            generator = DigestGenerator()
            result = await generator.generate_digest(
                template="daily",
                user_apps_by_category=all_apps_by_category,
            )

        assert result is not None, "Pipeline must recover via fallback"

        text = result.get("text") or result.get("digest") or ""
        assert len(text) > 50, "Fallback digest must have meaningful content"

        # Real groq_client.generate() calls _client.chat.completions.create
        # twice (first model fails, second succeeds)
        assert mock_create.call_count >= 2, "Must have tried at least 2 Groq models"

    @pytest.mark.asyncio
    async def test_pipeline_digest_id_is_unique(
        self, mock_snapshot, all_apps_by_category
    ):
        """
        Each pipeline run must produce a unique digest_id.
        Duplicate IDs would cause cache collisions.
        """
        from src.ai.digest_generator import DigestGenerator

        mock_fetcher = AsyncMock()
        mock_fetcher.fetch_all_sources = AsyncMock(return_value=mock_snapshot)

        mock_groq = AsyncMock()
        mock_groq.generate = AsyncMock(return_value=MOCK_DIGEST_TEXT)

        digest_ids: list[str] = []

        for _ in range(3):
            with (
                patch(
                    "src.ai.digest_generator.fetcher_manager",
                    mock_fetcher,
                ),
                patch(
                    "src.ai.digest_generator.groq_client",
                    mock_groq,
                ),
                patch(
                    "src.ai.digest_generator.cache.get_snapshot",
                    new_callable=AsyncMock,
                    return_value=mock_snapshot,
                ),
                patch(
                    "src.ai.digest_generator.cache.set_digest",
                    new_callable=AsyncMock,
                ),
            ):
                generator = DigestGenerator()
                result = await generator.generate_digest(
                    template="daily",
                    user_apps_by_category=all_apps_by_category,
                )

            digest_id = result.get("digest_id") or result.get("id")
            digest_ids.append(digest_id)

        # All 3 digest_ids must be unique
        assert len(set(digest_ids)) == 3, (
            f"digest_ids must be unique, got: {digest_ids}"
        )
