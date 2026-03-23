"""
test_digest.py

Standalone script to manually test the full digest pipeline.
Runs outside the bot process — useful for debugging and pre-deploy validation.

Usage:
    python scripts/test_digest.py           # terminal only
    python scripts/test_digest.py --send    # terminal + send to ADMIN_CHAT_ID
"""

import argparse
import asyncio
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path so src.* imports work from scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ------------------------------------------------------------------
# Groq pricing reference (update if model pricing changes)
# Source: console.groq.com/docs/openai
# ------------------------------------------------------------------
GROQ_PRICING: dict[str, dict[str, float]] = {
    "llama-3.3-70b-versatile": {
        "input_per_1m": 0.59,   # USD per 1M input tokens
        "output_per_1m": 0.79,  # USD per 1M output tokens
    },
    "llama-3.1-8b-instant": {
        "input_per_1m": 0.05,
        "output_per_1m": 0.08,
    },
}

# Default model used for digest generation
DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Report output directory
REPORTS_DIR = PROJECT_ROOT / "data" / "reports"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _estimate_cost(
    prompt_tokens: int,
    output_tokens: int,
    model: str = DEFAULT_MODEL,
) -> tuple[float, float]:
    """
    Estimate Groq API cost for a single generation call.
    Returns (total_usd, output_usd) — output cost dominates.
    """
    pricing = GROQ_PRICING.get(model, GROQ_PRICING[DEFAULT_MODEL])
    input_cost = (prompt_tokens / 1_000_000) * pricing["input_per_1m"]
    output_cost = (output_tokens / 1_000_000) * pricing["output_per_1m"]
    return input_cost + output_cost, output_cost


def _format_separator(char: str = "─", width: int = 60) -> str:
    return char * width


def _print_section(title: str) -> None:
    print(f"\n{_format_separator()}")
    print(f"  {title}")
    print(_format_separator())


