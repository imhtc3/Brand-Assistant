"""Optional LLM layer for grounded, natural-sounding answers.

Works with any OpenAI-compatible chat endpoint (OpenAI, Groq, Gemini's
OpenAI-compatible API, a local Ollama server). If LLM_API_KEY is not set, or
the call fails or times out, the assistant falls back to the approved FAQ
answer, so a provider outage never takes the bot down.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

NOT_FOUND = "NOT_FOUND"

PROMPT = """{persona}

Answer the customer's message using ONLY the facts in CONTEXT.
- Reply in 1-3 short sentences, in the same language the customer used.
- Do not add prices, timelines or policies that are not in CONTEXT.
- If CONTEXT does not answer the message, reply with exactly {not_found}.

CONTEXT:
{context}"""


class LLMClient:
    def __init__(self):
        self.api_key = os.getenv("LLM_API_KEY")
        self.base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.timeout = float(os.getenv("LLM_TIMEOUT", "8"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def grounded_answer(self, persona: str, context: str, message: str) -> str | None:
        """Return a rephrased answer, or None to signal 'use the FAQ answer as is'."""
        if not self.enabled:
            return None
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.2,
                    "max_tokens": 200,
                    "messages": [
                        {"role": "system", "content": PROMPT.format(
                            persona=persona, context=context, not_found=NOT_FOUND)},
                        {"role": "user", "content": message},
                    ],
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # network errors, bad JSON, rate limits
            log.warning("LLM call failed, using FAQ answer: %s", exc)
            return None
        if not text or NOT_FOUND in text:
            return None
        return text
