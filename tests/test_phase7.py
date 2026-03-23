# Phase 7 test suite — Celo Sepolia Network

import os

import pytest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# P-Teste 7.1 — get_w3("mainnet") behaviour is identical to pre-Phase 7
# ---------------------------------------------------------------------------


def test_get_w3_mainnet_uses_celo_rpc_url():
    """get_w3('mainnet') must resolve CELO_RPC_URL — regression zero."""
    rpc = "https://forno.celo.org"
    with patch.dict(os.environ, {"CELO_RPC_URL": rpc}):
        from src.fetchers.onchain_fetcher import get_w3

        w3 = get_w3("mainnet")
        assert rpc in w3.provider.endpoint_uri


# ---------------------------------------------------------------------------
# P-Teste 7.2 — get_w3("sepolia") resolves CELO_SEPOLIA_RPC_URL when set
# ---------------------------------------------------------------------------


def test_get_w3_sepolia_uses_sepolia_rpc_url():
    """get_w3('sepolia') must use CELO_SEPOLIA_RPC_URL when available."""
    sepolia_rpc = "https://celo-sepolia.example.rpc"
    with patch.dict(
        os.environ,
        {
            "CELO_RPC_URL": "https://forno.celo.org",
            "CELO_SEPOLIA_RPC_URL": sepolia_rpc,
        },
    ):
        from src.fetchers.onchain_fetcher import get_w3

        w3 = get_w3("sepolia")
        assert sepolia_rpc in w3.provider.endpoint_uri


# ---------------------------------------------------------------------------
# P-Teste 7.3 — CELO_SEPOLIA_RPC_URL absent → fallback mainnet + [WARN] log
# ---------------------------------------------------------------------------


def test_get_w3_sepolia_fallback_when_env_missing(capsys):
    """Missing CELO_SEPOLIA_RPC_URL must fall back to mainnet and print [WARN]."""
    mainnet_rpc = "https://forno.celo.org"
    env_patch = {k: v for k, v in os.environ.items() if k != "CELO_SEPOLIA_RPC_URL"}
    env_patch["CELO_RPC_URL"] = mainnet_rpc
    with patch.dict(os.environ, env_patch, clear=True):
        from src.fetchers.onchain_fetcher import get_w3

        w3 = get_w3("sepolia")
        captured = capsys.readouterr()
        assert "[WARN]" in captured.out
        assert "SEPOLIA" in captured.out.upper()
        assert mainnet_rpc in w3.provider.endpoint_uri


# ---------------------------------------------------------------------------
# P-Teste 7.4 — DB column 'network' exists with default 'mainnet'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_network_returns_mainnet_default():
    """get_user_network must return 'mainnet' for users with NULL network."""
    from src.database.manager import db
    from src.database.models import init_db

    await init_db()
    result = await db.get_user_network(user_id=999999999)
    assert result == "mainnet"


# ---------------------------------------------------------------------------
# P-Teste 7.5 — set_user_network rejects invalid network values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_user_network_raises_for_invalid_network():
    """set_user_network must raise ValueError for any network outside the valid set."""
    from src.database.manager import db
    from src.database.models import init_db

    await init_db()
    with pytest.raises(ValueError):
        await db.set_user_network(user_id=999999999, network="polygon")
