# P-Teste — Phase 8 referral economy (Share & Earn) validation

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from web3 import Web3

from src.bot.handlers import build_earnings_dashboard_html, parse_proposal_start_deep_link
from src.utils.defi_links import build_venue_links


# ---------------------------------------------------------------------------
# Deep link parse — scenarios 3, 4, 5, 6
# ---------------------------------------------------------------------------


def test_parse_proposal_42_ref_valid():
    """proposal_42_ref_67890 → proposal 42, referrer 67890."""
    assert parse_proposal_start_deep_link("proposal_42_ref_67890") == (42, 67890)


def test_parse_proposal_42_no_ref():
    """proposal_42 → referrer None (P6-UX.7)."""
    assert parse_proposal_start_deep_link("proposal_42") == (42, None)


def test_parse_malformed_ref_degrades():
    """proposal_42_ref_naoNumero → referrer None, proposal still 42."""
    assert parse_proposal_start_deep_link("proposal_42_ref_naoNumero") == (42, None)


def test_parse_auto_referral_same_id_still_parses():
    """proposal_42_ref_11111 → (42, 11111); start_handler guard skips DB write."""
    assert parse_proposal_start_deep_link("proposal_42_ref_11111") == (42, 11111)


def test_parse_empty_string():
    assert parse_proposal_start_deep_link("") == (None, None)


# ---------------------------------------------------------------------------
# /earnings HTML — scenarios 8, 9
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_earnings_dashboard_with_wallet_and_referrals():
    """Scenario 8 — counters + truncated wallet."""
    mock_user = MagicMock()
    mock_user.user_wallet = "0x1234567890123456789012345678901234567890"
    mock_user.wallet_address = None

    with patch(
        "src.bot.handlers.db.get_referral_stats",
        new_callable=AsyncMock,
        return_value={
            "referral_count": 1,
            "total_swap_count": 0,
            "gov_points": 5,
            "total_earned_usdm": "0",
        },
    ), patch(
        "src.bot.handlers.db.get_user",
        new_callable=AsyncMock,
        return_value=mock_user,
    ):
        html = await build_earnings_dashboard_html(67890)

    assert "💰 <b>Your GovAI Hub Earnings</b>" in html
    assert "👥 <b>Referrals:</b> 1 voters brought" in html
    assert "🗳️ <b>Actions generated:</b> 0" in html
    assert "⭐ <b>GovPoints:</b> 5" in html
    assert "💵 <b>USDm earned:</b> 0" in html
    assert "💼 <b>Wallet:</b> <code>" in html
    assert "0x1234" in html
    assert "registered wallet via DAO Treasury" in html


@pytest.mark.asyncio
async def test_earnings_dashboard_no_wallet_no_referrals_scenario_9():
    """Scenario 9 — empty copy + wallet warning, no counter grid."""
    mock_user = MagicMock()
    mock_user.user_wallet = None
    mock_user.wallet_address = None

    with patch(
        "src.bot.handlers.db.get_referral_stats",
        new_callable=AsyncMock,
        return_value={
            "referral_count": 0,
            "total_swap_count": 0,
            "gov_points": 0,
            "total_earned_usdm": "0",
        },
    ), patch(
        "src.bot.handlers.db.get_user",
        new_callable=AsyncMock,
        return_value=mock_user,
    ):
        html = await build_earnings_dashboard_html(99901)

    assert "No referrals yet. Share a proposal to start earning." in html
    assert "Wallet not registered" in html
    assert "👥 <b>Referrals:</b>" not in html


# ---------------------------------------------------------------------------
# Ubeswap feeTo — scenario 10
# ---------------------------------------------------------------------------


def test_build_venue_links_ubeswap_adds_fee_to_when_treasury_valid():
    treasury = "0x1111111111111111111111111111111111111111"
    with patch.dict(os.environ, {"TREASURY_ADDRESS": treasury}):
        markup = build_venue_links(
            {"venue": "ubeswap", "token_in": "CELO", "token_out": "stCELO"}
        )
    row0 = markup.inline_keyboard[0][0]
    assert row0.url is not None
    assert "app.ubeswap.org" in row0.url
    assert "feeTo=" in row0.url
    assert Web3.to_checksum_address(treasury) in row0.url


def test_build_venue_links_ubeswap_no_fee_to_when_treasury_missing():
    with patch.dict(os.environ, {"TREASURY_ADDRESS": ""}):
        markup = build_venue_links(
            {"venue": "ubeswap", "token_in": "CELO", "token_out": "stCELO"}
        )
    row0 = markup.inline_keyboard[0][0]
    assert row0.url is not None
    assert "feeTo=" not in row0.url


def test_build_venue_links_ubeswap_no_fee_to_when_treasury_invalid():
    with patch.dict(os.environ, {"TREASURY_ADDRESS": "not_an_address"}):
        markup = build_venue_links(
            {"venue": "ubeswap", "token_in": "CELO", "token_out": "stCELO"}
        )
    row0 = markup.inline_keyboard[0][0]
    assert "feeTo=" not in row0.url


def test_build_venue_links_fallback_base_when_tokens_unknown():
    with patch.dict(os.environ, {"TREASURY_ADDRESS": "0x2222222222222222222222222222222222222222"}):
        markup = build_venue_links(
            {
                "venue": "ubeswap",
                "token_in": "UNKNOWNX",
                "token_out": "UNKNOWNY",
            }
        )
    row0 = markup.inline_keyboard[0][0]
    assert row0.url == "https://app.ubeswap.org/#/swap"
    assert "feeTo=" not in row0.url
