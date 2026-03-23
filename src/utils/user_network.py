# src/utils/user_network.py
# Effective Celo chain selection from User ORM rows (mainnet / alfajores / sepolia).

from __future__ import annotations

from typing import Any

_CYCLE_ORDER: tuple[str, ...] = ("mainnet", "alfajores", "sepolia")


def effective_user_network(user: Any) -> str:
    """Return chain network for UI and RPC, reconciling legacy DB rows.

    The ``network`` column (Phase 7) is canonical when set to a non-mainnet value.
    If ``network`` is still the default ``mainnet`` but ``preferred_network`` was
    set under older code, the latter is honored for alfajores / sepolia.

    Args:
        user: SQLAlchemy ``User`` instance or any object with optional
            ``network`` and ``preferred_network`` attributes.

    Returns:
        One of ``mainnet``, ``alfajores``, ``sepolia``.
    """
    net = (getattr(user, "network", None) or "mainnet").strip().lower()
    pref = (getattr(user, "preferred_network", None) or "mainnet").strip().lower()
    if net == "sepolia":
        return "sepolia"
    if net == "alfajores":
        return "alfajores"
    if net == "mainnet":
        if pref == "alfajores":
            return "alfajores"
        if pref == "sepolia":
            return "sepolia"
        return "mainnet"
    return "mainnet"


def cycle_network(current: str) -> str:
    """Advance mainnet → alfajores → sepolia → mainnet."""
    c = (current or "mainnet").strip().lower()
    if c not in _CYCLE_ORDER:
        c = "mainnet"
    i = _CYCLE_ORDER.index(c)
    return _CYCLE_ORDER[(i + 1) % len(_CYCLE_ORDER)]


def network_toggle_label(network: str) -> str:
    """Inline keyboard label for the active network (tap cycles)."""
    n = (network or "mainnet").strip().lower()
    if n == "alfajores":
        return "🍪 Alfajores"
    if n == "sepolia":
        return "🔵 Sepolia"
    return "🟡 Mainnet"
