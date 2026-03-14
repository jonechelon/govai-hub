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

        Does NOT stamp the timestamp — call register_digest() after successful delivery.

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

        return True

    def register_digest(self, user_id: int) -> None:
        """Store delivery timestamp to enforce the 60-min sliding window.

        Must be called only after the digest has been successfully delivered.
        Separating check from registration ensures the cooldown is not activated
        when delivery fails (e.g. Groq unavailable, Telegram API error).

        Args:
            user_id: Telegram numeric user ID.
        """
        self._digest_state[user_id] = time.time()

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

        return True

    def register_ask(self, user_id: int) -> None:
        """Record an ask event to track daily quota.

        Must be called only after the ask has been successfully answered.
        Keeps rate limit consistent with register_digest (no quota consumed on failure).

        Args:
            user_id: Telegram numeric user ID.
        """
        now = time.time()
        state = self._ask_state.get(
            user_id, {"count": 0, "window_start": now}
        )
        if now - state["window_start"] >= ASK_WINDOW_SECONDS:
            state = {"count": 0, "window_start": now}
        state["count"] = state["count"] + 1
        self._ask_state[user_id] = state

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
