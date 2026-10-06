"""Loads per-brand configuration from YAML.

Each brand is one file in brands/. Adding a new client means adding a YAML file,
not changing code: the persona, FAQ knowledge base, menu, order-id format and
handoff rules all live there.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BRANDS_DIR = Path(os.getenv("BRANDS_DIR", ROOT / "brands"))


@dataclass
class FAQ:
    id: str
    question: str
    answer: str
    examples: list[str] = field(default_factory=list)


@dataclass
class HandoffRules:
    keywords: list[str]
    voice_transfer_number: str | None = None
    max_low_confidence_turns: int = 2


@dataclass
class Brand:
    id: str
    name: str
    persona: str
    greeting: str
    menu: list[str]
    order_id_pattern: re.Pattern
    handoff: HandoffRules
    faqs: list[FAQ]
    channels: dict


class ConfigError(ValueError):
    pass


def _load_one(path: Path) -> Brand:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("id", "name", "persona", "greeting", "faqs"):
        if key not in raw:
            raise ConfigError(f"{path.name}: missing required key '{key}'")
    faqs = [FAQ(**f) for f in raw["faqs"]]
    ids = [f.id for f in faqs]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"{path.name}: duplicate FAQ ids")
    h = raw.get("handoff", {})
    return Brand(
        id=raw["id"],
        name=raw["name"],
        persona=raw["persona"].strip(),
        greeting=raw["greeting"],
        menu=raw.get("menu", [])[:3],  # WhatsApp allows at most 3 reply buttons
        order_id_pattern=re.compile(raw.get("order_id_pattern", r"[A-Z]{2}\d{5}"), re.I),
        handoff=HandoffRules(
            keywords=[k.lower() for k in h.get("keywords", ["agent", "human"])],
            voice_transfer_number=h.get("voice_transfer_number"),
            max_low_confidence_turns=int(h.get("max_low_confidence_turns", 2)),
        ),
        faqs=faqs,
        channels=raw.get("channels", {}),
    )


def load_brands(directory: Path = BRANDS_DIR) -> dict[str, Brand]:
    brands = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        brand = _load_one(path)
        brands[brand.id] = brand
    if not brands:
        raise ConfigError(f"No brand configs found in {directory}")
    return brands
