# src/utils/celo_token_registry.py
# Celo Mainnet ERC-20 addresses for DEX deep links (swap aggregators, Ubeswap, Uniswap).
# Sources: celo.blockscout.com token search + project docs; verify on-chain before changing.

from __future__ import annotations

# --- Canonical symbols (UPPERCASE) → checksum address on Celo Mainnet ---
CELO_MAINNET_TOKENS: dict[str, str] = {
    # L1 / gas / governance (CELO ERC-20 mirrors native)
    "CELO": "0x471EcE3750Da237f93B8E339c536989b8978a438",
    # Liquid staking receipt (AMM swaps — NOT StakedCelo Account 0x4aAD…)
    "STCELO": "0xC668583dcbDc9ae6FA3CE46462758188adfdfC24",
    "SCELO": "0xC668583dcbDc9ae6FA3CE46462758188adfdfC24",
    # Mento / stables (cUSD legacy address = “USDm” label in older bot copy)
    "USDm": "0x765DE816845861e75a25fca122bb6898B8B1282a",
    "CUSD": "0x765DE816845861e75a25fca122bb6898B8B1282a",
    "CEUR": "0xD8763CBa276a3738E6DE85b4b3bF5FDed6D6cA73",
    "CREAL": "0xe8537a3d056DA446677B9E9d6c5dB704EaAb4787",
    # Mento regional (on-chain symbol eXOFx; use alias ``EXOF`` → ``EXOFX``)
    "EXOFX": "0x06b6E03Bc9711eeb58D5A381a476019E19FDDEc4",
    "CKES": "0x456a3D042C0DbD3db53D5489e98dFb038553B0d0",
    # Fiat / majors
    "USDC": "0xceba9300f2b948710d2653dD7B07f33A8B32118C",
    "USDT": "0x48065fbBE25f71C9282ddf5e1cD6D6A887483D5e",
    # Bridged / wrapped
    "WETH": "0xD221812de1BD094f35587EE8E174B07B6167D9Af",
    "WBTC": "0x8aC2901Dd8A1F17a1A4768A6bA4C3751e3995B2D",
    "AXLUSDC": "0xEB466342C4d449BC9f53A865D5Cb90586f405215",
    "AXLETH": "0xb829b68f57CC546dA7E5806A929e53bE32a4625D",
    "WBNB": "0xBf2554ce8A4D1351AFeB1aC3E5545AaF7591042d",
    "WAVAX": "0xFFdb274b4909fC2efE26C8e4Ddc9fe91963cAA4d",
    "WFTM": "0xd1A342eE2210238233a347FEd61EE7Faf9f251ce",
    "WMATIC": "0x9C234706292b1144133ED509ccc5B3CD193BF712",
    # DeFi / ecosystem (Celo Blockscout-verified)
    "UBE": "0x71e26d0E519D14591b9dE9a0fE9513A398101490",
    "MOO": "0x17700282592D6917F6A73D0bF8AcCf4D578c131e",
    "UNI": "0xeE571697998ec64e32B57D754D700c4dda2f2a0e",
    "SUSHI": "0x29dFce9c22003A4999930382Fd00f9Fd6133Acd1",
    "CRV": "0x173fd7434B8B50dF08e3298f173487ebDB35FD14",
    "SYMM": "0x8427bD503dd3169cCC9aFF7326c15258Bc305478",
    "HALOFI": "0xA553FeDB4EEc005C0480172199B0B24307FFd0Ae",
    "ARI": "0x745f233f80F7ddA3073755e50Fa32F4B8A6A1574",
    "PACT": "0x46c9757C5497c5B1f2eb73aE79b6B67D119B0B58",
    # Glo Dollar (symbol on-chain USDGLO — alias GLO for UX)
    "USDGLO": "0x4F604735c1cF31399C6E711D5962b2B3E0225AD3",
    "GLO": "0x4F604735c1cF31399C6E711D5962b2B3E0225AD3",
    # ReFi / carbon / GoodDollar (G on-chain)
    "G": "0x62B8B11039FcfE5aB0C56E502b1C372A3d2a9c7A",
    "NCT": "0x02De4766C272abc10Bc88c220D214A26960a7e92",
    "MCO2": "0x7Ff9DF85a856DB9CD644Ce4032fdf60a7Ad8B0Cc",
    "CMCO2": "0x32A9FE697a32135BFd313a6Ac28792DaE4D9979d",
    "PLASTIK": "0x27cd006548dF7C8c8e9fdc4A67fa05C2E3CA5CF9",
    # Microtasks / data economy
    "JMPT": "0x1d18d0386F51ab03E7E84E71BdA1681EbA865F1f",
}

