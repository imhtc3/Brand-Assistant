"""RCS Business Messaging (Google RBM) adapter.

RBM delivers user events as Pub/Sub push messages: the JSON body contains
`message.data`, a base64-encoded event with `senderPhoneNumber`, `messageId`
and either `text` or a `suggestionResponse` (a tapped chip). Replies are
`contentMessage` objects; suggestion chip text is limited to 25 characters.

Sending requires a Google service account, so `build_outbound` produces the
request body and delivery is left to the RBM client library.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from ..engine import Reply


@dataclass
class Inbound:
    msg_id: str
    sender: str
    text: str


def parse_push(body: dict) -> Inbound | None:
    data = body.get("message", {}).get("data")
    if not data:
        return None
    event = json.loads(base64.b64decode(data))
    if "messageId" not in event:  # delivery receipts, typing indicators, etc.
        return None
    if "suggestionResponse" in event:
        sr = event["suggestionResponse"]
        postback = sr.get("postbackData", "")
        text = postback if postback.startswith("faq:") else sr.get("text", "")
    else:
        text = event.get("text", "")
    return Inbound(event["messageId"], event.get("senderPhoneNumber", ""), text)


def build_outbound(reply: Reply) -> dict:
    msg: dict = {"text": reply.text}
    if reply.buttons:
        msg["suggestions"] = [{"reply": {"text": b.title[:25], "postbackData": b.id}} for b in reply.buttons[:11]]
    return {"contentMessage": msg}