def _save_report(content: str, timestamp: str) -> Path:
    """Save the test report to data/reports/test_digest_{timestamp}.txt."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"test_digest_{timestamp}.txt"
    report_path.write_text(content, encoding="utf-8")
    return report_path


# ------------------------------------------------------------------
# Core test runner
# ------------------------------------------------------------------

async def run_test(send_to_admin: bool = False) -> None:
    """
    Execute the full digest pipeline and display results.
    """
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    from src.utils.env_validator import get_env_or_fail
    from src.utils.logger import setup_logger
    from src.database.manager import db
    from src.fetchers.fetcher_manager import fetcher_manager
    from src.ai.digest_builder import DigestBuilder
    from src.ai.prompt_builder import prompt_builder
    from src.ai.groq_client import groq_client
    from src.database.models import APPS_AVAILABLE

    setup_logger()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_lines: list[str] = []

    def log(line: str = "") -> None:
        """Print to terminal and append to report buffer."""
        print(line)
        report_lines.append(line)

    log("Celo GovAI Hub — Digest Test")
    log(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log(_format_separator("="))

    # ------------------------------------------------------------------
    # Step 1 — Initialize DB
    # ------------------------------------------------------------------
    _print_section("Step 1/5 — Initializing database")
    await db.init_db()
    log("  DB initialized")

    # ------------------------------------------------------------------
    # Step 2 — Fetch all sources
    # ------------------------------------------------------------------
    _print_section("Step 2/5 — Fetching all sources")
    log("  Running fetcher_manager.fetch_all_sources()...")
    fetch_start = time.perf_counter()

    snapshot = await fetcher_manager.fetch_all_sources()

    fetch_elapsed = time.perf_counter() - fetch_start
    rss_count = len(snapshot.get("rss", []))
    twitter_count = len(snapshot.get("twitter", []))
    market_ok = "ok" if snapshot.get("market") else "FAILED"
    onchain_ok = "ok" if snapshot.get("onchain") else "FAILED"

    log(f"  RSS items:     {rss_count}")
    log(f"  Twitter items: {twitter_count}")
    log(f"  Market data:   {market_ok}")
    log(f"  On-chain data: {onchain_ok}")
    log(f"  Fetch time:    {fetch_elapsed:.2f}s")

    if market_ok == "ok":
        market = snapshot["market"]
        log(
            f"  CELO price:    ${market.get('price', 0):.4f} "
            f"({market.get('pct_24h', 0):+.2f}%)"
        )
    if onchain_ok == "ok":
        onchain = snapshot["onchain"]
        log(f"  Block number:  #{onchain.get('block_number', 'N/A'):,}")

    # ------------------------------------------------------------------
    # Step 3 — Build context with ALL apps enabled
    # ------------------------------------------------------------------
    _print_section("Step 3/5 — Building digest context (all apps)")

    # Use all available apps across all categories
    all_apps_by_category: dict[str, list[str]] = {
        cat: list(apps) for cat, apps in APPS_AVAILABLE.items()
    }
    total_apps = sum(len(v) for v in all_apps_by_category.values())
    log(f"  Apps enabled:  {total_apps} (all categories)")

    builder = DigestBuilder()
    context, context_sections = builder.build_context(
        snapshot=snapshot,
        user_apps_by_category=all_apps_by_category,
    )

    sections_count = len(context_sections)
    items_count = sum(
        len(s.get("items", [])) for s in context_sections
    )
    # Rough token estimate: ~1 token per 4 chars
    context_tokens = len(context) // 4

    log(f"  Sections:      {sections_count}")
    log(f"  Items:         {items_count}")
    log(f"  Context chars: {len(context):,}")
    log(f"  Context tokens (est.): ~{context_tokens:,}")

    for section in context_sections:
        cat = section.get("category", "unknown")
        n_items = len(section.get("items", []))
        log(f"    [{cat}] {n_items} item(s)")

    # ------------------------------------------------------------------
    # Step 4 — Build prompt and call Groq
    # ------------------------------------------------------------------
    _print_section("Step 4/5 — Generating digest via Groq")

    messages = prompt_builder.build_digest_prompt(context)

    log(f"  Model:         {DEFAULT_MODEL}")
    log("  Calling Groq...")

    groq_start = time.perf_counter()
    total_cost = 0.0

    try:
        digest_text, usage = await groq_client.generate(
            messages=messages, max_tokens=600, return_usage=True
        )
        groq_elapsed = time.perf_counter() - groq_start
        prompt_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        groq_ok = True
        total_cost, output_cost = _estimate_cost(
            prompt_tokens, output_tokens, DEFAULT_MODEL
        )
        input_cost = total_cost - output_cost
        log(f"  Prompt tokens: {prompt_tokens:,}")
        log(f"  Output tokens: {output_tokens:,}")
        log(f"  Total tokens:  {total_tokens:,}")
        log(f"  Latency:       {groq_elapsed:.2f}s")
        log(
            f"  Est. cost:     ${total_cost:.6f} USD "
            f"(input: ${input_cost:.6f} | output: ${output_cost:.6f})"
        )
    except Exception as exc:
        log(f"  ERROR: Groq call failed — {exc}")
        groq_ok = False
        digest_text = ""
        total_tokens = 0
        groq_elapsed = time.perf_counter() - groq_start

    # ------------------------------------------------------------------
    # Step 5 — Display digest
    # ------------------------------------------------------------------
    _print_section("Step 5/5 — Generated digest")

    if groq_ok and digest_text:
        log(digest_text)
    else:
        log("  [No digest generated — Groq call failed]")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _print_section("Summary")
    log(f"  RSS items fetched:    {rss_count}")
    log(f"  Twitter items:        {twitter_count}")
    log(f"  Sections in digest:   {sections_count}")
    log(f"  Items in context:     {items_count}")
    log(f"  Groq tokens used:     {total_tokens:,}")
    if groq_ok:
        daily_cost_30 = total_cost * 30
        monthly_cost = total_cost * 30 * 30
        log(f"  Cost this call:       ${total_cost:.6f} USD")
        log(f"  Est. daily (30 users): ${daily_cost_30:.4f} USD")
        log(f"  Est. monthly (30/day): ${monthly_cost:.4f} USD")
    log(_format_separator("="))

    # ------------------------------------------------------------------
    # Save report to data/reports/
    # ------------------------------------------------------------------
    report_content = "\n".join(report_lines)
    report_path = _save_report(report_content, timestamp)
    print(f"\n  Report saved: {report_path}")

    # ------------------------------------------------------------------
    # Optional: send to ADMIN_CHAT_ID
    # ------------------------------------------------------------------
    if send_to_admin and groq_ok and digest_text:
        print("\n  Sending digest to ADMIN_CHAT_ID...")
        try:
            from telegram import Bot
            from src.bot.keyboards import get_digest_keyboard

            bot_token = get_env_or_fail("TELEGRAM_BOT_TOKEN")
            admin_id = int(get_env_or_fail("ADMIN_CHAT_ID"))
            digest_id = uuid.uuid4().hex[:8]

            bot = Bot(token=bot_token)
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"[TEST DIGEST — {timestamp}]\n\n"
                    f"{digest_text}"
                ),
                reply_markup=get_digest_keyboard(digest_id, 0),
            )
            print(f"  Sent to admin ({admin_id}) | digest_id={digest_id}")
        except Exception as exc:
            print(f"  ERROR sending to admin: {exc}")
    elif send_to_admin and not groq_ok:
        print("  --send skipped: Groq call failed, nothing to send.")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Celo GovAI Hub — Manual digest test script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/test_digest.py\n"
            "  python scripts/test_digest.py --send\n"
        ),
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send the generated digest to ADMIN_CHAT_ID after generation",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run_test(send_to_admin=args.send))
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(0)
    except Exception as exc:
        print(f"\nFATAL ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
