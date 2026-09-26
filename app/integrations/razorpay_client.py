"""
Razorpay integration.

Live mode (RAZORPAY_KEY_ID + RAZORPAY_KEY_SECRET set): calls the real REST API.
Sandbox mode (no keys): returns realistic mock objects so the whole agent loop
can be demoed end-to-end. Webhook signatures are verified with HMAC-SHA256
exactly as Razorpay documents (X-Razorpay-Signature header).
"""
import hashlib
import hmac
import time
import uuid

import httpx

from app.config import get_settings

API = "https://api.razorpay.com/v1"


class RazorpayClient:
    def __init__(self):
        self.s = get_settings()

    @property
    def live(self) -> bool:
        return self.s.razorpay_live

    def _auth(self):
        return (self.s.razorpay_key_id, self.s.razorpay_key_secret)

    def create_payment_link(self, amount_rupees: float, customer: dict, description: str,
                            reference_id: str, notes: dict | None = None, expire_hours: int = 72) -> dict:
        payload = {
            "amount": int(round(amount_rupees * 100)),  # Razorpay expects paise
            "currency": "INR",
            "accept_partial": False,
            "description": description[:2048],
            "reference_id": reference_id[:40],
            "expire_by": int(time.time()) + expire_hours * 3600,
            "customer": {k: v for k, v in {"name": customer.get("name"), "email": customer.get("email"),
                                           "contact": customer.get("phone")}.items() if v},
            "notify": {"sms": False, "email": False},  # PayPilot sends its own personalised message
            "reminder_enable": True,
            "notes": {k: str(v)[:256] for k, v in (notes or {}).items()},
        }
        if not self.live:
            pid = "plink_" + uuid.uuid4().hex[:14]
            return {"id": pid, "short_url": f"https://rzp.io/i/{pid[-8:]}", "status": "created",
                    "amount": payload["amount"], "reference_id": reference_id, "mode": "sandbox"}
        r = httpx.post(f"{API}/payment_links", json=payload, auth=self._auth(), timeout=30)
        r.raise_for_status()
        data = r.json()
        return {"id": data["id"], "short_url": data["short_url"], "status": data["status"],
                "amount": data["amount"], "reference_id": reference_id, "mode": "live"}

    def fetch_payments(self, from_ts: int, to_ts: int, count: int = 100, skip: int = 0) -> list[dict]:
        if not self.live:
            return []
        r = httpx.get(f"{API}/payments", params={"from": from_ts, "to": to_ts, "count": count, "skip": skip},
                      auth=self._auth(), timeout=30)
        r.raise_for_status()
        return r.json().get("items", [])

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        secret = self.s.razorpay_webhook_secret
        if not secret:
            return not self.live  # sandbox mode accepts unsigned test webhooks
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")


def get_razorpay() -> RazorpayClient:
    return RazorpayClient()
