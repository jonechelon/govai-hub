"""PromptBuilder — builds messages array for Groq from DigestBuilder context (P18)."""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

DIGEST_SYSTEM_PROMPT = """You are Celo GovAI Hub, an AI agent that monitors the \
Celo blockchain ecosystem and delivers daily intelligence to subscribers.

Generate a concise daily digest in English based on the provided context.

Guidelines:
- Use emojis sparingly — only where they add clarity, not decoration
- Structure: brief 1-sentence intro + sections by category + market snapshot
- Each section: category header + 2-3 bullet points max
- Max 600 tokens total — be concise, cut filler words
- Be factual and neutral — no hype, no price predictions
- Prioritize news with real user impact (launches, upgrades, governance)
- If a category has no relevant news, skip it entirely
- Always end with the Market Snapshot section as provided in the context"""

ASK_SYSTEM_PROMPT = """You are Celo GovAI Hub, an AI assistant specialized in the \
Celo blockchain ecosystem.

Answer questions about Celo, its apps, DeFi protocols, stablecoins (cUSD, cEUR, \
cREAL), governance, and ecosystem news.

Guidelines:
- Answer concisely — max 200 tokens
- Be factual; if unsure, say so explicitly
- Use the digest context provided (if any) as your primary source
- For price/market questions, use only the data from the context — never invent numbers
- Respond in the same language the user writes in"""


class PromptBuilder:
    """Stateless builder for Groq chat messages — digest and ask flows."""

    def build_digest_prompt(self, context: str) -> list[dict]:
        """Build the messages array for Groq digest generation.

        Args:
            context: structured text from DigestBuilder.build_context()

        Returns:
            messages list ready for GroqClient.generate()
        """
        return [
            {"role": "system", "content": DIGEST_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]

    def build_ask_prompt(
        self,
        question: str,
        digest_context: str | None = None,
    ) -> list[dict]:
        """Build the messages array for Groq ask/Q&A interaction.

        Args:
            question: user's question from /ask command
            digest_context: optional last digest text used as knowledge context

        Returns:
            messages list ready for GroqClient.generate()
        """
        if digest_context:
            system = (
                ASK_SYSTEM_PROMPT
                + "\n\nContext from the latest digest:\n"
                + digest_context[:1500]
            )
        else:
            system = ASK_SYSTEM_PROMPT

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]

    def build_ask_prompt_with_history(
        self,
        question: str,
        history: list[dict],
        digest_context: str | None = None,
    ) -> list[dict]:
        """Build messages array for conversational ask mode (P27).

        Args:
            question: current user question
            history: last N exchanges as [{"role": ..., "content": ...}, ...]
            digest_context: optional last digest text

        Returns:
            messages list with system + history + current question
        """
        if digest_context:
            system = (
                ASK_SYSTEM_PROMPT
                + "\n\nContext from the latest digest:\n"
                + digest_context[:1500]
            )
        else:
            system = ASK_SYSTEM_PROMPT

        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": question})
        return messages


# Module-level singleton — imported by DigestGenerator (P19) and ask_handler (P26)
prompt_builder = PromptBuilder()


def build_autotrade_prompt(user_text: str, proposal_title: str = "") -> str:
    """Builds a Groq prompt that extracts a single DeFi intent from user text.

    Returns a string prompt to be passed to the Groq chat completion API.

    Args:
        user_text: Free-form user message describing a trade or stake intent.
        proposal_title: Optional governance proposal title for context.

    Returns:
        Full prompt string for the model.
    """
    context = f'Governance proposal: "{proposal_title}"\n' if proposal_title else ""
    return (
        f"{context}"
        "You are a DeFi intent parser. Extract exactly ONE trade intent from the user message below.\n"
        "Respond ONLY with a valid JSON object — no markdown, no explanation, no extra text.\n\n"
        "Required fields:\n"
        '  "action"      : "swap" | "stake" | "buy" | "sell"\n'
        '  "amount"      : number (0 if not specified)\n'
        '  "token_in"    : token symbol the user spends (e.g. "CELO")\n'
        '  "token_out"   : token symbol the user receives (e.g. "stCELO")\n'
        '  "venue"       : "ubeswap" | "mento" | "stcelo" | "unknown"\n'
        '  "fee_currency": "CELO" (always default to CELO unless user specifies otherwise)\n\n'
        "Example output:\n"
        '{"action":"swap","amount":10,"token_in":"CELO","token_out":"stCELO",'
        '"venue":"ubeswap","fee_currency":"CELO"}\n\n'
        f"User message: {user_text}"
    )


