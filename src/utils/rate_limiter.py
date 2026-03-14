import logging
import time

logger = logging.getLogger(__name__)

DIGEST_COOLDOWN_SECONDS: int = 3600
ASK_DAILY_LIMIT_FREE: int = 3
ASK_WINDOW_SECONDS: int = 86400


class RateLimiter:
    """In-memory rate limiter for /digest and /ask commands."""

    def __init__(self) -> None:
        self._digest_state: dict[int, float] = {}
        self._ask_state: dict[int, dict] = {}

    def check_digest(self, user_id: int) -> bool:
        """Check whether a user can trigger /digest now.

        Args:
            user_id: Telegram numeric user ID.

        Returns:
            True if the user can run /digest now, False if still in cooldown.
        """
        now = time.time()
        last_ts = self._digest_state.get(user_id, 0.0)

        if now - last_ts < DIGEST_COOLDOWN_SECONDS:
            remaining = DIGEST_COOLDOWN_SECONDS - (now - last_ts)
            logger.info(
                "[RATELIMIT] user=%s hit digest cooldown | remaining=%.0fs",
                user_id,
                remaining,
            )
            return False

        self._digest_state[user_id] = now
        return True

    def check_ask(self, user_id: int, is_premium: bool) -> bool:
        """Check whether a user can trigger /ask now.

        Premium users have unlimited access. Free users are limited by a
        sliding 24-hour window.

        Args:
            user_id: Telegram numeric user ID.
            is_premium: Whether the user has an active premium subscription.

        Returns:
            True if the user can run /ask now, False if rate-limited.
        """
        if is_premium:
            return True

        now = time.time()
        state = self._ask_state.get(
            user_id, {"count": 0, "window_start": now}
        )

        if now - state["window_start"] >= ASK_WINDOW_SECONDS:
            state = {"count": 0, "window_start": now}

        if state["count"] >= ASK_DAILY_LIMIT_FREE:
            resets_in = state["window_start"] + ASK_WINDOW_SECONDS - now
            logger.info(
                "[RATELIMIT] user=%s hit ask limit | "
                "count=%s/%s | resets_in=%.0fs",
                user_id,
                state["count"],
                ASK_DAILY_LIMIT_FREE,
                resets_in,
            )
            return False

        state["count"] += 1
        self._ask_state[user_id] = state
        return True

    def get_remaining(self, user_id: int, action: str) -> int:
        """Return how many requests a user still has available for an action.

        This does not consume any quota.

        Args:
            user_id: Telegram numeric user ID.
            action: Action name: "digest" or "ask".

        Returns:
            Remaining requests for the given action.
        """
        now = time.time()

        if action == "digest":
            last_ts = self._digest_state.get(user_id, 0.0)
            elapsed = now - last_ts
            if elapsed >= DIGEST_COOLDOWN_SECONDS:
                return 1
            return 0

        if action == "ask":
            state = self._ask_state.get(
                user_id, {"count": 0, "window_start": now}
            )
            if now - state["window_start"] >= ASK_WINDOW_SECONDS:
                return ASK_DAILY_LIMIT_FREE
            return max(0, ASK_DAILY_LIMIT_FREE - state["count"])

        logger.warning("[RATELIMIT] Unknown action: %s", action)
        return 0

    def get_digest_wait_seconds(self, user_id: int) -> int:
        """Return how many seconds remain for the /digest cooldown.

        Args:
            user_id: Telegram numeric user ID.

        Returns:
            Remaining cooldown in seconds (0 if the user can run /digest now).
        """
        now = time.time()
        last_ts = self._digest_state.get(user_id, 0.0)
        remaining = DIGEST_COOLDOWN_SECONDS - (now - last_ts)
        return max(0, int(remaining))

    def reset_digest(self, user_id: int) -> None:
        """Reset the /digest cooldown for a specific user."""
        self._digest_state.pop(user_id, None)


rate_limiter = RateLimiter()
# src/utils/rate_limiter.py
# Up-to-Celo — in-memory sliding-window rate limiter

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Deque

logger = logging.getLogger(__name__)

# Free tier: max 3 /ask queries per day (86 400 seconds)
_ASK_FREE_MAX: int = 3
_ASK_FREE_WINDOW_SECONDS: int = 86_400  # 24 hours

# Digest manual cooldown: 1 request per hour
_DIGEST_COOLDOWN_SECONDS: int = 3_600  # 60 minutes


class RateLimiter:
    """In-memory sliding-window rate limiter for bot commands.

    Thread-safe via the GIL for single-process deployments.
    State is lost on restart (acceptable for rate limiting purposes).
    """

    _instance: RateLimiter | None = None

    def __new__(cls) -> RateLimiter:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        # ask: {user_id: deque of timestamps}
        self._ask_log: dict[int, Deque[float]] = defaultdict(deque)
        # digest: {user_id: last request timestamp}
        self._digest_log: dict[int, float] = {}
        self._initialized = True

    # ── /ask rate limit ────────────────────────────────────────────────────────

    async def check_ask(self, user_id: int, is_premium: bool) -> bool:
        """Check whether a user is allowed to run /ask.

        Free users are limited to 3 queries per 24 hours.
        Premium users have unlimited access (fair use — no hard cap).

        Args:
            user_id: Telegram numeric user ID.
            is_premium: True if the user currently has an active premium subscription.

        Returns:
            True if the request is allowed, False if rate-limited.
        """
        if is_premium:
            return True

        now = time.monotonic()
        window_start = now - _ASK_FREE_WINDOW_SECONDS
        history = self._ask_log[user_id]

        # Evict entries outside the window
        while history and history[0] < window_start:
            history.popleft()

        if len(history) >= _ASK_FREE_MAX:
            logger.info("[RATELIMIT] user %d hit ask limit | tier: free", user_id)
            return False

        history.append(now)
        return True

    def ask_remaining(self, user_id: int) -> int:
        """Return how many /ask queries the user has left in the current window.

        Args:
            user_id: Telegram numeric user ID.

        Returns:
            Remaining queries (0 if rate-limited, _ASK_FREE_MAX for new users).
        """
        now = time.monotonic()
        window_start = now - _ASK_FREE_WINDOW_SECONDS
        history = self._ask_log[user_id]
        while history and history[0] < window_start:
            history.popleft()
        return max(0, _ASK_FREE_MAX - len(history))

    # ── /digest manual cooldown ────────────────────────────────────────────────

    async def check_digest(self, user_id: int) -> bool:
        """Check whether a user may request a manual digest.

        Cooldown is 1 hour per user regardless of tier.

        Args:
            user_id: Telegram numeric user ID.

        Returns:
            True if allowed, False if still in cooldown.
        """
        now = time.monotonic()
        last = self._digest_log.get(user_id)

        if last is not None and (now - last) < _DIGEST_COOLDOWN_SECONDS:
            remaining = int(_DIGEST_COOLDOWN_SECONDS - (now - last))
            logger.info(
                "[RATELIMIT] user %d hit digest cooldown | %ds remaining",
                user_id,
                remaining,
            )
            return False

        self._digest_log[user_id] = now
        return True

    def digest_cooldown_remaining(self, user_id: int) -> int:
        """Return seconds remaining in the digest cooldown for a user.

        Args:
            user_id: Telegram numeric user ID.

        Returns:
            Seconds until the user can request another digest (0 if no cooldown).
        """
        now = time.monotonic()
        last = self._digest_log.get(user_id)
        if last is None:
            return 0
        remaining = _DIGEST_COOLDOWN_SECONDS - (now - last)
        return max(0, int(remaining))
