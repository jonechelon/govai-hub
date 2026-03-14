# scripts/test_connection.py
# Up-to-Celo — standalone connectivity test for all external services

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from project root: python scripts/test_connection.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from telegram import Bot
from telegram.error import InvalidToken, NetworkError, TelegramError
from web3 import Web3

from src.utils.env_validator import AppConfig, get_env_or_fail

# ── result tracking ────────────────────────────────────────────────────────────

RESULTS: dict[str, str] = {}


def ok(service: str, detail: str) -> None:
    RESULTS[service] = f"✅  {detail}"
    print(f"  ✅  {service}: {detail}")


def fail(service: str, detail: str) -> None:
    RESULTS[service] = f"❌  {detail}"
    print(f"  ❌  {service}: {detail}")


# ── individual checks ──────────────────────────────────────────────────────────

async def check_telegram(config: AppConfig) -> None:
    """Call get_me() and send startup message to ADMIN_CHAT_ID."""
    print("\n🔍 Telegram Bot API")
    try:
        bot = Bot(token=config.telegram_bot_token)
        async with bot:
            bot_info = await bot.get_me()
            ok("Telegram get_me()", f"@{bot_info.username} | id: {bot_info.id}")

            try:
                await bot.send_message(
                    chat_id=config.admin_chat_id,
                    text="🟡 Up-to-Celo iniciado! Connectivity test running...",
                )
                ok("Telegram send_message()", f"Message sent to admin_chat_id: {config.admin_chat_id}")
            except TelegramError as exc:
                fail("Telegram send_message()", str(exc))

    except InvalidToken:
        fail("Telegram get_me()", "InvalidToken — check TELEGRAM_BOT_TOKEN in .env")
    except NetworkError as exc:
        fail("Telegram get_me()", f"NetworkError — {exc}")


async def check_celo_rpc(config: AppConfig) -> None:
    """Connect to Celo RPC and fetch latest block number."""
    print("\n🔍 Celo RPC")
    try:
        w3 = Web3(Web3.HTTPProvider(config.celo_rpc_url, request_kwargs={"timeout": 10}))

        if not w3.is_connected():
            fail("Celo RPC is_connected()", f"Could not connect to {config.celo_rpc_url}")
            return

        block_number = w3.eth.block_number
        ok("Celo RPC block_number", f"Block #{block_number:,} | endpoint: {config.celo_rpc_url}")

    except Exception as exc:
        fail("Celo RPC", f"{type(exc).__name__}: {exc}")


async def check_coingecko() -> None:
    """Ping the CoinGecko API to confirm availability."""
    print("\n🔍 CoinGecko API")
    url = "https://api.coingecko.com/api/v3/ping"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ok("CoinGecko /ping", f"status: {resp.status} | response: {data}")
                else:
                    fail("CoinGecko /ping", f"Unexpected status: {resp.status}")
    except aiohttp.ClientError as exc:
        fail("CoinGecko /ping", f"ClientError: {exc}")
    except Exception as exc:
        fail("CoinGecko /ping", f"{type(exc).__name__}: {exc}")


async def check_defillama() -> None:
    """Ping DeFi Llama to confirm Celo chain data is available."""
    print("\n🔍 DeFi Llama API")
    url = "https://api.llama.fi/v2/chains"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    chains = await resp.json()
                    celo = next((c for c in chains if c.get("name", "").lower() == "celo"), None)
                    if celo:
                        tvl = celo.get("tvl", "N/A")
                        ok("DeFi Llama /chains", f"Celo TVL: ${float(tvl):,.0f}" if isinstance(tvl, (int, float)) else f"Celo found | TVL: {tvl}")
                    else:
                        fail("DeFi Llama /chains", "Celo chain not found in response")
                else:
                    fail("DeFi Llama /chains", f"Unexpected status: {resp.status}")
    except aiohttp.ClientError as exc:
        fail("DeFi Llama /chains", f"ClientError: {exc}")
    except Exception as exc:
        fail("DeFi Llama /chains", f"{type(exc).__name__}: {exc}")


