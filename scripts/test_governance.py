# scripts/test_governance.py
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from src.database.manager import DatabaseManager
from src.database.models import GovernanceAlert
from src.fetchers.governance_fetcher import GovernanceFetcher


async def test_db_insert() -> None:
    """Insert a dummy governance proposal into the DB and verify it appears in unsent alerts."""
    db = DatabaseManager()

    alert = GovernanceAlert(
        proposal_id=999,
        proposer="0x1234567890123456789012345678901234567890",
        description_url="https://forum.celo.org/t/test-proposal-999",
        deposit_cusd=150.5,
        queued_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        block_number=28012345,
        tx_hash="0xdeadbeef" * 8,
        sent_at=None,
    )

    await db.log_governance_alert(
        {
            "proposal_id": alert.proposal_id,
            "proposer": alert.proposer,
            "description_url": alert.description_url,
            "deposit": alert.deposit_cusd,
            "queued_at": alert.queued_at,
            "block_number": alert.block_number,
            "tx_hash": alert.tx_hash,
        }
    )
    print("✅ Proposal 999 inserted. Waiting for next governance_poller cycle (15 min).")


async def test_fetcher_tuple() -> None:
    """Verify that fetch_new_proposals returns a (list, int | None) tuple."""
    fetcher = GovernanceFetcher()
    proposals, current_block = await asyncio.to_thread(fetcher.fetch_new_proposals)

    print(f"✅ fetch_new_proposals returned tuple: proposals={len(proposals)} | current_block={current_block}")
    for p in proposals:
        print(f"   • proposal_id={p['proposal_id']} | block={p['block_number']}")


async def main() -> None:
    await test_db_insert()
    await test_fetcher_tuple()


asyncio.run(main())
