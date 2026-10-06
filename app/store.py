"""SQLite storage: conversation log, session state, webhook dedupe and insights.

Messaging platforms retry webhooks, so every inbound message id is recorded and
duplicates are dropped (idempotency). All text is PII-redacted before storage.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import Counter

from .guardrails import redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT NOT NULL, user_id TEXT NOT NULL, channel TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    text TEXT NOT NULL, intent TEXT, confidence REAL, handoff INTEGER DEFAULT 0,
    latency_ms REAL, ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_brand_ts ON messages(brand, ts);
CREATE TABLE IF NOT EXISTS processed (msg_id TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    brand TEXT NOT NULL, user_id TEXT NOT NULL, state TEXT NOT NULL, updated REAL NOT NULL,
    PRIMARY KEY (brand, user_id)
);
"""

SESSION_TTL_SECONDS = 30 * 60  # a conversation "resets" after 30 idle minutes


class Store:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    # --- idempotency -------------------------------------------------
    def seen_before(self, msg_id: str | None) -> bool:
        """Atomically mark a message id as processed; True if it was already there."""
        if not msg_id:
            return False
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO processed(msg_id, ts) VALUES (?, ?)", (msg_id, time.time()))
            self._conn.commit()
            return cur.rowcount == 0

    # --- sessions ----------------------------------------------------
    def get_session(self, brand: str, user_id: str) -> dict:
        row = self._conn.execute(
            "SELECT state, updated FROM sessions WHERE brand=? AND user_id=?", (brand, user_id)).fetchone()
        if not row or time.time() - row["updated"] > SESSION_TTL_SECONDS:
            return {}
        return json.loads(row["state"])

    def save_session(self, brand: str, user_id: str, state: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(brand, user_id, state, updated) VALUES (?,?,?,?) "
                "ON CONFLICT(brand, user_id) DO UPDATE SET state=excluded.state, updated=excluded.updated",
                (brand, user_id, json.dumps(state), time.time()))
            self._conn.commit()

    # --- logging -----------------------------------------------------
    def log(self, brand, user_id, channel, direction, text, intent=None, confidence=None,
            handoff=False, latency_ms=None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(brand,user_id,channel,direction,text,intent,confidence,handoff,latency_ms,ts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (brand, user_id, channel, direction, redact(text), intent, confidence,
                 int(handoff), latency_ms, time.time()))
            self._conn.commit()

    # --- insights (what an account owner reviews with the client) ----
    def insights(self, brand: str) -> dict:
        rows = self._conn.execute(
            "SELECT user_id, channel, intent, confidence, handoff, latency_ms FROM messages "
            "WHERE brand=? AND direction='out'", (brand,)).fetchall()
        unanswered = self._conn.execute(
            # The user message each fallback reply answered (same user, latest earlier inbound).
            "SELECT (SELECT i.text FROM messages i WHERE i.brand=o.brand AND i.user_id=o.user_id "
            "        AND i.direction='in' AND i.id < o.id ORDER BY i.id DESC LIMIT 1) AS text "
            "FROM messages o WHERE o.brand=? AND o.direction='out' AND o.intent='fallback'",
            (brand,)).fetchall()
        if not rows:
            return {"brand": brand, "conversations": 0}
        users = {r["user_id"] for r in rows}
        handed_off = {r["user_id"] for r in rows if r["handoff"]}
        intents = Counter(r["intent"] for r in rows)
        confs = [r["confidence"] for r in rows if r["confidence"] is not None]
        lat = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
        return {
            "brand": brand,
            "conversations": len(users),
            "bot_replies": len(rows),
            "containment_rate": round(1 - len(handed_off) / len(users), 3),
            "handoff_rate": round(len(handed_off) / len(users), 3),
            "fallback_rate": round(intents.get("fallback", 0) / len(rows), 3),
            "avg_faq_confidence": round(sum(confs) / len(confs), 3) if confs else None,
            "p95_latency_ms": round(lat[int(0.95 * (len(lat) - 1))], 1) if lat else None,
            "by_channel": dict(Counter(r["channel"] for r in rows)),
            "top_intents": intents.most_common(8),
            "top_unanswered": Counter(r["text"].lower() for r in unanswered if r["text"]).most_common(10),
        }
