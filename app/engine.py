"""The conversation engine: one entry point shared by every channel.

Channel adapters (WhatsApp, RCS, voice, web) turn platform payloads into a
plain `handle(brand, user_id, channel, text)` call and turn the `Reply` back
into the platform's format. All business logic lives here, so a flow is
built once and works on every channel.

Decision order for each message:
  1. duplicate webhook?            -> ignore
  2. already handed to a human?    -> hold (or return to bot on "menu")
  3. asks for a human / angry?     -> hand off
  4. greeting / thanks             -> small talk + menu
  5. order status (slot filling)   -> call the order tool
  6. FAQ retrieval                 -> answer / clarify / fallback
  7. repeated fallbacks            -> hand off
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .config import Brand
from .guardrails import fit_channel
from .knowledge import FAQRetriever
from .llm import LLMClient
from .store import Store
from .tools import OrderClient, describe_order

# Thresholds on retrieval confidence (cosine similarity). Tuned on eval/dev.jsonl
# only; eval/test.jsonl is held out for reporting. See eval/run_eval.py.
ANSWER_THRESHOLD = 0.40
CLARIFY_THRESHOLD = 0.25

_GREETING = re.compile(r"^(hi+|hello|hey|hii+|namaste|good (morning|afternoon|evening)|start|menu)[!. ]*$", re.I)
_THANKS = re.compile(r"^(thanks|thank you|thx|ok(ay)?|great|cool|done)[!. ]*( thanks| thank you)?[!. ]*$", re.I)
_ORDER_STATUS = re.compile(
    r"\b(track|where('?s| is)|status|check|kab aayega|kahan)\b.*\border\b"
    r"|\border\b.*\b(status|where|track|kab|kahan|update)\b", re.I)
_FRUSTRATION = re.compile(r"\b(useless|worst|pathetic|not helping|stupid bot|fraud|scam)\b", re.I)
_BACK_TO_BOT = re.compile(r"^(menu|restart|bot|start over)$", re.I)


@dataclass
class Button:
    id: str
    title: str


@dataclass
class Reply:
    text: str
    intent: str
    confidence: float | None = None
    buttons: list[Button] = field(default_factory=list)
    handoff: bool = False
    transfer_number: str | None = None


def _short(title: str, n: int = 20) -> str:
    return title if len(title) <= n else title[: n - 1].rsplit(" ", 1)[0] + "…"


class Assistant:
    def __init__(self, brands: dict[str, Brand], store: Store | None = None,
                 orders: OrderClient | None = None, llm: LLMClient | None = None):
        self.brands = brands
        self.store = store or Store()
        self.orders = orders or OrderClient()
        self.llm = llm or LLMClient()
        self.retrievers = {b.id: FAQRetriever(b.faqs, b.name) for b in brands.values()}

    # ------------------------------------------------------------------
    def handle(self, brand_id: str, user_id: str, channel: str, text: str,
               msg_id: str | None = None) -> Reply | None:
        if brand_id not in self.brands:
            raise KeyError(f"Unknown brand '{brand_id}'")
        if self.store.seen_before(msg_id):
            return None  # webhook retry; we already answered

        started = time.perf_counter()
        brand = self.brands[brand_id]
        text = (text or "").strip()
        session = self.store.get_session(brand_id, user_id)
        self.store.log(brand_id, user_id, channel, "in", text or "<empty>")

        reply = self._decide(brand, session, text)
        reply.text = fit_channel(reply.text, channel)
        if reply.handoff:
            reply.transfer_number = brand.handoff.voice_transfer_number
            if channel == "voice":
                reply.text = "I'm transferring you to our support team now. Please stay on the line."

        self.store.save_session(brand_id, user_id, session)
        self.store.log(brand_id, user_id, channel, "out", reply.text, reply.intent, reply.confidence,
                       reply.handoff, (time.perf_counter() - started) * 1000)
        return reply

    # ------------------------------------------------------------------
    def _decide(self, brand: Brand, session: dict, text: str) -> Reply:
        lowered = text.lower()

        if session.get("handoff"):
            if _BACK_TO_BOT.match(text):
                session.clear()
                return self._menu(brand, "Welcome back! What can I help with?")
            return Reply("You're in the queue for our support team. An agent will reply here shortly. "
                         "Type 'menu' to go back to the assistant.", "handoff_waiting", handoff=True)

        if not text:
            return self._menu(brand, "Sorry, I can only read text messages for now. What can I help with?")

        if any(k in lowered for k in brand.handoff.keywords) or _FRUSTRATION.search(text):
            return self._handoff(session, "I'm connecting you to our support team now.")

        if text.startswith("faq:"):  # tapped a "Did you mean" button
            faq = next((f for f in brand.faqs if f.id == text[4:]), None)
            if faq:
                session["low_conf"] = 0
                return Reply(faq.answer, f"faq:{faq.id}", 1.0)

        if _GREETING.match(text):
            return self._menu(brand, brand.greeting)
        if _THANKS.match(text):
            return Reply("Happy to help! Message me anytime.", "thanks")

        order = self._order_flow(brand, session, text)
        if order:
            return order

        return self._faq(brand, session, text)

    def _order_flow(self, brand: Brand, session: dict, text: str) -> Reply | None:
        found = brand.order_id_pattern.search(text)
        if found:
            session.pop("awaiting", None)
            order_id = found.group(0)
            return Reply(describe_order(order_id, self.orders.lookup(brand.id, order_id)), "order_status", 1.0)
        if _ORDER_STATUS.search(text) or text.lower() in ("track my order", "check my order"):
            session["awaiting"] = "order_id"
            example = brand.order_id_pattern.pattern.replace("\\d{5}", "12345")
            return Reply(f"Sure! Please share your order ID. It looks like {example} and is in your "
                         "order confirmation.", "ask_order_id", 1.0)
        if session.get("awaiting") == "order_id":
            session.pop("awaiting")  # user changed topic; don't trap them in the slot
        return None

    def _faq(self, brand: Brand, session: dict, text: str) -> Reply:
        matches = self.retrievers[brand.id].search(text, k=2)
        top = matches[0]
        if top.score >= ANSWER_THRESHOLD:
            session["low_conf"] = 0
            answer = self.llm.grounded_answer(
                brand.persona, f"Q: {top.faq.question}\nA: {top.faq.answer}", text) or top.faq.answer
            return Reply(answer, f"faq:{top.faq.id}", round(top.score, 3))

        session["low_conf"] = session.get("low_conf", 0) + 1
        if session["low_conf"] >= brand.handoff.max_low_confidence_turns:
            return self._handoff(session, "I'm not able to answer that one, so I'm passing you to our support team.")

        if top.score >= CLARIFY_THRESHOLD:
            options = [m for m in matches if m.score >= CLARIFY_THRESHOLD]
            return Reply("I want to get this right. Did you mean one of these?", "clarify",
                         round(top.score, 3),
                         buttons=[Button(f"faq:{m.faq.id}", _short(m.faq.question)) for m in options])
        return Reply("Sorry, I didn't understand that. Could you rephrase it, or pick an option below?",
                     "fallback", round(top.score, 3), buttons=self._menu(brand, "").buttons)

    def _menu(self, brand: Brand, text: str) -> Reply:
        return Reply(text, "greeting", buttons=[Button(f"menu:{i}", _short(t)) for i, t in enumerate(brand.menu)])

    @staticmethod
    def _handoff(session: dict, text: str) -> Reply:
        session.clear()
        session["handoff"] = True
        return Reply(text + " An agent will reply here shortly.", "handoff", handoff=True)
