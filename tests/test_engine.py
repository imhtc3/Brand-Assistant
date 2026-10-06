import unittest

from app import engine
from app.config import load_brands
from app.engine import Assistant
from app.guardrails import fit_channel, redact
from app.llm import LLMClient
from app.store import Store

BRAND = "kirana-express"


class FakeLLM(LLMClient):
    def __init__(self, answer=None):
        super().__init__()
        self.answer, self.calls = answer, 0

    @property
    def enabled(self):
        return self.answer is not None

    def grounded_answer(self, persona, context, message):
        self.calls += 1
        return self.answer


def make(llm=None):
    return Assistant(load_brands(), Store(), llm=llm or FakeLLM())


class EngineTests(unittest.TestCase):
    def test_greeting_shows_menu_buttons(self):
        r = make().handle(BRAND, "u", "whatsapp", "hi")
        self.assertEqual(r.intent, "greeting")
        self.assertEqual(len(r.buttons), 3)

    def test_order_status_with_id(self):
        r = make().handle(BRAND, "u", "web", "where is KE10231")
        self.assertEqual(r.intent, "order_status")
        self.assertIn("out for delivery", r.text)

    def test_unknown_order_id(self):
        r = make().handle(BRAND, "u", "web", "KE99999")
        self.assertIn("couldn't find", r.text)

    def test_order_slot_filling_across_turns(self):
        a = make()
        self.assertEqual(a.handle(BRAND, "u", "web", "where is my order").intent, "ask_order_id")
        r = a.handle(BRAND, "u", "web", "it's ke10232")
        self.assertEqual(r.intent, "order_status")
        self.assertIn("packed", r.text)

    def test_slot_released_when_topic_changes(self):
        a = make()
        a.handle(BRAND, "u", "web", "track my order")
        r = a.handle(BRAND, "u", "web", "is cash on delivery available")
        self.assertEqual(r.intent, "faq:payment_methods")

    def test_faq_answer(self):
        r = make().handle(BRAND, "u", "web", "is cod available")
        self.assertEqual(r.intent, "faq:payment_methods")
        self.assertGreaterEqual(r.confidence, engine.ANSWER_THRESHOLD)

    def test_typo_and_hinglish(self):
        r = make().handle(BRAND, "u", "web", "refnd kab milega")
        self.assertEqual(r.intent, "faq:refund_policy")

    def test_out_of_scope_is_not_answered(self):
        r = make().handle(BRAND, "u", "web", "what is the capital of france")
        self.assertFalse(r.intent.startswith("faq:"))

    def test_clarify_button_resolves_directly(self):
        r = make().handle(BRAND, "u", "whatsapp", "faq:refund_policy")
        self.assertEqual((r.intent, r.confidence), ("faq:refund_policy", 1.0))

    def test_explicit_handoff_and_hold(self):
        a = make()
        self.assertTrue(a.handle(BRAND, "u", "web", "talk to an agent").handoff)
        held = a.handle(BRAND, "u", "web", "hello??")
        self.assertEqual(held.intent, "handoff_waiting")
        self.assertEqual(a.handle(BRAND, "u", "web", "menu").intent, "greeting")

    def test_voice_handoff_wording_and_transfer(self):
        r = make().handle(BRAND, "CA1", "voice", "talk to an agent")
        self.assertIn("stay on the line", r.text)
        self.assertEqual(r.transfer_number, "+910000000000")

    def test_frustration_triggers_handoff(self):
        self.assertTrue(make().handle(BRAND, "u", "web", "this bot is useless").handoff)

    def test_repeated_fallbacks_escalate(self):
        a = make()
        self.assertFalse(a.handle(BRAND, "u", "web", "zxqv blorp").handoff)
        self.assertTrue(a.handle(BRAND, "u", "web", "flibber jabber").handoff)

    def test_duplicate_webhook_is_ignored(self):
        a = make()
        self.assertIsNotNone(a.handle(BRAND, "u", "whatsapp", "hi", msg_id="wamid.1"))
        self.assertIsNone(a.handle(BRAND, "u", "whatsapp", "hi", msg_id="wamid.1"))

    def test_users_are_isolated(self):
        a = make()
        a.handle(BRAND, "alice", "web", "agent")
        self.assertEqual(a.handle(BRAND, "bob", "web", "hi").intent, "greeting")

    def test_brands_are_isolated(self):
        r = make().handle("nimbus-telecom", "u", "web", "is cod available")
        self.assertFalse(r.intent.startswith("faq:payment"))

    def test_llm_rephrase_used_when_available(self):
        r = make(FakeLLM("We take UPI, cards and COD!")).handle(BRAND, "u", "web", "is cod available")
        self.assertEqual(r.text, "We take UPI, cards and COD!")

    def test_llm_failure_falls_back_to_faq(self):
        llm = FakeLLM(None)
        r = make(llm).handle(BRAND, "u", "web", "is cod available")
        self.assertIn("cash on delivery", r.text)

    def test_unknown_brand(self):
        with self.assertRaises(KeyError):
            make().handle("nope", "u", "web", "hi")


class GuardrailTests(unittest.TestCase):
    def test_redaction(self):
        out = redact("call me on +91 98765 43210 or mail a.b@x.com, card 4111 1111 1111 1111")
        self.assertNotIn("98765", out)
        self.assertNotIn("a.b@x.com", out)
        self.assertNotIn("4111", out)

    def test_order_ids_survive_redaction(self):
        self.assertIn("KE10231", redact("order KE10231"))

    def test_voice_is_short_and_plain(self):
        out = fit_channel("*Bold* one. Two. Three. Four.", "voice")
        self.assertEqual(out, "Bold one. Two.")


class InsightsTests(unittest.TestCase):
    def test_insights_and_pii_free_log(self):
        a = make()
        a.handle(BRAND, "u1", "whatsapp", "is cod available")
        a.handle(BRAND, "u2", "voice", "qwerty zzz my number is 9876543210")
        a.handle(BRAND, "u3", "web", "agent")
        ins = a.store.insights(BRAND)
        self.assertEqual(ins["conversations"], 3)
        self.assertAlmostEqual(ins["handoff_rate"], 0.333, places=3)
        self.assertEqual(ins["top_unanswered"][0][0], "qwerty zzz my number is [phone]")
        stored = [r[0] for r in a.store._conn.execute("SELECT text FROM messages")]
        self.assertFalse(any("9876543210" in t for t in stored))


if __name__ == "__main__":
    unittest.main()
