"""PromptBuilder — builds messages array for Groq from DigestBuilder context (P18)."""

from __future__ import annotations

import logging

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
