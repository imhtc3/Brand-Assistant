"""WhatsApp Business Cloud API adapter.

Handles the three things every WhatsApp integration needs:
  * webhook verification (GET with hub.challenge),
  * request authentication (X-Hub-Signature-256 = HMAC-SHA256 of the raw body
    keyed with the app secret),
  * parsing inbound messages / building outbound payloads, including reply
    buttons (max 3, titles max 20 chars).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

import requests

from ..engine import Reply

log = logging.getLogger(__name__)
GRAPH_URL = "https://graph.facebook.com/v21.0/{phone_number_id}/messages"


@dataclass
class Inbound:
    msg_id: str
    sender: str
    text: str
    phone_number_id: str


def verify_subscription(params: dict, verify_token: str) -> str | None:
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == verify_token:
        return params.get("hub.challenge")
    return None


def verify_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_webhook(payload: dict) -> list[Inbound]:
    """Extract user messages; delivery/read status callbacks are ignored."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            pnid = value.get("metadata", {}).get("phone_number_id", "")
            for msg in value.get("messages", []):
                kind = msg.get("type")
                if kind == "text":
                    text = msg["text"].get("body", "")
                elif kind == "interactive":
                    inter = msg["interactive"]
                    choice = inter.get("button_reply") or inter.get("list_reply") or {}
                    # Our own "Did you mean" buttons carry the FAQ id; menu buttons carry their title.
                    text = choice.get("id", "") if choice.get("id", "").startswith("faq:") else choice.get("title", "")
                elif kind == "button":  # quick-reply on a template message
                    text = msg["button"].get("text", "")
                else:  # image, audio, location, sticker...
                    text = ""
                out.append(Inbound(msg["id"], msg["from"], text, pnid))
    return out


def build_outbound(to: str, reply: Reply) -> dict:
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to}
    if reply.buttons:
        return base | {
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": reply.text},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": b.id, "title": b.title[:20]}} for b in reply.buttons[:3]
                ]},
            },
        }
    return base | {"type": "text", "text": {"body": reply.text, "preview_url": False}}


def send(payload: dict, phone_number_id: str, token: str | None) -> bool:
    """POST to the Graph API. Without a token we log instead (dry-run for local dev)."""
    if not token:
        log.info("[dry-run] WhatsApp send to %s: %s", payload.get("to"), payload.get("type"))
        return False
    resp = requests.post(GRAPH_URL.format(phone_number_id=phone_number_id), json=payload,
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
    if resp.status_code >= 400:
        log.error("WhatsApp send failed %s: %s", resp.status_code, resp.text[:300])
        return False
    return True
