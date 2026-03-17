# Novo script: scripts/test_governance.py
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database.manager import DatabaseManager
from src.database.models import GovernanceAlert
from datetime import datetime, timezone, timedelta

async def test_governance():
    db = DatabaseManager()
    
    # Simular proposal recente (ainda não enviada)
    alert = GovernanceAlert(
        proposal_id=999,
        proposer="0x1234567890123456789012345678901234567890",
        description_url="https://forum.celo.org/t/test-proposal-999",
        deposit_celo=150.5,
        queued_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        block_number=28012345,
        tx_hash="0xdeadbeef" * 8,
        sent_at=None
    )
    
    await db.log_governance_alert({
        "proposal_id": alert.proposal_id,
        "proposer": alert.proposer,
        "description_url": alert.description_url,
        "deposit": alert.deposit_celo,
        "queued_at": alert.queued_at,
        "block_number": alert.block_number,
        "tx_hash": alert.tx_hash,
    })
    
    print("✅ Proposal 999 inserida. Aguarde próximo ciclo governance_poller (15min)")

asyncio.run(test_governance())
