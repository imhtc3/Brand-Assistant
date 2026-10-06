"""HTTP layer: thin FastAPI routes around the engine and channel adapters.

Run locally:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
import os

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .channels import rcs, voice, whatsapp
from .config import load_brands
from .engine import Assistant, Reply
from .store import Store

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("brand-assistant")

WA_TOKEN = os.getenv("WHATSAPP_TOKEN")
WA_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")
WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "dev-verify-token")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

assistant = Assistant(load_brands(), Store(os.getenv("DB_PATH", "assistant.db")))
app = FastAPI(title="Brand Assistant", version="1.0.0",
              description="Multichannel AI assistant for brands on WhatsApp, RCS and voice.")


def _brand_or_404(brand_id: str):
    if brand_id not in assistant.brands:
        raise HTTPException(404, f"Unknown brand '{brand_id}'")
    return assistant.brands[brand_id]


def _reply_json(reply: Reply | None) -> dict:
    if reply is None:
        return {"duplicate": True}
    return {"text": reply.text, "intent": reply.intent, "confidence": reply.confidence,
            "handoff": reply.handoff, "buttons": [b.__dict__ for b in reply.buttons]}


@app.get("/health")
def health():
    return {"status": "ok", "brands": sorted(assistant.brands), "llm_enabled": assistant.llm.enabled}


# --- Web/test channel ---------------------------------------------------
class ChatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(max_length=2000)


@app.post("/chat/{brand_id}")
def chat(brand_id: str, body: ChatIn):
    _brand_or_404(brand_id)
    return _reply_json(assistant.handle(brand_id, body.user_id, "web", body.text))


# --- WhatsApp -----------------------------------------------------------
@app.get("/webhooks/whatsapp/{brand_id}")
def whatsapp_verify(brand_id: str, request: Request):
    _brand_or_404(brand_id)
    challenge = whatsapp.verify_subscription(dict(request.query_params), WA_VERIFY_TOKEN)
    if challenge is None:
        raise HTTPException(403, "Verification failed")
    return Response(challenge, media_type="text/plain")


@app.post("/webhooks/whatsapp/{brand_id}")
async def whatsapp_inbound(brand_id: str, request: Request, background: BackgroundTasks):
    brand = _brand_or_404(brand_id)
    raw = await request.body()
    if WA_APP_SECRET and not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256"), WA_APP_SECRET):
        raise HTTPException(401, "Bad signature")
    pnid = brand.channels.get("whatsapp", {}).get("phone_number_id", "")
    handled = 0
    for msg in whatsapp.parse_webhook(await request.json()):
        reply = assistant.handle(brand_id, msg.sender, "whatsapp", msg.text, msg_id=msg.msg_id)
        if reply:
            handled += 1
            # Acknowledge Meta fast (it retries slow webhooks); send the reply afterwards.
            background.add_task(whatsapp.send, whatsapp.build_outbound(msg.sender, reply),
                                msg.phone_number_id or pnid, WA_TOKEN)
    return {"handled": handled}


# --- RCS ----------------------------------------------------------------
@app.post("/webhooks/rcs/{brand_id}")
async def rcs_inbound(brand_id: str, request: Request):
    _brand_or_404(brand_id)
    msg = rcs.parse_push(await request.json())
    if msg is None:
        return {"handled": 0}
    reply = assistant.handle(brand_id, msg.sender, "rcs", msg.text, msg_id=msg.msg_id)
    return {"handled": int(reply is not None), "outbound": rcs.build_outbound(reply) if reply else None}


# --- Voice --------------------------------------------------------------
@app.post("/webhooks/voice/{brand_id}")
async def voice_inbound(brand_id: str, request: Request):
    brand = _brand_or_404(brand_id)
    form = {k: str(v) for k, v in (await request.form()).items()}
    if TWILIO_AUTH_TOKEN and not voice.verify_signature(
            str(request.url), form, request.headers.get("X-Twilio-Signature"), TWILIO_AUTH_TOKEN):
        raise HTTPException(401, "Bad signature")
    call_sid, _caller, speech = voice.parse_form(form)
    if not speech:  # call just connected: greet and start listening
        reply = Reply(f"{brand.greeting} How can I help?", "greeting")
    else:
        reply = assistant.handle(brand_id, call_sid, "voice", speech)
    return Response(voice.build_twiml(reply, str(request.url)), media_type="application/xml")


# --- Account insights ----------------------------------------------------
@app.get("/brands/{brand_id}/insights")
def insights(brand_id: str):
    _brand_or_404(brand_id)
    return assistant.store.insights(brand_id)