# Groq / user text may use alternate spellings → canonical key in CELO_MAINNET_TOKENS
TOKEN_SYMBOL_ALIASES: dict[str, str] = {
    "CUSD": "USDm",
    "STABLE": "USDm",
    "CUSDT": "USDT",
    "ETH": "WETH",
    "BTC": "WBTC",
    "SYRUP": "SYMM",
    "HALO": "HALOFI",
    "EXOF": "EXOFX",
    "GD": "G",
    "G$": "G",
    "GOODDOLLAR": "G",
}


def normalize_celo_token_symbol(raw: str | None) -> str | None:
    """Return canonical UPPERCASE symbol for registry lookup, or None."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip().upper().replace(" ", "")
    if not s:
        return None
    if s in TOKEN_SYMBOL_ALIASES:
        s = TOKEN_SYMBOL_ALIASES[s]
    if s in CELO_MAINNET_TOKENS:
        return s
    return None


def address_for_symbol(symbol: str | None) -> str | None:
    """Resolve checksum address for a canonical or alias symbol."""
    key = normalize_celo_token_symbol(symbol)
    if not key:
        return None
    return CELO_MAINNET_TOKENS.get(key)


def _hint_tokens_ordered(groq_hint: str) -> list[str]:
    """Extract distinct registry symbols from free text (longest-token greedy scan)."""
    hint = (groq_hint or "").upper()
    needles: list[tuple[str, str]] = []
    for k in CELO_MAINNET_TOKENS:
        if len(k) >= 3:
            needles.append((k, k))
    for alias, canon in TOKEN_SYMBOL_ALIASES.items():
        if len(alias) >= 3 and canon in CELO_MAINNET_TOKENS:
            needles.append((alias, canon))
    needles.sort(key=lambda x: len(x[0]), reverse=True)

    ordered: list[str] = []
    seen: set[str] = set()
    pos = 0
    while pos < len(hint):
        matched: tuple[str, str] | None = None
        for needle, canon in needles:
            if hint.startswith(needle, pos):
                matched = (needle, canon)
                break
        if matched is not None:
            _, canon = matched
            if canon not in seen:
                seen.add(canon)
                ordered.append(canon)
            pos += len(matched[0])
        else:
            pos += 1
    return ordered


def resolve_swap_pair_from_suggestions(
    suggestions: list[dict] | None,
    groq_hint: str = "",
) -> tuple[str, str]:
    """Pick (token_in, token_out) for DEX deep links.

    Priority:
    1. First Groq suggestion with both tokens resolvable on Celo.
    2. First two distinct symbols found in ``groq_hint`` (order preserved).
    3. Default CELO → USDm (cUSD pool).
    """
    if suggestions:
        for s in suggestions:
            if not isinstance(s, dict):
                continue
            tin = normalize_celo_token_symbol(s.get("token_in"))
            tout = normalize_celo_token_symbol(s.get("token_out"))
            if tin and tout and tin != tout:
                return tin, tout
            if tin and not tout:
                tout = "USDm"
                if tin != tout:
                    return tin, tout
            if tout and not tin:
                tin = "CELO"
                if tin != tout:
                    return tin, tout

    ordered = _hint_tokens_ordered(groq_hint)
    if len(ordered) >= 2:
        return ordered[0], ordered[1]
    if len(ordered) == 1:
        a = ordered[0]
        b = "USDm" if a != "USDm" else "USDC"
        if a != b:
            return a, b
    return "CELO", "USDm"
