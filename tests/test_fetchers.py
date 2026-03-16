"""
test_fetchers.py

Unit tests for RSS, Market, OnChain and Payment fetchers.
All external I/O is mocked — no real network calls are made.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ──────────────────────────────────────────────────────────────────────
# test_rss_fetcher_returns_items
# ──────────────────────────────────────────────────────────────────────


class TestRSSFetcher:
    @pytest.fixture
    def rss_response_xml(self) -> str:
        """Minimal valid RSS 2.0 feed with 2 entries."""
        return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Celo Blog</title>
    <item>
      <title>Celo Q1 2026 Update</title>
      <link>https://blog.celo.org/q1-2026</link>
      <pubDate>Sat, 14 Mar 2026 10:00:00 +0000</pubDate>
    </item>
    <item>
      <title>MiniPay reaches 1M users</title>
      <link>https://blog.celo.org/minipay-1m</link>
      <pubDate>Fri, 13 Mar 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

    @pytest.mark.asyncio
    async def test_rss_fetcher_returns_items(self, rss_response_xml):
        """
        RSSFetcher.fetch_all() must return at least one item per feed
        when aiohttp returns a valid RSS XML response.
        """
        from src.fetchers.rss_fetcher import RSSFetcher

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value=rss_response_xml)

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        one_feed = [
            {
                "url": "https://blog.celo.org/feed",
                "source": "Celo Blog",
                "source_app": "celonetwork",
                "category": "network",
            }
        ]

        with (
            patch("src.fetchers.rss_fetcher.CONFIG", {"rss_feeds": one_feed}),
            patch.object(RSSFetcher, "_load_cache", return_value=None),
            patch("aiohttp.ClientSession", return_value=mock_session),
        ):
            fetcher = RSSFetcher()
            items = await fetcher.fetch_all()

        assert isinstance(items, list), "fetch_all() must return a list"
        assert len(items) > 0, "Must return at least one item"

        first = items[0]
        assert "title" in first, "Item must have 'title'"
        assert "url" in first or "link" in first, "Item must have URL"
        assert "source" in first, "Item must have 'source'"

    @pytest.mark.asyncio
    async def test_rss_fetcher_handles_timeout(self):
        """
        RSSFetcher.fetch_all() must return empty list (not raise)
        when aiohttp times out on all feeds.
        """
        from src.fetchers.rss_fetcher import RSSFetcher

        one_feed = [
            {
                "url": "https://blog.celo.org/feed",
                "source": "Celo Blog",
                "source_app": "celonetwork",
                "category": "network",
            }
        ]

        async def mock_get(*args, **kwargs):
            raise asyncio.TimeoutError("timeout")

        mock_session = AsyncMock()
        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.fetchers.rss_fetcher.CONFIG", {"rss_feeds": one_feed}),
            patch.object(RSSFetcher, "_load_cache", return_value=None),
            patch("aiohttp.ClientSession", return_value=mock_session),
        ):
            fetcher = RSSFetcher()
            items = await fetcher.fetch_all()

        assert isinstance(items, list)
        assert len(items) == 0


# ──────────────────────────────────────────────────────────────────────
# test_market_fetcher_returns_price
# ──────────────────────────────────────────────────────────────────────


class TestMarketFetcher:
    COINGECKO_RAW = {
        "market_data": {
            "current_price": {"usd": 0.0734},
            "price_change_percentage_24h": -2.23,
            "market_cap": {"usd": 47_000_000},
            "total_volume": {"usd": 3_200_000},
        }
    }

    DEFILLAMA_CHAINS = [{"name": "celo", "tvl": 25_990_187.0}]

    @pytest.mark.asyncio
    async def test_market_fetcher_returns_price(self):
        """
        MarketFetcher.fetch() must return CELO price and TVL when
        CoinGecko and DeFi Llama respond successfully.
        """
        from src.fetchers.market_fetcher import MarketFetcher

        async def mock_get(url, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            if "coingecko" in url:
                mock_resp.json = AsyncMock(return_value=self.COINGECKO_RAW)
            else:
                mock_resp.json = AsyncMock(
                    return_value=self.DEFILLAMA_CHAINS
                )
            return mock_resp

        mock_session = AsyncMock()
        mock_session.get = mock_get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(MarketFetcher, "_load_cache", return_value=None),
            patch("aiohttp.ClientSession", return_value=mock_session),
        ):
            fetcher = MarketFetcher()
            result = await fetcher.fetch()

        assert result is not None
        assert result.get("price") == pytest.approx(0.0734, rel=1e-3)
        assert "pct_24h" in result
        assert "tvl" in result
        assert result["tvl"] > 0

    @pytest.mark.asyncio
    async def test_market_fetcher_returns_last_cache_on_failure(self):
        """
        MarketFetcher.fetch() must return dict (or stale cache) when both
        APIs fail, instead of raising an exception.
        """
        from src.fetchers.market_fetcher import MarketFetcher

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=Exception("network error"))
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(MarketFetcher, "_load_cache", return_value=None),
            patch("aiohttp.ClientSession", return_value=mock_session),
        ):
            fetcher = MarketFetcher()
            result = await fetcher.fetch()

        assert result is None or isinstance(result, dict)


# ──────────────────────────────────────────────────────────────────────
# test_onchain_fetcher_returns_block
# ──────────────────────────────────────────────────────────────────────


class TestOnChainFetcher:
    @pytest.mark.asyncio
    async def test_onchain_fetcher_returns_block(self):
        """
        OnChainFetcher.fetch() must return block_number and cUSD supply
        when web3 RPC calls succeed.
        """
        from src.fetchers.onchain_fetcher import OnChainFetcher

        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.block_number = 61_623_613

        mock_contract = MagicMock()
        mock_contract.functions.totalSupply.return_value.call.return_value = (
            14_200_000 * 10**18
        )
        mock_w3.eth.contract.return_value = mock_contract

        config = {
            "celo_rpc": {"primary": "https://forno.celo.org"},
            "celo_contracts": {
                "cusd": "0x765DE816845861e75A25fCA122bb6898B8B1282a",
                "ceur": "0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73",
                "creal": "0xe8537a3d056DA446677B9E9d6c5dB704EaAb4787",
            },
        }

        with (
            patch("src.fetchers.onchain_fetcher.Web3", return_value=mock_w3),
            patch("src.fetchers.onchain_fetcher.CONFIG", config),
            patch.object(OnChainFetcher, "_load_cache", return_value=None),
        ):
            fetcher = OnChainFetcher()
            result = await fetcher.fetch()

        assert result is not None
        assert result.get("block_number") == 61_623_613
        assert "cusd_supply" in result
        assert result["cusd_supply"] == pytest.approx(14_200_000, rel=0.01)

    @pytest.mark.asyncio
    async def test_onchain_fetcher_handles_rpc_failure(self):
        """
        OnChainFetcher.fetch() must not raise when web3 RPC is unreachable.
        """
        from src.fetchers.onchain_fetcher import OnChainFetcher

        mock_w3 = MagicMock()
        mock_w3.is_connected.return_value = True
        mock_w3.eth.block_number = MagicMock(
            side_effect=Exception("RPC timeout")
        )

        config = {
            "celo_rpc": {"primary": "https://forno.celo.org"},
            "celo_contracts": {
                "cusd": "0x765DE816845861e75A25fCA122bb6898B8B1282a",
                "ceur": "0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73",
                "creal": "0xe8537a3d056DA446677B9E9d6c5dB704EaAb4787",
            },
        }

        with (
            patch("src.fetchers.onchain_fetcher.Web3", return_value=mock_w3),
            patch("src.fetchers.onchain_fetcher.CONFIG", config),
            patch.object(OnChainFetcher, "_load_cache", return_value=None),
        ):
            fetcher = OnChainFetcher()
            result = await fetcher.fetch()

        assert result is None or isinstance(result, dict)


# ──────────────────────────────────────────────────────────────────────
# test_payment_fetcher_verify_tx
# ──────────────────────────────────────────────────────────────────────


class TestPaymentVerifier:
    BOT_WALLET = "0x772fd6fE727306fcaE44981bF256b7b9b3138DBb"
    FROM_WALLET = "0xABCDEF1234567890ABCdef1234567890abcdef12"
    CELO_CONTRACT = "0x471EcE3750Da237f93B8E339c536989b8978a438"
    ERC20_TRANSFER_TOPIC = bytes.fromhex(
        "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )
    AMOUNT_7_CELO_WEI = 7 * 10**18

    def _make_log_dict(self):
        """Build a Transfer log dict matching handler expectations."""
        to_padded = bytes(12) + bytes.fromhex(self.BOT_WALLET[2:])
        from_padded = bytes(12) + bytes.fromhex(self.FROM_WALLET[2:])
        amount_data = self.AMOUNT_7_CELO_WEI.to_bytes(32, "big")
        return {
            "address": self.CELO_CONTRACT,
            "topics": [
                self.ERC20_TRANSFER_TOPIC,
                from_padded,
                to_padded,
            ],
            "data": amount_data,
        }

    def _make_receipt(self, status: int = 1, block_number: int = 61_623_600):
        return {
            "status": status,
            "blockNumber": block_number,
            "logs": [self._make_log_dict()],
        }

    @pytest.mark.asyncio
    async def test_payment_fetcher_verify_tx_valid(self):
        """
        _verify_celo_payment_sync() must return payment dict when
        a valid 7 CELO ERC-20 Transfer to the bot wallet is found.
        """
        from src.bot.handlers import _verify_celo_payment_sync

        mock_w3 = MagicMock()
        mock_w3.eth.block_number = 61_623_613
        mock_w3.eth.get_transaction_receipt = MagicMock(
            return_value=self._make_receipt()
        )

        from src.bot.handlers import CELO_CONTRACT_ADDRESS

        def to_checksum(addr):
            if not isinstance(addr, str) or not addr.startswith("0x"):
                return addr
            if addr.lower() == CELO_CONTRACT_ADDRESS.lower():
                return CELO_CONTRACT_ADDRESS
            return addr.lower()

        mock_web3_class = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = to_checksum

        with (
            patch("src.bot.handlers.Web3", mock_web3_class),
            patch(
                "src.bot.handlers.get_env_or_fail",
                side_effect=lambda k: (
                    self.BOT_WALLET if k == "BOT_WALLET_ADDRESS"
                    else "https://forno.celo.org"
                ),
            ),
        ):
            result = _verify_celo_payment_sync("0x" + "a" * 64)

        assert result is not None
        assert result["amount_celo"] == pytest.approx(7.0, rel=1e-6)
        assert "from_address" in result
        assert result["confirmations"] >= 3

    @pytest.mark.asyncio
    async def test_payment_fetcher_verify_tx_reverted(self):
        """
        _verify_celo_payment_sync() must return None for reverted tx.
        """
        from src.bot.handlers import _verify_celo_payment_sync

        mock_w3 = MagicMock()
        mock_w3.eth.block_number = 61_623_613
        mock_w3.eth.get_transaction_receipt = MagicMock(
            return_value=self._make_receipt(status=0)
        )

        from src.bot.handlers import CELO_CONTRACT_ADDRESS

        def to_checksum(addr):
            if not isinstance(addr, str) or not addr.startswith("0x"):
                return addr
            if addr.lower() == CELO_CONTRACT_ADDRESS.lower():
                return CELO_CONTRACT_ADDRESS
            return addr.lower()

        mock_web3_class = MagicMock()
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = to_checksum

        with (
            patch("src.bot.handlers.Web3", mock_web3_class),
            patch(
                "src.bot.handlers.get_env_or_fail",
                side_effect=lambda k: (
                    self.BOT_WALLET if k == "BOT_WALLET_ADDRESS"
                    else "https://forno.celo.org"
                ),
            ),
        ):
            result = _verify_celo_payment_sync("0x" + "b" * 64)

        assert result is None

    @pytest.mark.asyncio
    async def test_payment_fetcher_invalid_hash_format(self):
        """
        _verify_celo_payment_sync() must return None for malformed hash.
        """
        from src.bot.handlers import _verify_celo_payment_sync

        result = _verify_celo_payment_sync("not-a-hash")
        assert result is None

        result = _verify_celo_payment_sync("0x1234")
        assert result is None
