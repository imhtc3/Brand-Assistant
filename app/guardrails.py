"""Safety and channel-formatting rules applied to every message."""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s-]{8,}\d)(?!\w)")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")

# Per-channel limits: WhatsApp text bodies max out at 4096 chars, interactive
# bodies at 1024; RCS suggestion chips at 25 chars; voice answers must be short
# because callers can't skim audio.
LIMITS = {"whatsapp": 1024, "rcs": 2000, "voice": 300, "web": 2000}


def redact(text: str) -> str:
    """Mask contact and card numbers before anything is written to logs/DB."""
    text = _CARD.sub("[CARD]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    return _PHONE.sub("[PHONE]", text)


def strip_markdown(text: str) -> str:
    return re.sub(r"[*_~`]", "", text)


def fit_channel(text: str, channel: str) -> str:
    limit = LIMITS.get(channel, 2000)
    if channel == "voice":
        text = strip_markdown(text)
        # Keep the first two sentences for speech.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        text = " ".join(sentences[:2])
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text
