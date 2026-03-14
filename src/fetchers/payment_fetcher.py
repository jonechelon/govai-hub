# src/fetchers/payment_fetcher.py
# Up-to-Celo — cUSD payment verifier via Celo RPC (web3.py)

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from web3 import Web3
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

# Minimal ERC-20 ABI — only the Transfer event is needed for log decoding
_ERC20_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    }
]

_CELO_RPC = os.getenv("CELO_RPC_URL", "https://forno.celo.org")
_VERIFY_RETRIES = 3
_VERIFY_BACKOFF_SECONDS = 5


class PaymentFetcher:
    """Verifies cUSD payments on Celo via web3.py.

    All blocking web3 calls are dispatched to an executor so the asyncio
    event loop is never blocked.
    """

    CUSD_CONTRACT: str = "0x765DE816845861e75A25fCA122bb6898B8B1282a"
    TRANSFER_TOPIC: str = Web3.keccak(text="Transfer(address,address,uint256)").hex()

    def __init__(self, rpc_url: str = _CELO_RPC) -> None:
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(self.CUSD_CONTRACT),
            abi=_ERC20_ABI,
        )

    # ── internal helpers ───────────────────────────────────────────────────────

    async def _run_sync(self, func, *args):
        """Run a blocking web3 call in the default thread-pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args)

    def _get_receipt_sync(self, tx_hash: str) -> Optional[TxReceipt]:
        try:
            return self._w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            return None

    # ── public API ─────────────────────────────────────────────────────────────

    async def verify_tx(
        self,
        tx_hash: str,
        expected_to: str,
    ) -> Optional[dict]:
        """Verify a cUSD Transfer transaction on Celo.

        Args:
            tx_hash: full 0x-prefixed transaction hash (64 hex chars).
            expected_to: wallet address that should have received the cUSD
                         (typically BOT_WALLET_ADDRESS).

        Returns:
            dict with keys ``from``, ``value_cusd`` (float), ``block`` (int)
            if the transaction is valid and matches expected_to.
            None if receipt not found, transaction failed, wrong recipient,
            or no Transfer log is present.
        """
        receipt = None
        for attempt in range(1, _VERIFY_RETRIES + 1):
            receipt = await self._run_sync(self._get_receipt_sync, tx_hash)
            if receipt is not None:
                break
            if attempt < _VERIFY_RETRIES:
                logger.debug(
                    "[PAYMENT] verify_tx receipt not found yet (attempt %d/%d) | tx: %s",
                    attempt, _VERIFY_RETRIES, tx_hash[:10],
                )
                await asyncio.sleep(_VERIFY_BACKOFF_SECONDS * attempt)

        if receipt is None:
            logger.info("[PAYMENT] verify_tx | hash: %s... | status: fail (receipt not found)", tx_hash[:10])
            return None

        # Transaction must have succeeded
        if receipt.get("status") != 1:
            logger.info("[PAYMENT] verify_tx | hash: %s... | status: fail (tx reverted)", tx_hash[:10])
            return None

        # The transaction must have been sent to the cUSD contract
        receipt_to = (receipt.get("to") or "").lower()
        if receipt_to != self.CUSD_CONTRACT.lower():
            logger.info(
                "[PAYMENT] verify_tx | hash: %s... | status: fail (to mismatch: %s)",
                tx_hash[:10], receipt_to,
            )
            return None

        # Decode Transfer logs from the cUSD contract
        transfer_log = None
        for log in receipt.get("logs", []):
            log_address = (log.get("address") or "").lower()
            if log_address != self.CUSD_CONTRACT.lower():
                continue
            topics = log.get("topics", [])
            if not topics:
                continue
            first_topic = topics[0].hex() if hasattr(topics[0], "hex") else str(topics[0])
            if first_topic.lower() != ("0x" + self.TRANSFER_TOPIC).lower() and \
               first_topic.lower() != self.TRANSFER_TOPIC.lower():
                continue
            transfer_log = log
            break

        if transfer_log is None:
            logger.info("[PAYMENT] verify_tx | hash: %s... | status: fail (no Transfer log)", tx_hash[:10])
            return None

        # Decode: topics[1]=from (indexed), topics[2]=to (indexed), data=value
        topics = transfer_log["topics"]
        from_addr = "0x" + topics[1].hex()[-40:]
        to_addr = "0x" + topics[2].hex()[-40:]

        if to_addr.lower() != expected_to.lower():
            logger.info(
                "[PAYMENT] verify_tx | hash: %s... | status: fail (Transfer.to %s != expected %s)",
                tx_hash[:10], to_addr, expected_to,
            )
            return None

        raw_value = int(transfer_log["data"].hex(), 16) if hasattr(transfer_log["data"], "hex") else int(transfer_log["data"], 16)
        value_cusd = raw_value / 1e18

        logger.info(
            "[PAYMENT] verify_tx | hash: %s... | status: ok | from: %s | value: %.4f cUSD",
            tx_hash[:10], from_addr, value_cusd,
        )
        return {
            "from": Web3.to_checksum_address(from_addr),
            "value_cusd": value_cusd,
            "block": receipt["blockNumber"],
        }

    async def watch_incoming_cusd(self, from_block: int) -> list[dict]:
        """Scan Celo logs for cUSD transfers sent to BOT_WALLET_ADDRESS.

        Args:
            from_block: the earliest block to include in the scan.

        Returns:
            List of dicts, each with keys:
            ``from`` (str), ``value_cusd`` (float), ``tx_hash`` (str), ``block`` (int).
        """
        bot_wallet = os.getenv("BOT_WALLET_ADDRESS", "")
        if not bot_wallet:
            logger.warning("[PAYMENT] BOT_WALLET_ADDRESS not set — skipping watch_incoming_cusd")
            return []

        padded_to = "0x" + "0" * 24 + bot_wallet[2:].lower()

        def _get_logs():
            return self._w3.eth.get_logs(
                {
                    "address": Web3.to_checksum_address(self.CUSD_CONTRACT),
                    "topics": [
                        "0x" + self.TRANSFER_TOPIC,
                        None,
                        padded_to,
                    ],
                    "fromBlock": from_block,
                }
            )

        try:
            logs = await self._run_sync(_get_logs)
        except Exception as exc:
            logger.warning("[PAYMENT] watch_incoming_cusd get_logs failed: %s", exc)
            return []

        to_block = from_block
        results: list[dict] = []
        for log in logs:
            topics = log["topics"]
            from_addr = "0x" + topics[1].hex()[-40:]
            raw_value = int(log["data"].hex(), 16) if hasattr(log["data"], "hex") else int(log["data"], 16)
            value_cusd = raw_value / 1e18
            tx_hash = log["transactionHash"].hex()
            block_number = log["blockNumber"]
            to_block = max(to_block, block_number)
            results.append(
                {
                    "from": Web3.to_checksum_address(from_addr),
                    "value_cusd": value_cusd,
                    "tx_hash": "0x" + tx_hash if not tx_hash.startswith("0x") else tx_hash,
                    "block": block_number,
                }
            )

        logger.info(
            "[PAYMENT] Scanned blocks %d→%d | found: %d transfers",
            from_block,
            to_block,
            len(results),
        )
        return results
