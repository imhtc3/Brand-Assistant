"""Voice channel via Twilio Programmable Voice.

Each turn: Twilio posts the caller's transcribed speech (SpeechResult), we
answer with TwiML that speaks the reply and listens again (<Gather>). A
handoff dials the brand's support number. Requests are authenticated with
Twilio's X-Twilio-Signature (HMAC-SHA1 over URL + sorted form params).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from xml.sax.saxutils import escape

from ..engine import Reply


def verify_signature(url: str, params: dict, header: str | None, auth_token: str) -> bool:
    if not header:
        return False
    data = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), header)


def parse_form(form: dict) -> tuple[str, str, str]:
    """Returns (session id, caller, transcribed text). CallSid scopes one call."""
    return form.get("CallSid", ""), form.get("From", ""), form.get("SpeechResult", "")


def build_twiml(reply: Reply, action_url: str, language: str = "en-IN") -> str:
    say = f'<Say language="{language}">{escape(reply.text)}</Say>'
    if reply.handoff and reply.transfer_number:
        return f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response>{say}<Dial>{escape(reply.transfer_number)}</Dial></Response>"
    options = ""
    if reply.buttons:  # read the options aloud instead of showing buttons
        options = f'<Say language="{language}">You can say: {escape(", ".join(b.title for b in reply.buttons))}.</Say>'
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response>"
            f'<Gather input="speech" action="{escape(action_url)}" method="POST" speechTimeout="auto" language="{language}">'
            f"{say}{options}</Gather>"
            f'<Say language="{language}">Sorry, I didn\'t hear anything. Goodbye.</Say></Response>')
