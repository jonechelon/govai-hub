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


async def generate_proposal_summary(text: str) -> dict[str, str]:
    """Extract proposal metadata and generate an ELI5 summary.

    Args:
        text: Cleaned proposal description text extracted from the source URL.

    Returns:
        Dict with keys: title, date_created, author, status, discussions_to,
        date_executed, summary.
    """
    metadata_system_prompt = (
        "You are a Celo governance proposal data extractor and summarizer.\n"
        "Read the provided proposal text (it may contain CGP-style metadata headers).\n"
        "Extract the following fields and return ONLY a valid JSON object with exactly "
        "these keys (all values must be strings): "
        "title, date_created, author, status, discussions_to, date_executed, summary.\n"
        "If a specific metadata field is not found in the text, return 'N/A' for that field. "
        "You may set 'summary' to 'N/A'. We will regenerate a structured summary in a second step.\n"
        "Do not wrap the JSON in markdown fences. Do not include any other text."
    )
    metadata_user_prompt = f"Proposal text:\n\n{text}"

    messages = [
        {"role": "system", "content": metadata_system_prompt},
        {"role": "user", "content": metadata_user_prompt},
    ]

    content, _usage = await groq_client._call(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=420,
    )

    fallback = {
        "title": "N/A",
        "date_created": "N/A",
        "author": "N/A",
        "status": "N/A",
        "discussions_to": "N/A",
        "date_executed": "N/A",
        "summary": "N/A",
    }

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("[GROQ] Proposal summary JSONDecodeError — returning fallback dict")
        return fallback

    if not isinstance(parsed, dict):
        logger.warning("[GROQ] Proposal summary JSON is not an object — returning fallback dict")
        return fallback

    result: dict[str, str] = {}
    for key in fallback.keys():
        value = parsed.get(key, "N/A")
        result[key] = (
            value
            if isinstance(value, str) and value
            else str(value)
            if value
            else "N/A"
        )

    # Second step — regenerate summary with strict metadata + summary HTML structure
    system_prompt = (
        "You MUST extract the proposal metadata and provide a summary in EXACTLY this HTML format. "
        "Do not use markdown links, output raw URLs for links.\n\n"
        "<b>Title:</b> [Extract Title]\n"
        "<b>Author:</b> [Extract Author]\n"
        "<b>Status:</b> [Extract Status]\n"
        "<b>Date Created:</b> [Extract Date Created]\n"
        "<b>Discussions-To:</b> [Extract Raw URL - NO HTML LINK TAGS]\n"
        "<b>Date Executed:</b> [Extract Date Executed or N/A]\n\n"
        "💡 <b>AI Summary</b>\n\n"
        "👶 <b>ELI5:</b> [1-2 simple sentences]\n"
        "⚙️ <b>Details:</b> [Technical/financial summary]\n"
        "🌍 <b>Impact:</b> [Why it matters]\n\n"
        "Output only this HTML structure, with no markdown code fences and no additional sections."
    )

    summary_user_prompt = f"Proposal text:\n\n{text}"
    summary_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": summary_user_prompt},
    ]

    try:
        summary_text, _summary_usage = await groq_client._call(
            model="llama-3.3-70b-versatile",
            messages=summary_messages,
            max_tokens=360,
        )
        result["summary"] = summary_text.strip() if summary_text else "N/A"
    except Exception as exc:
        logger.warning(
            "[GROQ] Structured summary regeneration failed — using fallback summary | error=%s",
            exc,
        )

    return result
