"""HTTP-level tests. Skipped automatically if FastAPI's test client deps aren't installed."""
import hashlib
import hmac
import importlib
import json
import os
import unittest

try:
    from fastapi.testclient import TestClient
except Exception:  # fastapi or httpx missing
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi[testclient] not installed")
class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.update(DB_PATH=":memory:", WHATSAPP_APP_SECRET="s3cret", WHATSAPP_VERIFY_TOKEN="vt")
        import app.main
        cls.main = importlib.reload(app.main)
        cls.client = TestClient(cls.main.app)

    def test_health(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")

    def test_chat_and_unknown_brand(self):
        r = self.client.post("/chat/kirana-express", json={"user_id": "u", "text": "KE10231"})
        self.assertEqual(r.json()["intent"], "order_status")
        self.assertEqual(self.client.post("/chat/nope", json={"user_id": "u", "text": "hi"}).status_code, 404)

    def test_whatsapp_verify(self):
        q = {"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "123"}
        self.assertEqual(self.client.get("/webhooks/whatsapp/kirana-express", params=q).text, "123")

    def test_whatsapp_signature_enforced_and_dedupe(self):
        body = json.dumps({"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "1"},
            "messages": [{"from": "91", "id": "wamid.X", "type": "text", "text": {"body": "hi"}}]}}]}]}).encode()
        url = "/webhooks/whatsapp/kirana-express"
        bad = self.client.post(url, content=body, headers={"X-Hub-Signature-256": "sha256=00"})
        self.assertEqual(bad.status_code, 401)
        sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        h = {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}
        self.assertEqual(self.client.post(url, content=body, headers=h).json()["handled"], 1)
        self.assertEqual(self.client.post(url, content=body, headers=h).json()["handled"], 0)

    def test_voice_greets_then_answers(self):
        first = self.client.post("/webhooks/voice/kirana-express", data={"CallSid": "CA1"})
        self.assertIn("<Gather", first.text)
        turn = self.client.post("/webhooks/voice/kirana-express", data={"CallSid": "CA1", "SpeechResult": "is cash on delivery available"})
        self.assertIn("cash on delivery", turn.text)

    def test_insights(self):
        self.client.post("/chat/kirana-express", json={"user_id": "ins", "text": "agent"})
        self.assertGreater(self.client.get("/brands/kirana-express/insights").json()["conversations"], 0)


if __name__ == "__main__":
    unittest.main()
