"""Ticket lifecycle, customer responses to PayPilot messages, and serialisation shared by the portal and owner APIs."""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AgentAction, Customer, CustomerResponse, Outcome, Product, Sale, SupportTicket, TicketMessage
from app.support.triage import CATEGORY_LABELS, classify, is_opt_out

PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
RESPONSES = ("paid", "not_interested", "stop", "question")
# how each PayPilot message is labelled for the customer (the agent's own titles are written for the owner)
MESSAGE_LABELS = {"payment_retry": "Complete your payment", "cart_recovery": "Your cart is waiting",
                  "winback_offer": "An offer for you", "second_order": "For your next order",
                  "vip_reward": "A thank-you from us", "cross_sell": "Picked for you"}


class SupportError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ serialisation

def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def serialize_ticket(t: SupportTicket, db: Session, with_messages: bool = True) -> dict:
    msgs = [{"id": m.id, "author": m.author, "body": m.body, "attachments": m.attachments or {},
             "created_at": _iso(m.created_at)} for m in t.messages]
    last = msgs[-1] if msgs else None
    action = db.get(AgentAction, t.related_action_id) if t.related_action_id else None
    human = [m for m in msgs if m["author"] != "system"]
    out = {"id": t.id, "number": f"PP-{1000 + t.id}", "customer_id": t.customer_id, "customer_name": t.customer_name,
           "subject": t.subject, "category": t.category, "category_label": CATEGORY_LABELS.get(t.category, "Other"),
           "priority": t.priority, "status": t.status, "order_id": t.order_id, "source": t.source,
           "triage_reason": t.triage_reason,
           "related_action": {"id": action.id, "type": action.action_type, "title": action.title} if action else None,
           "waiting_on": ("owner" if human and human[-1]["author"] == "customer" else "customer") if t.status != "resolved" else None,
           "last_message": (last["body"][:140] if last else ""), "message_count": len(human),
           "created_at": _iso(t.created_at), "updated_at": _iso(t.updated_at), "resolved_at": _iso(t.resolved_at)}
    if with_messages:
        out["messages"] = msgs
    return out


def sort_tickets(tickets: list[SupportTicket]) -> list[SupportTicket]:
    return sorted(tickets, key=lambda t: (t.status == "resolved", PRIORITY_ORDER.get(t.priority, 9),
                                          -(t.updated_at or t.created_at).timestamp()))


# ------------------------------------------------------------------ tickets

def _customer(db: Session, customer_id: str) -> Customer:
    c = db.query(Customer).filter(Customer.customer_id == customer_id).first()
    if not c:
        raise SupportError("Customer not found", 404)
    return c


def _segment(db: Session, customer_id: str) -> str:
    try:
        from app.analytics.customers import customer_features
        f = customer_features(db)
        row = f[f.customer_id == customer_id]
        return "" if row.empty else str(row.iloc[0]["segment"])
    except Exception:  # triage must never fail because analytics could not run
        return ""


def _opt_out(db: Session, c: Customer, ticket: SupportTicket | None = None) -> bool:
    if not c.marketing_opt_in:
        return False
    c.marketing_opt_in = False
    if ticket is not None:
        ticket.messages.append(TicketMessage(author="system", body="Promotional messages turned off for this customer, as requested. "
                                                                   "The agent will not send them offers."))
    return True


def create_ticket(db: Session, customer_id: str, subject: str, message: str, category: str = "",
                  order_id: str = "", related_action_id: int | None = None, source: str = "portal",
                  created_at: datetime | None = None) -> SupportTicket:
    c = _customer(db, customer_id)
    subject, message = subject.strip(), message.strip()
    if not message:
        raise SupportError("Please describe the problem")
    tri = classify(subject, message, _segment(db, customer_id), category)
    now = created_at or datetime.utcnow()
    t = SupportTicket(customer_id=c.customer_id, customer_name=c.customer_name, subject=subject or message[:80],
                      category=tri["category"], priority=tri["priority"], triage_reason=tri["reason"],
                      order_id=order_id or "", related_action_id=related_action_id, source=source,
                      created_at=now, updated_at=now)
    t.messages.append(TicketMessage(author="customer", body=message, created_at=now))
    db.add(t)
    if tri["category"] == "account" and is_opt_out(f"{subject} {message}"):
        _opt_out(db, c, t)
    db.commit()
    db.refresh(t)
    return t


def get_ticket(db: Session, ticket_id: int, customer_id: str | None = None) -> SupportTicket:
    t = db.get(SupportTicket, ticket_id)
    if not t or (customer_id and t.customer_id != customer_id):
        raise SupportError("Ticket not found", 404)
    return t