def parse_defi_intent(text: str) -> dict | None:
    """Extracts a DeFi intent JSON from Groq response text.

    Uses regex to isolate the JSON block, then parses defensively.
    Returns None if parsing fails or required fields are missing.

    Args:
        text: Raw model output that may contain JSON.

    Returns:
        Parsed intent dict, or None if invalid.
    """
    required_fields = {"action", "amount", "token_in", "token_out", "venue", "fee_currency"}

    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None

        data = json.loads(match.group())

        if not required_fields.issubset(data.keys()):
            return None

        if not data.get("fee_currency"):
            data["fee_currency"] = "CELO"

        return data

    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


def build_ai_purchase_suggestions_prompt(user_text: str) -> str:
    """Builds a Groq prompt that generates 2–5 distinct DeFi action suggestions.

    Based on free-form user input. Used by /aitrade and AI Trade article flow.

    Args:
        user_text: User message to derive suggestions from.

    Returns:
        Full prompt string for the model.
    """
    return (
        "You are a DeFi advisor on the Celo network.\n"
        "Based on the user's message, suggest between 2 and 5 distinct trade or stake "
        "actions (pick the number that best fits the content — not always three).\n"
        "Each suggestion must use a different angle (staking vs swap vs stables vs "
        "liquidity); do not repeat the same wording or structure.\n"
        "Every label must start with one emoji (choose a fitting one per line), "
        "then a short actionable phrase — max 64 characters total per label.\n"
        "Respond ONLY with a valid JSON object — no markdown, no explanation.\n\n"
        "Format:\n"
        '{"suggestions": [\n'
        '  {"label": "📈 …", "venue": "ubeswap|mento|stcelo",\n'
        '   "token_in": "CELO", "token_out": "stCELO", "pair_hint": "<optional>"},\n'
        "  ... (2 to 5 items)\n"
        "]}\n\n"
        "Available venues: ubeswap (swaps), mento (stable swaps), stcelo (staking).\n"
        "Available tokens: CELO, stCELO, USDm, cEUR, USDC.\n"
        "Prefer CELO as token_in unless the user or article explicitly names another.\n"
        "Ground labels in the user's message when relevant; vary tone and focus across lines.\n\n"
        f"User message: {user_text}"
    )


def parse_ai_suggestions(text: str) -> list[dict]:
    """Extracts the list of DeFi suggestions from a Groq response.

    Returns a list of up to 5 valid suggestion dicts.
    Falls back to empty list on any parse error — never raises.

    Args:
        text: Raw model output that may contain JSON.

    Returns:
        Up to five suggestion dicts with required keys.
    """
    required_suggestion_fields = {"label", "venue", "token_in", "token_out"}

    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return []

        data = json.loads(match.group())
        suggestions = data.get("suggestions", [])

        if not isinstance(suggestions, list):
            return []

        valid: list[dict] = []
        for item in suggestions:
            if not required_suggestion_fields.issubset(item.keys()):
                continue
            item["label"] = item["label"][:64]
            valid.append(item)

        return valid[:5]

    except (json.JSONDecodeError, TypeError, AttributeError):
        return []


def build_digest_title_shorten_prompt(numbered_titles: list[tuple[int, str]]) -> str:
    """Build user prompt for batch shortening of long RSS/Twitter headlines.

    Args:
        numbered_titles: Pairs ``(id, title)`` with ids 1..N matching the batch.

    Returns:
        Full user message for Groq.
    """
    lines = [
        "Shorten each headline below for a compact Telegram digest list.",
        "Rules:",
        "- Respond ONLY with a JSON array of objects.",
        '- Each object: {"id": <integer>, "short_title": "<string>"}',
        "- ids must match the input numbers exactly (1 to N).",
        "- Each short_title: at most 2 lines when shown in Telegram (~160 characters max).",
        "- Keep meaning; do not invent facts. Preserve meaningful emojis if present.",
        "",
        "Headlines:",
    ]
    for tid, title in numbered_titles:
        lines.append(f"{tid}. {title}")
    return "\n".join(lines)


def parse_shortened_titles(raw: str) -> dict[int, str]:
    """Parse Groq JSON array of ``{id, short_title}`` into a dict id → short_title.

    Returns:
        Mapping of batch id to shortened string; empty dict on parse failure.
    """
    result: dict[int, str] = {}
    try:
        match = re.search(r"\[[\s\S]*\]", raw)
        if not match:
            return {}

        data = json.loads(match.group())
        if not isinstance(data, list):
            return {}

        for obj in data:
            if not isinstance(obj, dict):
                continue
            oid = obj.get("id")
            st = obj.get("short_title", "")
            if oid is None or not isinstance(st, str):
                continue
            try:
                key = int(oid)
            except (TypeError, ValueError):
                continue
            result[key] = st.strip()[:240]

        return result

    except (json.JSONDecodeError, TypeError, AttributeError):
        return []
