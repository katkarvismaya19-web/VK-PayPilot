import hashlib
import hmac
import json

from app.api.razorpay import record_event
from app.config import get_settings
from app.integrations.razorpay_client import RazorpayClient
from app.models import AgentAction, Outcome


def test_webhook_signature_verification(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "razorpay_webhook_secret", "whsec_test")
    body = b'{"event":"payment.captured"}'
    good = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
    rc = RazorpayClient()
    assert rc.verify_webhook(body, good)
    assert not rc.verify_webhook(body, "tampered")


def test_sandbox_payment_link():
    link = RazorpayClient().create_payment_link(1499.0, {"name": "Test"}, "desc", "ref1")
    assert link["amount"] == 149900 and link["mode"] == "sandbox"


def test_payment_attributed_to_action(client, db):
    client.post("/api/agent/run", json={"max_actions": 5})
    action = db.query(AgentAction).first()
    event = {"payment": {"entity": {"id": "pay_attrib_test", "amount": 250000, "method": "upi", "created_at": 1790000000,
                                    "notes": {"paypilot_action_id": str(action.id), "customer_id": action.customer_id}}}}
    r = client.post("/api/razorpay/webhook", content=json.dumps({"event": "payment_link.paid", "payload": event}))
    assert r.json()["attributed_action"] == action.id
    assert db.query(Outcome).filter(Outcome.action_id == action.id, Outcome.converted).count() == 1
    # idempotent on retry
    assert record_event(db, "payment_link.paid", event)["duplicate"]
