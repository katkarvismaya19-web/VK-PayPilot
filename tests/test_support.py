"""Customer portal + owner support inbox."""
import pytest

from app.support.triage import classify, suggest_reply


@pytest.fixture()
def sent(client):
    client.post("/api/demo/reset")
    client.post("/api/agent/run", json={"max_actions": 10})
    acts = client.get("/api/agent/actions?status=proposed").json()[:4]
    for a in acts:
        client.post(f"/api/agent/actions/{a['id']}/execute")
    yield acts
    client.post("/api/demo/reset")


def test_triage_categories_and_priority():
    assert classify("Money debited", "amount deducted but order failed")["priority"] == "urgent"
    assert classify("Stop", "please stop sending me offers")["category"] == "account"
    assert classify("Kurta", "what is the fabric and size fit")["priority"] == "low"
    assert classify("Late", "order not delivered", segment="Champions")["priority"] == "high"


def test_suggested_reply_is_grounded_and_customer_facing():
    r = suggest_reply({"customer_name": "Priya Shah", "subject": "Refund", "category": "refund",
                       "messages": [{"author": "customer", "body": "where is my refund"}]})
    assert r["text"].startswith("Hi Priya") and "[1]" in r["text"]
    assert r["citations"][0]["source"] == "Customer Support Playbook"
    assert "check the" not in r["text"].lower()  # owner instructions never leak into the customer reply


def test_seeded_inbox(client):
    client.post("/api/demo/reset")
    tickets = client.get("/api/support/tickets?status=").json()
    assert len(tickets) >= 5 and tickets[0]["priority"] == "urgent"
    assert len({t["customer_name"] for t in tickets}) == len(tickets)


def test_customer_pays_from_portal(client, sent):
    a = sent[0]
    view = client.get(f"/api/portal/{a['customer_id']}").json()
    assert any(m["id"] == a["id"] for m in view["messages"])
    r = client.post(f"/api/portal/{a['customer_id']}/messages/{a['id']}/respond", json={"response": "paid"}).json()
    assert r["payment"]["attributed_action"] == a["id"]
    assert client.get("/api/agent/impact").json()["conversions"] == 1
    again = client.post(f"/api/portal/{a['customer_id']}/messages/{a['id']}/respond", json={"response": "paid"})
    assert again.status_code == 409


def test_stop_turns_off_marketing(client, sent):
    a = sent[1]
    client.post(f"/api/portal/{a['customer_id']}/messages/{a['id']}/respond", json={"response": "stop"})
    assert client.get(f"/api/portal/{a['customer_id']}").json()["customer"]["marketing_opt_in"] is False


def test_customers_cannot_touch_each_others_data(client, sent):
    a, b = sent[0], sent[1]
    assert client.post(f"/api/portal/{b['customer_id']}/messages/{a['id']}/respond",
                       json={"response": "paid"}).status_code == 404
    t = client.post(f"/api/portal/{a['customer_id']}/tickets", json={"subject": "Hi", "message": "a question"}).json()
    assert client.post(f"/api/portal/{b['customer_id']}/tickets/{t['id']}/messages",
                       json={"body": "x"}).status_code == 404


def test_ticket_lifecycle_with_payment_link(client, sent):
    cid = sent[0]["customer_id"]
    t = client.post(f"/api/portal/{cid}/tickets",
                    json={"subject": "Double charged", "message": "I was charged twice for one order"}).json()
    assert t["priority"] == "urgent" and t["waiting_on"] == "owner"
    detail = client.get(f"/api/support/tickets/{t['id']}").json()
    assert detail["suggested_reply"]["citations"]
    r = client.post(f"/api/support/tickets/{t['id']}/reply",
                    json={"body": "Sorry, here is a fresh link.", "status": "in_progress", "payment_link_amount": 499}).json()
    link = r["messages"][-1]["attachments"]["payment_link"]
    assert link["short_url"] in r["messages"][-1]["body"] and r["waiting_on"] == "customer"
    # Razorpay confirms the payment made through the ticket's link
    import json
    event = {"payment": {"entity": {"id": "pay_ticket_test", "amount": 49900, "method": "upi", "created_at": 1790000000,
                                    "notes": {"paypilot_ticket_id": str(t["id"]), "customer_id": cid}}}}
    client.post("/api/razorpay/webhook", content=json.dumps({"event": "payment_link.paid", "payload": event}))
    thread = client.get(f"/api/support/tickets/{t['id']}").json()["messages"]
    assert "received via Razorpay" in thread[-1]["body"]
    assert client.post(f"/api/support/tickets/{t['id']}/status", json={"status": "resolved"}).json()["status"] == "resolved"
    reopened = client.post(f"/api/portal/{cid}/tickets/{t['id']}/messages", json={"body": "one more thing"}).json()
    assert reopened["status"] == "open"
