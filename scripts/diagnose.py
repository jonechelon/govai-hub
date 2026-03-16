"""
diagnose.py

Standalone diagnostic script for Up-to-Celo.
Checks all external dependencies and prints a status table.
Run before deploy or when debugging issues.

Usage:
    python scripts/diagnose.py
    python scripts/diagnose.py --verbose    # show error details
    python scripts/diagnose.py --fast       # skip slow checks (Nitter)
"""

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Add project root so src.* imports work from scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Status icons
OK = "✅"
WARN = "⚠️ "
FAIL = "❌"

# Column widths for the status table
COL_CHECK = 42
COL_STATUS = 5
COL_LATENCY = 10
COL_DETAIL = 35


# ──────────────────────────────────────────────────────────────────────
# Result dataclass
# ──────────────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    name: str
    status: str  # OK / WARN / FAIL
    icon: str  # ✅ / ⚠️ / ❌
    detail: str = ""
    latency_ms: float = 0.0
    error: str = ""  # full error for --verbose


# ──────────────────────────────────────────────────────────────────────
# Individual check functions
# ──────────────────────────────────────────────────────────────────────


def check_env_file() -> CheckResult:
    """Verify that .env file exists in the project root."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        return CheckResult(
            name=".env file present",
            status="OK",
            icon=OK,
            detail=str(env_path),
        )
    return CheckResult(
        name=".env file present",
        status="FAIL",
        icon=FAIL,
        detail="Not found — copy .env.example",
        error=f"Expected at: {env_path}",
    )


def check_env_variables() -> list[CheckResult]:
    """Verify all required environment variables are set."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    required_vars = {
        "TELEGRAM_BOT_TOKEN": "Get from @BotFather on Telegram",
        "GROQ_API_KEY": "Get from console.groq.com",
        "ADMIN_CHAT_ID": "Your personal Telegram chat ID",
        "BOT_WALLET_ADDRESS": "Generate with web3.py — see .env.example",
        "BOT_WALLET_PRIVATE_KEY": "Generated alongside BOT_WALLET_ADDRESS",
        "CELO_RPC_URL": "Default: https://forno.celo.org",
    }

    results = []
    for var, hint in required_vars.items():
        value = os.getenv(var)
        if value and len(value.strip()) > 0:
            # Mask sensitive values for display
            if "KEY" in var or "TOKEN" in var or "PRIVATE" in var:
                display = value[:6] + "..." + value[-4:]
            else:
                display = value[:30] + ("..." if len(value) > 30 else "")
            results.append(
                CheckResult(
                    name=f"  {var}",
                    status="OK",
                    icon=OK,
                    detail=display,
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"  {var}",
                    status="FAIL",
                    icon=FAIL,
                    detail="Not set",
                    error=f"Hint: {hint}",
                )
            )
    return results


async def check_telegram_bot() -> CheckResult:
    """Verify bot token is valid by calling Bot.get_me()."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return CheckResult(
            name="Telegram Bot API",
            status="FAIL",
            icon=FAIL,
            detail="TELEGRAM_BOT_TOKEN not set",
        )

    try:
        from telegram import Bot

        start = time.perf_counter()
        bot = Bot(token=token)
        me = await bot.get_me()
        ms = (time.perf_counter() - start) * 1000

        return CheckResult(
            name="Telegram Bot API",
            status="OK",
            icon=OK,
            detail=f"@{me.username} (id={me.id})",
            latency_ms=ms,
        )
    except Exception as exc:
        return CheckResult(
            name="Telegram Bot API",
            status="FAIL",
            icon=FAIL,
            detail="Invalid token or network error",
            error=str(exc),
        )


async def check_celo_rpc() -> CheckResult:
    """Verify Celo RPC by fetching current block number."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    rpc_url = os.getenv("CELO_RPC_URL", "https://forno.celo.org")

    try:
        from web3 import Web3

        start = time.perf_counter()
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        block = w3.eth.block_number
        ms = (time.perf_counter() - start) * 1000

        return CheckResult(
            name="Celo RPC",
            status="OK",
            icon=OK,
            detail=f"Block #{block:,} via {rpc_url}",
            latency_ms=ms,
        )
    except Exception as exc:
        return CheckResult(
            name="Celo RPC",
            status="FAIL",
            icon=FAIL,
            detail=f"Unreachable: {rpc_url}",
            error=str(exc),
        )


