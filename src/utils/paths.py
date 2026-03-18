"""
Centralized absolute path constants for the Celo GovAI Hub project.

All cache, log, and data directories are derived from PROJECT_ROOT so the
bot works correctly regardless of the working directory at runtime.

Depth: src/utils/paths.py → utils/ → src/ → project root (3 parents).
"""

from pathlib import Path

# Resolved at import time — never depends on cwd
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
DIGEST_CACHE_DIR = CACHE_DIR / "digest"   # digest_generator saves here; /ask reads here
SNAPSHOT_PATH = CACHE_DIR / "full_snapshot.json"
RSS_CACHE_PATH = CACHE_DIR / "rss_cache.json"
TWITTER_CACHE_PATH = CACHE_DIR / "twitter_cache.json"
MARKET_CACHE_PATH = CACHE_DIR / "market_cache.json"
ONCHAIN_CACHE_PATH = CACHE_DIR / "onchain_cache.json"
LOGS_DIR = DATA_DIR / "logs"

# Ensure required directories exist as soon as this module is imported
for _d in (CACHE_DIR, DIGEST_CACHE_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
