"""Unit tests for src.utils.user_network helpers."""

from types import SimpleNamespace

from src.utils.user_network import (
    cycle_network,
    effective_user_network,
    network_toggle_label,
)


def test_cycle_network_rotates_three_way() -> None:
    assert cycle_network("mainnet") == "alfajores"
    assert cycle_network("alfajores") == "sepolia"
    assert cycle_network("sepolia") == "mainnet"


def test_cycle_network_invalid_defaults_to_mainnet_sequence() -> None:
    assert cycle_network("unknown") == "alfajores"


def test_effective_user_network_legacy_prefers_alfajores_when_network_stale() -> None:
    u = SimpleNamespace(network="mainnet", preferred_network="alfajores")
    assert effective_user_network(u) == "alfajores"


def test_effective_user_network_canonical_sepolia() -> None:
    u = SimpleNamespace(network="sepolia", preferred_network="mainnet")
    assert effective_user_network(u) == "sepolia"


def test_network_toggle_label_covers_all() -> None:
    assert "Mainnet" in network_toggle_label("mainnet")
    assert "Alfajores" in network_toggle_label("alfajores")
    assert "Sepolia" in network_toggle_label("sepolia")
