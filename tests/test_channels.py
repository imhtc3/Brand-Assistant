import base64
import hashlib
import hmac
import json
import unittest

from app.channels import rcs, voice, whatsapp
from app.engine import Button, Reply


def wa_payload(msg):
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "000000000000001"},
        "messages": [msg]}}]}]}


class WhatsAppTests(unittest.TestCase):
    def test_verify_subscription(self):
        ok = {"hub.mode": "subscribe", "hub.verify_token": "t", "hub.challenge": "42"}
        self.assertEqual(whatsapp.verify_subscription(ok, "t"), "42")
        self.assertIsNone(whatsapp.verify_subscription(ok | {"hub.verify_token": "x"}, "t"))

    def test_signature(self):
        body = b'{"a":1}'
        sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(whatsapp.verify_signature(body, sig, "secret"))
        self.assertFalse(whatsapp.verify_signature(body + b" ", sig, "secret"))
        self.assertFalse(whatsapp.verify_signature(body, None, "secret"))

    def test_parse_text_and_buttons(self):
        text = whatsapp.parse_webhook(wa_payload(
            {"from": "9199", "id": "wamid.A", "type": "text", "text": {"body": "hi"}}))
        self.assertEqual((text[0].text, text[0].sender, text[0].msg_id), ("hi", "9199", "wamid.A"))
        faq_btn = whatsapp.parse_webhook(wa_payload({"from": "9199", "id": "wamid.B", "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": "faq:refund_policy", "title": "How do…"}}}))
        self.assertEqual(faq_btn[0].text, "faq:refund_policy")
        menu_btn = whatsapp.parse_webhook(wa_payload({"from": "9199", "id": "wamid.C", "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": "menu:0", "title": "Track my order"}}}))
        self.assertEqual(menu_btn[0].text, "Track my order")

    def test_status_callbacks_ignored(self):
        payload = wa_payload({})
        payload["entry"][0]["changes"][0]["value"] = {"statuses": [{"status": "read"}]}
        self.assertEqual(whatsapp.parse_webhook(payload), [])

    def test_build_outbound(self):
        plain = whatsapp.build_outbound("9199", Reply("hello", "x"))
        self.assertEqual(plain["type"], "text")
        btns = whatsapp.build_outbound("9199", Reply("pick", "x", buttons=[Button(f"b{i}", "A very long button title") for i in range(5)]))
        buttons = btns["interactive"]["action"]["buttons"]
        self.assertEqual(len(buttons), 3)
        self.assertTrue(all(len(b["reply"]["title"]) <= 20 for b in buttons))

    def test_send_dry_run_without_token(self):
        self.assertFalse(whatsapp.send({"to": "1"}, "pnid", None))


class VoiceTests(unittest.TestCase):
    def test_signature(self):
        url, params, token = "https://x.io/webhooks/voice/b", {"CallSid": "CA1", "SpeechResult": "hi"}, "tok"
        data = url + "CallSidCA1SpeechResulthi"
        sig = base64.b64encode(hmac.new(b"tok", data.encode(), hashlib.sha1).digest()).decode()
        self.assertTrue(voice.verify_signature(url, params, sig, token))
        self.assertFalse(voice.verify_signature(url, params | {"From": "x"}, sig, token))

    def test_twiml_gather_and_escaping(self):
        xml = voice.build_twiml(Reply("Rice & dal <now>", "x"), "https://x.io/v")
        self.assertIn("<Gather input=\"speech\"", xml)
        self.assertIn("Rice &amp; dal &lt;now&gt;", xml)

    def test_twiml_handoff_dials(self):
        xml = voice.build_twiml(Reply("Connecting", "handoff", handoff=True, transfer_number="+911"), "u")
        self.assertIn("<Dial>+911</Dial>", xml)


class RCSTests(unittest.TestCase):
    def push(self, event):
        return {"message": {"data": base64.b64encode(json.dumps(event).encode()).decode()}}

    def test_parse_text_and_suggestion(self):
        m = rcs.parse_push(self.push({"senderPhoneNumber": "+9199", "messageId": "m1", "text": "hi"}))
        self.assertEqual((m.sender, m.text), ("+9199", "hi"))
        s = rcs.parse_push(self.push({"senderPhoneNumber": "+9199", "messageId": "m2",
                                      "suggestionResponse": {"postbackData": "faq:refund_policy", "text": "Refunds"}}))
        self.assertEqual(s.text, "faq:refund_policy")

    def test_receipts_ignored(self):
        self.assertIsNone(rcs.parse_push(self.push({"eventType": "READ"})))

    def test_build_outbound_chips(self):
        out = rcs.build_outbound(Reply("pick", "x", buttons=[Button("faq:a", "x" * 40)]))
        chip = out["contentMessage"]["suggestions"][0]["reply"]
        self.assertEqual((len(chip["text"]), chip["postbackData"]), (25, "faq:a"))


if __name__ == "__main__":
    unittest.main()
