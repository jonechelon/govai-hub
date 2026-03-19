from __future__ import annotations

import asyncio
import json
import logging
import time

from groq import AsyncGroq

from src.utils.env_validator import get_env_or_fail

logger = logging.getLogger(__name__)

GROQ_MODELS: list[str] = [
    "llama-3.3-70b-versatile",  # primary — best quality
    "llama-3.1-8b-instant",     # fallback 1 — faster, lower quality
    "mixtral-8x7b-32768",       # fallback 2 — last resort
]


class GroqClient:
    """Async Groq API client with automatic model fallback.

    Uses a waterfall strategy: if the requested model fails, the next model
    in GROQ_MODELS is tried after an exponential backoff delay. Temperature is
    fixed at 0.3 to ensure consistent, factual tone across all digest outputs.
    """

    TEMPERATURE: float = 0.3
    TIMEOUT: float = 30.0           # hard cancel via asyncio.wait_for
    BACKOFF: list[int] = [1, 2, 4]  # seconds between retries per attempt index

    def __init__(self) -> None:
        api_key = get_env_or_fail("GROQ_API_KEY")
        self._client = AsyncGroq(api_key=api_key)

    async def generate(
        self,
        messages: list[dict],
        max_tokens: int,
        model: str | None = None,
        return_usage: bool = False,
    ) -> str | tuple[str, dict]:
        """Call the Groq Chat Completions API with automatic model fallback.

        Args:
            messages:     OpenAI-style messages list (role/content dicts).
            max_tokens:   Maximum tokens allowed in the completion.
            model:       If provided, start the fallback chain from this model.
                         If None, start from the first model in GROQ_MODELS.
            return_usage: If True, return (text, usage_dict) instead of just text.
                          usage_dict keys: prompt_tokens, completion_tokens, total_tokens.

        Returns:
            The generated text content string, or (text, usage_dict) if return_usage=True.
            Text may be truncated if finish_reason=length.

        Raises:
            RuntimeError: If every model in the fallback chain fails.
        """
        if model and model in GROQ_MODELS:
            start_idx = GROQ_MODELS.index(model)
            models_to_try = GROQ_MODELS[start_idx:]
        else:
            models_to_try = GROQ_MODELS

        last_error: Exception | None = None

        for attempt, current_model in enumerate(models_to_try):
            try:
                content, usage = await self._call(current_model, messages, max_tokens)
                if return_usage:
                    return content, usage
                return content
            except Exception as exc:
                last_error = exc
                has_next = attempt < len(models_to_try) - 1
                wait = self.BACKOFF[min(attempt, len(self.BACKOFF) - 1)]
                logger.warning(
                    f"[GROQ] model={current_model} failed (attempt {attempt + 1}): {exc} "
                    f"— {'trying next model' if has_next else 'no more fallbacks'}"
                )
                if has_next:
                    await asyncio.sleep(wait)

        raise RuntimeError(f"All Groq models failed. Last error: {last_error}")

    async def _call(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
    ) -> tuple[str, dict]:
        """Execute a single Groq API call with timeout enforcement.

        asyncio.wait_for is used instead of the SDK's native timeout because
        the SDK timeout may not fire if the TCP connection stalls after headers
        are sent. wait_for cancels the coroutine at the event-loop level,
        guaranteeing the 30 s ceiling is always respected.

        Args:
            model:      Groq model identifier to use for this call.
            messages:   OpenAI-style messages list.
            max_tokens: Maximum tokens allowed in the completion.

        Returns:
            Tuple of (content, usage_dict). usage_dict has keys:
            prompt_tokens, completion_tokens, total_tokens.
            content is empty string if the model returns None.

        Raises:
            asyncio.TimeoutError: If the call exceeds TIMEOUT seconds.
            groq.APIError: On API-level errors (rate limit, invalid key, etc.).
        """
        start = time.time()

        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=self.TEMPERATURE,
            ),
            timeout=self.TIMEOUT,
        )

        content: str = response.choices[0].message.content or ""

        usage = response.usage
        total_tokens = usage.total_tokens if usage else 0
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        finish_reason = response.choices[0].finish_reason

        latency_ms = int((time.time() - start) * 1000)

        usage_dict = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

        logger.info(
            "[GROQ] model=%s | tokens=%s (prompt=%s) | latency=%sms | finish=%s",
            model,
            total_tokens,
            prompt_tokens,
            latency_ms,
            finish_reason,
        )

        if finish_reason == "length":
            logger.warning(
                f"[GROQ] Response truncated (finish=length) — consider increasing max_tokens "
                f"or reducing context. model={model} max_tokens={max_tokens}"
            )

        return content, usage_dict

    async def generate_with_system(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        model: str | None = None,
    ) -> str:
        """Convenience wrapper that builds the messages array and calls generate().

        Args:
            system_prompt: Content for the system role message.
            user_prompt:   Content for the user role message.
            max_tokens:    Maximum tokens allowed in the completion.
            model:         Optional starting model for the fallback chain.

        Returns:
            The generated text content string.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return await self.generate(messages, max_tokens, model)


# Module-level singleton — imported by DigestGenerator, ask_handler, notifier
groq_client = GroqClient()


async def generate_proposal_summary(text: str) -> dict:
    """Extract metadata and generate an ELI5 summary from a Celo governance proposal.

    The LLM is instructed to respond exclusively with a valid JSON object
    containing proposal metadata fields and a plain-language ELI5 summary.

    Args:
        text: Cleaned proposal description text extracted from the source URL.

    Returns:
        A dict with keys: title, date_created, author, status, discussions_to,
        date_executed, summary. Any missing field defaults to 'N/A'.
        Returns a fallback dict if JSON parsing fails.
    """
    system_prompt = (
        "You are a Celo blockchain data extractor and analyst. "
        "Your task is to read a Celo governance proposal text and extract structured metadata. "
        "You MUST respond EXCLUSIVELY with a single valid JSON object — no extra text, no markdown fences. "
        "The JSON object must contain exactly these keys: "
        "\"title\", \"date_created\", \"author\", \"status\", "
        "\"discussions_to\", \"date_executed\", \"summary\". "
        "If a specific metadata field is not found in the text, return \"N/A\" for that field. "
        "The \"summary\" field must contain an ELI5 (Explain Like I'm 5) explanation in exactly "
        "3 bullet points answering: 1. What changes? 2. How much does it cost? "
        "3. Why does it matter to the ecosystem?"
    )
    user_prompt = f"Proposal text:\n\n{text}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    # Increased token budget to accommodate JSON envelope + 3-bullet summary.
    content, _usage = await groq_client._call(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=500,
    )

    _EXPECTED_KEYS = ("title", "date_created", "author", "status", "discussions_to", "date_executed", "summary")
    _FALLBACK: dict = {k: "N/A" for k in _EXPECTED_KEYS}
    _FALLBACK["summary"] = content or "Summary unavailable."

    try:
        clean = content.strip()
        # Strip markdown code fences that some models add despite instructions.
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        result: dict = json.loads(clean)
        for key in _EXPECTED_KEYS:
            if key not in result:
                result[key] = "N/A"
        return result
    except json.JSONDecodeError as exc:
        logger.warning(
            "[GROQ] generate_proposal_summary JSON parse failed | error=%s | raw=%.200s",
            exc,
            content,
        )
        return _FALLBACK