def customer_message(db: Session, t: SupportTicket, body: str) -> SupportTicket:
    if not body.strip():
        raise SupportError("Message is empty")
    t.messages.append(TicketMessage(author="customer", body=body.strip()))
    if t.status == "resolved":
        t.status, t.resolved_at = "open", None
        t.messages.append(TicketMessage(author="system", body="Reopened by the customer."))
    t.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(t)
    return t


def owner_reply(db: Session, t: SupportTicket, body: str, status: str = "in_progress",
                payment_link_amount: float | None = None) -> SupportTicket:
    if status not in ("open", "in_progress", "resolved"):
        raise SupportError("Unknown status")
    if not body.strip():
        raise SupportError("Reply is empty")
    attachments = {}
    if payment_link_amount:
        if payment_link_amount <= 0:
            raise SupportError("Payment link amount must be positive")
        from app.integrations.razorpay_client import get_razorpay
        c = _customer(db, t.customer_id)
        link = get_razorpay().create_payment_link(
            payment_link_amount, {"name": c.customer_name, "email": c.email, "phone": c.phone},
            f"Payment for ticket PP-{1000 + t.id}", f"pp-ticket-{t.id}-{int(time.time())}",
            notes={"paypilot_ticket_id": t.id, "customer_id": c.customer_id})
        attachments["payment_link"] = link
        body = f"{body.strip()}\n\nComplete your payment here: {link['short_url']}"
    t.messages.append(TicketMessage(author="owner", body=body.strip(), attachments=attachments))
    set_status(db, t, status, commit=False)
    db.commit()
    db.refresh(t)
    return t


def set_status(db: Session, t: SupportTicket, status: str, commit: bool = True) -> SupportTicket:
    if status not in ("open", "in_progress", "resolved"):
        raise SupportError("Unknown status")
    if status != t.status:
        if status == "resolved":
            t.resolved_at = datetime.utcnow()
            t.messages.append(TicketMessage(author="system", body="Marked as resolved by the store."))
        elif t.status == "resolved":
            t.resolved_at = None
            t.messages.append(TicketMessage(author="system", body="Reopened by the store."))
        t.status = status
    t.updated_at = datetime.utcnow()
    if commit:
        db.commit()
        db.refresh(t)
    return t


# ------------------------------------------------------------------ responses to PayPilot messages

def latest_responses(db: Session, action_ids: list[int]) -> dict[int, CustomerResponse]:
    out: dict[int, CustomerResponse] = {}
    if action_ids:
        for r in db.query(CustomerResponse).filter(CustomerResponse.action_id.in_(action_ids)).order_by(CustomerResponse.id):
            out[r.action_id] = r
    return out


def respond_to_action(db: Session, customer_id: str, action_id: int, response: str, note: str = "") -> dict:
    """Record a customer's answer to a sent PayPilot message and apply its effect."""
    if response not in RESPONSES:
        raise SupportError("Unknown response")
    c = _customer(db, customer_id)
    a = db.get(AgentAction, action_id)
    if not a or a.customer_id != customer_id or a.status != "executed":
        raise SupportError("Message not found", 404)
    final = db.query(CustomerResponse).filter(CustomerResponse.action_id == a.id,
                                              CustomerResponse.response.in_(("paid", "not_interested", "stop"))).first()
    has_outcome = db.query(Outcome).filter(Outcome.action_id == a.id).first() is not None
    if response != "question" and (final or (has_outcome and response == "paid")):
        raise SupportError("You've already answered this message", 409)

    result: dict = {"response": response}
    if response == "paid":
        from app.integrations.razorpay_client import get_razorpay
        link = (a.result or {}).get("payment_link")
        if get_razorpay().live and link:
            result["pay_url"] = link.get("short_url")  # the real payment is confirmed by Razorpay's webhook
        else:
            from app.api.razorpay import record_event
            value = (a.payload or {}).get("value") or 1000
            amount = round(value * (1 - (a.discount_pct or 0) / 100), 2)
            event = {"payment": {"entity": {"id": "pay_portal" + uuid.uuid4().hex[:10], "amount": int(amount * 100),
                                            "method": "upi", "created_at": int(time.time()), "email": c.email,
                                            "notes": {"paypilot_action_id": str(a.id), "customer_id": c.customer_id,
                                                      "cart_id": ((a.payload or {}).get("facts") or {}).get("cart_id") or ""}}}}
            kind = "payment_link.paid" if a.action_type in ("cart_recovery", "payment_retry") else "payment.captured"
            result["payment"] = record_event(db, kind, event)
            result["amount"] = amount
    elif response in ("not_interested", "stop"):
        if not has_outcome:
            db.add(Outcome(action_id=a.id, converted=False, revenue=0))
        if response == "stop":
            result["opted_out"] = _opt_out(db, c)
    elif response == "question":
        t = create_ticket(db, customer_id, f"Re: {MESSAGE_LABELS.get(a.action_type, 'your message')}", note, related_action_id=a.id, source="message_reply")
        result["ticket"] = serialize_ticket(t, db)

    db.add(CustomerResponse(action_id=a.id, customer_id=customer_id, response=response, note=note.strip()[:2000]))
    db.commit()
    return result


