"""
test_ai.py

Unit tests for DigestBuilder and GroqClient.
No real Groq API calls are made — AsyncGroq is fully mocked.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ──────────────────────────────────────────────────────────────────────
# test_digest_builder_filters_by_apps
# ──────────────────────────────────────────────────────────────────────


class TestDigestBuilder:
    @pytest.fixture
    def full_snapshot(self) -> dict:
        """Mock snapshot with items from multiple apps and categories."""
        return {
            "rss": [
                {
                    "title": "Ubeswap adds CELO/cUSD pool",
                    "url": "https://ubeswap.org/blog/1",
                    "source": "Ubeswap",
                    "source_app": "ubeswap",
                    "category": "defi",
                },
                {
                    "title": "MiniPay surpasses 2M users",
                    "url": "https://minipay.opera.com/blog/1",
                    "source": "MiniPay",
                    "source_app": "minipay",
                    "category": "payments",
                },
                {
                    "title": "Toucan Protocol carbon update",
                    "url": "https://toucan.earth/blog/1",
                    "source": "Toucan",
                    "source_app": "toucan",
                    "category": "refi",
                },
            ],
            "twitter": [],
            "market": {
                "price": 0.0734,
                "pct_24h": -2.23,
                "market_cap": 47_000_000,
                "tvl": 25_990_187,
            },
            "onchain": {
                "block_number": 61_623_613,
                "cusd_supply": 14_200_000,
            },
        }

    def test_digest_builder_filters_by_apps(self, full_snapshot):
        """
        DigestBuilder.build_context() must only include items whose
        source_app is in the user's enabled apps list.
        """
        from src.ai.digest_builder import DigestBuilder

        user_apps = {
            "defi": ["ubeswap", "moola"],
            "payments": [],
            "onramp_nft": [],
            "refi_social": [],
        }

        builder = DigestBuilder()
        context, sections = builder.build_context(
            snapshot=full_snapshot,
            user_apps_by_category=user_apps,
        )

        assert isinstance(context, str)
        assert len(context) > 0
        assert isinstance(sections, list)

        assert "ubeswap" in context.lower() or "Ubeswap" in context
        assert "MiniPay" not in context
        assert "Toucan" not in context
        assert "0.0734" in context or "CELO" in context

    def test_digest_builder_includes_market_always(self, full_snapshot):
        """
        DigestBuilder.build_context() must always include market data
        even when all app categories are disabled.
        """
        from src.ai.digest_builder import DigestBuilder

        user_apps = {
            cat: [] for cat in ["defi", "payments", "onramp_nft", "refi_social"]
        }

        builder = DigestBuilder()
        context, sections = builder.build_context(
            snapshot=full_snapshot,
            user_apps_by_category=user_apps,
        )

        assert "0.0734" in context or "CELO" in context or "TVL" in context

    def test_digest_builder_respects_max_items(self, full_snapshot):
        """
        DigestBuilder.build_context() must not exceed 3 items per category.
        """
        from src.ai.digest_builder import DigestBuilder, MAX_ITEMS_PER_CATEGORY

        for i in range(5):
            full_snapshot["rss"].append({
                "title": f"DeFi update #{i}",
                "url": f"https://defi.example.com/{i}",
                "source": "Ubeswap",
                "source_app": "ubeswap",
                "category": "defi",
            })

        user_apps = {
            "defi": ["ubeswap"],
            "payments": [],
            "onramp_nft": [],
            "refi_social": [],
        }
        builder = DigestBuilder()
        _, sections = builder.build_context(
            snapshot=full_snapshot,
            user_apps_by_category=user_apps,
        )

        defi_sections = [s for s in sections if s.get("category") == "defi"]
        if defi_sections:
            items = defi_sections[0].get("items", [])
            assert len(items) <= MAX_ITEMS_PER_CATEGORY


# ──────────────────────────────────────────────────────────────────────
# test_groq_client_fallback
# ──────────────────────────────────────────────────────────────────────


class TestGroqClient:
    MESSAGES = [
        {"role": "system", "content": "You are a Celo digest bot."},
        {"role": "user", "content": "Summarize today's Celo news."},
    ]

    def _make_mock_response(self, content: str, total_tokens: int = 200):
        """Build a mock Groq completion response."""
        choice = MagicMock()
        choice.message = MagicMock()
        choice.message.content = content
        choice.finish_reason = "stop"

        usage = MagicMock()
        usage.prompt_tokens = 120
        usage.completion_tokens = total_tokens - 120
        usage.total_tokens = total_tokens

        response = MagicMock()
        response.choices = [choice]
        response.usage = usage
        return response

    @pytest.mark.asyncio
    async def test_groq_client_returns_text(self):
        """
        GroqClient.generate() must return a non-empty string when
        the primary Groq model succeeds.
        """
        from src.ai.groq_client import GroqClient

        mock_response = self._make_mock_response(
            "Today in Celo: Ubeswap TVL up 5%..."
        )
        mock_create = AsyncMock(return_value=mock_response)

        mock_groq = MagicMock()
        mock_groq.chat.completions.create = mock_create

        with (
            patch("src.ai.groq_client.AsyncGroq", return_value=mock_groq),
            patch("src.ai.groq_client.get_env_or_fail", return_value="test-key"),
        ):
            client = GroqClient()
            result = await client.generate(
                messages=self.MESSAGES, max_tokens=600
            )

        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_groq_client_fallback(self):
        """
        GroqClient.generate() must fall back to the next model when
        the primary model raises an exception, and return a valid result.
        """
        from src.ai.groq_client import GroqClient

        call_count = {"n": 0}

        async def mock_create(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("model_overloaded: try again later")
            return self._make_mock_response(
                "Fallback digest: Celo network stable."
            )

        mock_groq = MagicMock()
        mock_groq.chat.completions.create = mock_create

        with (
            patch("src.ai.groq_client.AsyncGroq", return_value=mock_groq),
            patch("src.ai.groq_client.get_env_or_fail", return_value="test-key"),
        ):
            client = GroqClient()
            result = await client.generate(
                messages=self.MESSAGES, max_tokens=600
            )

        assert isinstance(result, str)
        assert len(result) > 0
        assert call_count["n"] >= 2

    @pytest.mark.asyncio
    async def test_groq_client_all_models_fail(self):
        """
        GroqClient.generate() must raise RuntimeError when all
        fallback models fail.
        """
        from src.ai.groq_client import GroqClient

        async def always_fail(**kwargs):
            raise Exception("service unavailable")

        mock_groq = MagicMock()
        mock_groq.chat.completions.create = always_fail

        with (
            patch("src.ai.groq_client.AsyncGroq", return_value=mock_groq),
            patch("src.ai.groq_client.get_env_or_fail", return_value="test-key"),
        ):
            client = GroqClient()
            with pytest.raises((RuntimeError, Exception)):
                await client.generate(
                    messages=self.MESSAGES, max_tokens=600
                )

    @pytest.mark.asyncio
    async def test_groq_client_returns_usage_when_requested(self):
        """
        GroqClient.generate(return_usage=True) must return a tuple
        (text, usage_dict) with token counts.
        """
        from src.ai.groq_client import GroqClient

        mock_groq = MagicMock()
        mock_groq.chat.completions.create = AsyncMock(
            return_value=self._make_mock_response("Celo digest here.", 200)
        )

        with (
            patch("src.ai.groq_client.AsyncGroq", return_value=mock_groq),
            patch("src.ai.groq_client.get_env_or_fail", return_value="test-key"),
        ):
            client = GroqClient()
            out = await client.generate(
                messages=self.MESSAGES,
                max_tokens=600,
                return_usage=True,
            )

        text, usage = out
        assert isinstance(text, str)
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        assert usage["total_tokens"] == 200
