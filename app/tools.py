"""Backend integrations the assistant can call.

In a real deployment, order lookup would call the brand's order API. Here it
reads a mock JSON file so the project runs anywhere; swap `OrderClient.lookup`
for an HTTP call to integrate a real system.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .config import ROOT

ORDERS_FILE = Path(os.getenv("ORDERS_FILE", ROOT / "data" / "orders.json"))

STATUS_TEXT = {
    "placed": "has been placed and is waiting to be packed",
    "packed": "is packed and waiting for a rider",
    "out_for_delivery": "is out for delivery",
    "delivered": "was delivered",
    "cancelled": "was cancelled",
}


class OrderClient:
    def __init__(self, path: Path = ORDERS_FILE):
        self._orders = json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}

    def lookup(self, brand_id: str, order_id: str) -> dict | None:
        return self._orders.get(brand_id, {}).get(order_id.upper())


def describe_order(order_id: str, order: dict | None) -> str:
    if order is None:
        return f"I couldn't find an order with ID {order_id.upper()}. Please check the ID in your order confirmation."
    status = STATUS_TEXT.get(order["status"], order["status"])
    text = f"Order {order_id.upper()} {status}."
    if order.get("eta_minutes") and order["status"] in ("packed", "out_for_delivery"):
        text += f" Expected in about {order['eta_minutes']} minutes."
    if order.get("items"):
        text += f" Items: {', '.join(order['items'])}."
    return text