async def check_groq(config: AppConfig) -> None:
    """Verify Groq API key is accepted (list models endpoint)."""
    print("\n🔍 Groq API")
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {config.groq_api_key}"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    model_ids = [m["id"] for m in data.get("data", [])]
                    has_llama = any("llama" in m for m in model_ids)
                    ok("Groq /models", f"Key valid | llama available: {has_llama} | total models: {len(model_ids)}")
                elif resp.status == 401:
                    fail("Groq /models", "401 Unauthorized — check GROQ_API_KEY in .env")
                else:
                    fail("Groq /models", f"Unexpected status: {resp.status}")
    except aiohttp.ClientError as exc:
        fail("Groq /models", f"ClientError: {exc}")
    except Exception as exc:
        fail("Groq /models", f"{type(exc).__name__}: {exc}")


def check_wallet_address(config: AppConfig) -> None:
    """Verify BOT_WALLET_ADDRESS is a valid EIP-55 checksum address."""
    print("\n🔍 Bot Wallet Address")
    wallet = config.bot_wallet_address
    try:
        if Web3.is_checksum_address(wallet):
            ok("Bot wallet checksum", f"Valid checksum address: {wallet}")
        else:
            # Attempt to convert to checksum for a helpful hint
            try:
                checksum = Web3.to_checksum_address(wallet)
                fail(
                    "Bot wallet checksum",
                    f"Address is not in EIP-55 checksum format. Use: {checksum}",
                )
            except Exception:
                fail("Bot wallet checksum", f"Invalid Ethereum address: {wallet}")
    except Exception as exc:
        fail("Bot wallet checksum", f"{type(exc).__name__}: {exc}")


async def check_wallet_cusd_balance(config: AppConfig) -> None:
    """Query cUSD balance of BOT_WALLET_ADDRESS on Celo Mainnet."""
    print("\n🔍 Bot Wallet cUSD Balance")
    CUSD_CONTRACT = "0x765DE816845861e75A25fCA122bb6898B8B1282a"
    ERC20_BALANCE_ABI = [
        {
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function",
        }
    ]
    try:
        w3 = Web3(Web3.HTTPProvider(config.celo_rpc_url, request_kwargs={"timeout": 10}))
        if not w3.is_connected():
            fail("Wallet cUSD balance", f"Could not connect to {config.celo_rpc_url}")
            return

        checksum_wallet = Web3.to_checksum_address(config.bot_wallet_address)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(CUSD_CONTRACT),
            abi=ERC20_BALANCE_ABI,
        )
        balance_wei = contract.functions.balanceOf(checksum_wallet).call()
        balance_cusd = balance_wei / 1e18
        ok("Wallet cUSD balance", f"💰 Bot wallet cUSD balance: {balance_cusd:.4f} cUSD")
    except Exception as exc:
        fail("Wallet cUSD balance", f"{type(exc).__name__}: {exc}")


# ── summary ────────────────────────────────────────────────────────────────────

def print_summary() -> bool:
    """Print final summary table. Returns True if all checks passed."""
    print("\n" + "─" * 60)
    print("📊 Up-to-Celo — Connectivity Test Summary")
    print("─" * 60)
    all_ok = True
    for service, result in RESULTS.items():
        print(f"  {result}  ({service})")
        if result.startswith("❌"):
            all_ok = False
    print("─" * 60)
    if all_ok:
        print("🟢 All services reachable — ready for P5+\n")
    else:
        print("🔴 One or more services failed — fix before proceeding\n")
    return all_ok


# ── entrypoint ─────────────────────────────────────────────────────────────────

async def run_all_checks(config: AppConfig) -> bool:
    """Run all connectivity checks concurrently where possible."""
    await check_telegram(config)

    # Run non-Telegram checks concurrently
    await asyncio.gather(
        check_celo_rpc(config),
        check_coingecko(),
        check_defillama(),
        check_groq(config),
    )

    # Wallet checks (synchronous address validation + async RPC balance query)
    check_wallet_address(config)
    await check_wallet_cusd_balance(config)

    return print_summary()


def main() -> None:
    print("🟡 Up-to-Celo — Connectivity Test")
    print("=" * 60)

    try:
        config = get_env_or_fail()
    except EnvironmentError as exc:
        print(f"\n❌ Environment error: {exc}")
        sys.exit(1)

    all_ok = asyncio.run(run_all_checks(config))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