def check_wallet_address() -> CheckResult:
    """Validate BOT_WALLET_ADDRESS checksum (EIP-55)."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    address = os.getenv("BOT_WALLET_ADDRESS", "").strip()
    if not address:
        return CheckResult(
            name="BOT_WALLET_ADDRESS (checksum)",
            status="FAIL",
            icon=FAIL,
            detail="Not set",
        )

    try:
        from web3 import Web3

        checksum = Web3.to_checksum_address(address)
        # EIP-55: checksum address must match exactly (case-sensitive)
        if checksum == address:
            return CheckResult(
                name="BOT_WALLET_ADDRESS (checksum)",
                status="OK",
                icon=OK,
                detail=f"{address[:10]}...{address[-6:]}",
            )
        return CheckResult(
            name="BOT_WALLET_ADDRESS (checksum)",
            status="WARN",
            icon=WARN,
            detail="Valid but not checksummed",
            error=f"Expected: {checksum}",
        )
    except Exception as exc:
        return CheckResult(
            name="BOT_WALLET_ADDRESS (checksum)",
            status="FAIL",
            icon=FAIL,
            detail="Invalid address format",
            error=str(exc),
        )


async def check_coingecko() -> CheckResult:
    """Verify CoinGecko API by fetching CELO price."""
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=celo&vs_currencies=usd&include_24hr_change=true"
    )
    try:
        import aiohttp

        start = time.perf_counter()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                ms = (time.perf_counter() - start) * 1000
                data = await resp.json()

        price = data.get("celo", {}).get("usd")
        change = data.get("celo", {}).get("usd_24h_change", 0)

        if price:
            return CheckResult(
                name="CoinGecko API",
                status="OK",
                icon=OK,
                detail=f"CELO=${price:.4f} ({change:+.2f}%)",
                latency_ms=ms,
            )
        return CheckResult(
            name="CoinGecko API",
            status="WARN",
            icon=WARN,
            detail="Response OK but no CELO price",
            latency_ms=ms,
        )
    except Exception as exc:
        return CheckResult(
            name="CoinGecko API",
            status="FAIL",
            icon=FAIL,
            detail="Unreachable or rate-limited",
            error=str(exc),
        )


async def check_defillama() -> CheckResult:
    """Verify DeFi Llama API by fetching Celo TVL."""
    url = "https://api.llama.fi/v2/chains"
    try:
        import aiohttp

        start = time.perf_counter()
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                ms = (time.perf_counter() - start) * 1000
                data = await resp.json()

        # Find Celo in the chains list
        celo = next(
            (c for c in data if c.get("name", "").lower() == "celo"),
            None,
        )
        if celo:
            tvl = celo.get("tvl", 0)
            return CheckResult(
                name="DeFi Llama API",
                status="OK",
                icon=OK,
                detail=f"Celo TVL: ${tvl:,.0f}",
                latency_ms=ms,
            )
        return CheckResult(
            name="DeFi Llama API",
            status="WARN",
            icon=WARN,
            detail="Response OK but Celo not found",
            latency_ms=ms,
        )
    except Exception as exc:
        return CheckResult(
            name="DeFi Llama API",
            status="FAIL",
            icon=FAIL,
            detail="Unreachable",
            error=str(exc),
        )


async def check_nitter_instances() -> CheckResult:
    """
    Try each Nitter instance in order and report the first working one.
    Returns WARN (not FAIL) if all fail — digest continues via RSS.
    """
    instances = [
        "https://xcancel.com",
        "https://nitter.privacydev.net",
        "https://nitter.poast.org",
        "https://nitter.net",
    ]

    try:
        import aiohttp

        for instance in instances:
            try:
                test_url = f"{instance}/CeloOrg/rss"
                start = time.perf_counter()
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        test_url,
                        timeout=aiohttp.ClientTimeout(total=8),
                        headers={"User-Agent": "Mozilla/5.0"},
                    ) as resp:
                        ms = (time.perf_counter() - start) * 1000
                        if resp.status == 200:
                            return CheckResult(
                                name="Nitter (Twitter RSS)",
                                status="OK",
                                icon=OK,
                                detail=f"Working: {instance}",
                                latency_ms=ms,
                            )
            except Exception:
                continue

        # All instances failed — WARN not FAIL (digest continues via RSS)
        return CheckResult(
            name="Nitter (Twitter RSS)",
            status="WARN",
            icon=WARN,
            detail="All instances unreachable — digest uses RSS only",
            error="Tried: " + ", ".join(instances),
        )
    except Exception as exc:
        return CheckResult(
            name="Nitter (Twitter RSS)",
            status="WARN",
            icon=WARN,
            detail="Check failed",
            error=str(exc),
        )


async def check_groq() -> CheckResult:
    """Verify Groq API by making a minimal completion request."""
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return CheckResult(
            name="Groq API (llama-3.3-70b)",
            status="FAIL",
            icon=FAIL,
            detail="GROQ_API_KEY not set",
        )

    try:
        from groq import AsyncGroq

        start = time.perf_counter()
        client = AsyncGroq(api_key=api_key)

        # Minimal request — max_tokens=5 to minimize cost
        resp = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Reply: OK"}],
            max_tokens=5,
        )
        ms = (time.perf_counter() - start) * 1000
        content = resp.choices[0].message.content or ""
        tokens = resp.usage.total_tokens if resp.usage else 0

        return CheckResult(
            name="Groq API (llama-3.3-70b)",
            status="OK",
            icon=OK,
            detail=f"Response: '{content.strip()[:20]}' | {tokens} tokens",
            latency_ms=ms,
        )
    except Exception as exc:
        error_str = str(exc)
        # Distinguish auth errors from network errors
        if "401" in error_str or "invalid_api_key" in error_str.lower():
            detail = "Invalid API key"
        elif "429" in error_str or "rate_limit" in error_str.lower():
            return CheckResult(
                name="Groq API (llama-3.3-70b)",
                status="WARN",
                icon=WARN,
                detail="Rate limited — key valid but quota exceeded",
                error=error_str,
            )
        else:
            detail = "Unreachable or service error"

        return CheckResult(
            name="Groq API (llama-3.3-70b)",
            status="FAIL",
            icon=FAIL,
            detail=detail,
            error=error_str,
        )


# ──────────────────────────────────────────────────────────────────────
# Display helpers
# ──────────────────────────────────────────────────────────────────────


def _print_header() -> None:
    width = COL_CHECK + COL_STATUS + COL_LATENCY + COL_DETAIL + 6
    print()
    print("=" * width)
    print("  Up-to-Celo — Dependency Diagnostic")
    print("=" * width)
    print(
        f"  {'Check':<{COL_CHECK}} "
        f"{'Status':<{COL_STATUS}} "
        f"{'Latency':>{COL_LATENCY}} "
        f"  {'Detail':<{COL_DETAIL}}"
    )
    print("-" * width)


def _print_result(r: CheckResult, verbose: bool = False) -> None:
    latency = f"{r.latency_ms:.0f}ms" if r.latency_ms > 0 else "—"
    detail = r.detail[:COL_DETAIL]

    print(
        f"  {r.name:<{COL_CHECK}} "
        f"{r.icon:<{COL_STATUS}} "
        f"{latency:>{COL_LATENCY}} "
        f"  {detail}"
    )
    # Show full error in verbose mode
    if verbose and r.error:
        print(f"    {'':>{COL_CHECK}} └─ {r.error[:80]}")


def _print_section(title: str) -> None:
    print(f"\n  {title}")
    print("  " + "─" * (COL_CHECK + COL_STATUS + COL_LATENCY + COL_DETAIL + 4))


def _print_summary(results: list[CheckResult]) -> None:
    ok_count = sum(1 for r in results if r.status == "OK")
    warn_count = sum(1 for r in results if r.status == "WARN")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    total = len(results)

    width = COL_CHECK + COL_STATUS + COL_LATENCY + COL_DETAIL + 6
    print()
    print("=" * width)
    print(
        f"  Summary: {ok_count}/{total} passed | "
        f"{warn_count} warnings | {fail_count} failures"
    )

    if fail_count == 0 and warn_count == 0:
        print("  All checks passed — bot is ready to run.")
    elif fail_count == 0:
        print("  No critical failures — bot can run with degraded features.")
    else:
        print("  Fix FAIL items before deploying.")
        print()
        print("  Failed checks:")
        for r in results:
            if r.status == "FAIL":
                print(f"    {FAIL} {r.name}: {r.detail}")
    print("=" * width)
    print()


# ──────────────────────────────────────────────────────────────────────
# Main runner
# ──────────────────────────────────────────────────────────────────────


async def run_diagnostics(verbose: bool, fast: bool) -> int:
    """
    Run all checks sequentially and print results.
    Returns exit code: 0 = all OK, 1 = at least one FAIL.
    """
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    all_results: list[CheckResult] = []

    _print_header()

    # ── Section 1: Environment ───────────────────────────────────────
    _print_section("1. Environment")

    env_result = check_env_file()
    _print_result(env_result, verbose)
    all_results.append(env_result)

    var_results = check_env_variables()
    for r in var_results:
        _print_result(r, verbose)
        all_results.append(r)

    wallet_result = check_wallet_address()
    _print_result(wallet_result, verbose)
    all_results.append(wallet_result)

    # ── Section 2: Telegram ──────────────────────────────────────────
    _print_section("2. Telegram")

    tg_result = await check_telegram_bot()
    _print_result(tg_result, verbose)
    all_results.append(tg_result)

    # ── Section 3: Blockchain ────────────────────────────────────────
    _print_section("3. Blockchain")

    rpc_result = await check_celo_rpc()
    _print_result(rpc_result, verbose)
    all_results.append(rpc_result)

    # ── Section 4: Market Data APIs ──────────────────────────────────
    _print_section("4. Market Data")

    cg_result = await check_coingecko()
    _print_result(cg_result, verbose)
    all_results.append(cg_result)

    dl_result = await check_defillama()
    _print_result(dl_result, verbose)
    all_results.append(dl_result)

    # ── Section 5: Twitter/Nitter ────────────────────────────────────
    _print_section("5. Twitter / Nitter")

    if fast:
        nitter_result = CheckResult(
            name="Nitter (Twitter RSS)",
            status="WARN",
            icon=WARN,
            detail="Skipped (--fast mode)",
        )
    else:
        nitter_result = await check_nitter_instances()
    _print_result(nitter_result, verbose)
    all_results.append(nitter_result)

    # ── Section 6: AI ────────────────────────────────────────────────
    _print_section("6. AI (Groq)")

    groq_result = await check_groq()
    _print_result(groq_result, verbose)
    all_results.append(groq_result)

    # ── Summary ──────────────────────────────────────────────────────
    _print_summary(all_results)

    # Exit code 1 if any FAIL, 0 otherwise
    has_failures = any(r.status == "FAIL" for r in all_results)
    return 1 if has_failures else 0


# ──────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Up-to-Celo — Dependency diagnostic tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/diagnose.py\n"
            "  python scripts/diagnose.py --verbose\n"
            "  python scripts/diagnose.py --fast\n"
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show full error messages for failed checks",
    )
    parser.add_argument(
        "--fast",
        "-f",
        action="store_true",
        help="Skip slow checks (Nitter — can take up to 30s)",
    )
    args = parser.parse_args()

    try:
        exit_code = asyncio.run(
            run_diagnostics(verbose=args.verbose, fast=args.fast)
        )
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nDiagnostic interrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
