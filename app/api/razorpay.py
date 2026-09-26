"""
Razorpay webhooks -> transactions + attribution.

Configure in Razorpay Dashboard > Settings > Webhooks:
  URL:    https://<your-host>/api/razorpay/webhook
  Events: payment.captured, payment.failed, payment_link.paid
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.integrations.razorpay_client import get_razorpay
from app.models import AgentAction, Cart, Customer, Outcome, Payment

router = APIRouter(prefix="/api/razorpay", tags=["razorpay"])


def _customer_id(db: Session, entity: dict, notes: dict) -> str:
    if notes.get("customer_id"):
        return notes["customer_id"]
    c = None
    if entity.get("email"):
        c = db.query(Customer).filter(Customer.email == entity["email"]).first()
    if not c and entity.get("contact"):
        c = db.query(Customer).filter(Customer.phone == entity["contact"]).first()
    return c.customer_id if c else "unknown"


def record_event(db: Session, event: str, payload: dict) -> dict:
    pay = payload.get("payment", {}).get("entity", {})
    link = payload.get("payment_link", {}).get("entity", {})
    notes = {**(pay.get("notes") or {}), **(link.get("notes") or {})}
    if not pay.get("id"):
        return {"ignored": True, "reason": "no payment entity"}

    status = "failed" if event == "payment.failed" else "captured"
    existing = db.query(Payment).filter(Payment.razorpay_payment_id == pay["id"]).first()
    if existing:
        return {"duplicate": True, "payment_id": pay["id"]}  # webhooks can be retried; stay idempotent

    cid = _customer_id(db, pay, notes)
    ts = datetime.fromtimestamp(pay.get("created_at", datetime.now(timezone.utc).timestamp()), timezone.utc).replace(tzinfo=None)
    db.add(Payment(razorpay_payment_id=pay["id"], razorpay_order_id=pay.get("order_id") or "", customer_id=cid,
                   amount=pay.get("amount", 0) / 100, status=status, method=pay.get("method", ""),
                   error_reason=pay.get("error_reason") or "", created_at=ts))

    attributed = None
    if status == "captured" and notes.get("paypilot_action_id"):
        action = db.get(AgentAction, int(notes["paypilot_action_id"]))
        if action:
            db.add(Outcome(action_id=action.id, converted=True, revenue=pay.get("amount", 0) / 100))
            attributed = action.id
        if notes.get("cart_id"):
            cart = db.query(Cart).filter(Cart.cart_id == notes["cart_id"]).first()
            if cart:
                cart.status = "recovered"
    if status == "captured" and notes.get("paypilot_ticket_id"):
        from app.models import SupportTicket, TicketMessage
        ticket = db.get(SupportTicket, int(notes["paypilot_ticket_id"]))
        if ticket:
            ticket.messages.append(TicketMessage(
                author="system", body=f"Payment of ₹{pay.get('amount', 0) / 100:,.0f} received via Razorpay ({pay['id']})."))
            ticket.updated_at = datetime.utcnow()
    db.commit()
    return {"recorded": pay["id"], "status": status, "customer_id": cid, "attributed_action": attributed}


@router.post("/webhook")
async def webhook(request: Request, x_razorpay_signature: str = Header(default=""), db: Session = Depends(get_db)):
    body = await request.body()
    if not get_razorpay().verify_webhook(body, x_razorpay_signature):
        raise HTTPException(400, "Invalid webhook signature")
    data = json.loads(body)
    return record_event(db, data.get("event", ""), data.get("payload", {}))