# ------------------------------------------------------------------ portal view

def recent_orders(db: Session, customer_id: str, limit: int = 5) -> list[dict]:
    rows = (db.query(Sale.order_id, Sale.transaction_date, Sale.amount, Sale.quantity, Product.product_name)
              .join(Customer, Customer.customer_key == Sale.customer_key)
              .join(Product, Product.product_key == Sale.product_key)
              .filter(Customer.customer_id == customer_id)
              .order_by(Sale.transaction_date.desc()).limit(40).all())
    orders: dict[str, dict] = {}
    for oid, when, amount, qty, name in rows:
        key = oid or f"{when:%Y%m%d%H%M}"
        o = orders.setdefault(key, {"order_id": key, "date": _iso(when), "amount": 0.0, "items": []})
        o["amount"] = round(o["amount"] + (amount or 0), 2)
        o["items"].append(f"{name}" + (f" x{qty}" if qty and qty > 1 else ""))
    return list(orders.values())[:limit]


def portal_view(db: Session, customer_id: str) -> dict:
    c = _customer(db, customer_id)
    actions = (db.query(AgentAction).filter(AgentAction.customer_id == customer_id, AgentAction.status == "executed")
                 .order_by(AgentAction.executed_at.desc()).all())
    responses = latest_responses(db, [a.id for a in actions])
    outcomes = {o.action_id: o for o in db.query(Outcome).filter(Outcome.action_id.in_([a.id for a in actions]))} if actions else {}
    messages = []
    for a in actions:
        r, o = responses.get(a.id), outcomes.get(a.id)
        messages.append({"id": a.id, "type": a.action_type, "label": MESSAGE_LABELS.get(a.action_type, "Message"), "message": a.message, "channel": a.channel,
                         "sent_at": _iso(a.executed_at), "discount_pct": a.discount_pct,
                         "value": (a.payload or {}).get("value"),
                         "payment_link": (a.result or {}).get("payment_link"), "coupon": (a.result or {}).get("coupon"),
                         "response": r.response if r else None,
                         "paid": bool(o and o.converted), "paid_amount": round(o.revenue) if o and o.converted else 0})
    tickets = sort_tickets(db.query(SupportTicket).filter(SupportTicket.customer_id == customer_id).all())
    return {"customer": {"customer_id": c.customer_id, "name": c.customer_name, "first_name": c.customer_name.split()[0],
                         "city": c.city, "email": c.email, "marketing_opt_in": c.marketing_opt_in},
            "orders": recent_orders(db, customer_id), "messages": messages,
            "tickets": [serialize_ticket(t, db) for t in tickets]}


# ------------------------------------------------------------------ owner stats

def support_stats(db: Session) -> dict:
    counts = dict(db.query(SupportTicket.status, func.count(SupportTicket.id)).group_by(SupportTicket.status).all())
    active = db.query(SupportTicket).filter(SupportTicket.status != "resolved").all()
    resolved = db.query(SupportTicket).filter(SupportTicket.status == "resolved", SupportTicket.resolved_at.isnot(None)).all()
    hours = [(t.resolved_at - t.created_at).total_seconds() / 3600 for t in resolved if t.resolved_at >= t.created_at]
    resp = dict(db.query(CustomerResponse.response, func.count(CustomerResponse.id)).group_by(CustomerResponse.response).all())
    feed = db.query(CustomerResponse).order_by(CustomerResponse.id.desc()).limit(8).all()
    names = {c.customer_id: c.customer_name for c in db.query(Customer).filter(
        Customer.customer_id.in_([r.customer_id for r in feed]))} if feed else {}
    titles = {a.id: a.title for a in db.query(AgentAction).filter(AgentAction.id.in_([r.action_id for r in feed]))} if feed else {}
    return {"open": counts.get("open", 0), "in_progress": counts.get("in_progress", 0), "resolved": counts.get("resolved", 0),
            "urgent": sum(1 for t in active if t.priority == "urgent"),
            "waiting_on_owner": sum(1 for t in active if serialize_ticket(t, db, False)["waiting_on"] == "owner"),
            "avg_resolution_hours": round(sum(hours) / len(hours), 1) if hours else None,
            "responses": {k: resp.get(k, 0) for k in RESPONSES},
            "recent_responses": [{"customer_name": names.get(r.customer_id, r.customer_id), "customer_id": r.customer_id,
                                  "response": r.response, "note": r.note, "action_title": titles.get(r.action_id, ""),
                                  "created_at": _iso(r.created_at)} for r in feed]}
