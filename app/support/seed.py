"""Seed a realistic support inbox from the store's own data (customers who really had failed payments, carts, etc.)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Cart, Customer, Payment, SupportTicket, TicketMessage
from app.support.service import create_ticket, owner_reply, recent_orders, set_status


def _inr(v: float) -> str:
    return f"₹{round(v):,}"


def seed_tickets(db: Session) -> int:
    if db.query(SupportTicket).count():
        return 0
    now = datetime.utcnow()
    used: set[str] = set()
    names: set[str] = set()
    name_of = {c.customer_id: c.customer_name for c in db.query(Customer)}

    def claim(cid: str) -> bool:
        if cid in used or name_of.get(cid) in names:
            return False
        used.add(cid)
        names.add(name_of.get(cid))
        return True

    def pick(q):
        for row in q:
            if claim(row.customer_id):
                return row
        return None

    made = []
    failed = pick(db.query(Payment).filter(Payment.status == "failed").order_by(Payment.created_at.desc()).limit(40))
    if failed:
        made.append(create_ticket(
            db, failed.customer_id, "Money debited but order failed",
            f"I paid {_inr(failed.amount)} through {failed.method.upper()} and the amount was deducted from my bank, "
            "but the website said payment failed and I have no order confirmation. Please check urgently.",
            created_at=now - timedelta(hours=3)))

    cart = pick(db.query(Cart).filter(Cart.status == "abandoned").order_by(Cart.value.desc()).limit(40))
    if cart:
        made.append(create_ticket(
            db, cart.customer_id, "Coupon not applying at checkout",
            f"I have {len(cart.items or [])} items in my cart worth {_inr(cart.value)}. The code from your message says "
            "'invalid' when I apply it. Can you check?", created_at=now - timedelta(hours=7)))

    customers = db.query(Customer).order_by(Customer.customer_key).all()
    with_orders = [c for c in customers if c.customer_id not in used]

    def next_with_order():
        for c in with_orders:
            if c.customer_id not in used and c.customer_name not in names:
                orders = recent_orders(db, c.customer_id, 1)
                if orders and claim(c.customer_id):
                    return c, orders[0]
        return None, None

    c, o = next_with_order()
    if c:
        made.append(create_ticket(
            db, c.customer_id, "Order still not delivered",
            f"My order {o['order_id']} ({', '.join(o['items'][:2])}) was supposed to arrive by Monday. It still hasn't come "
            "and tracking hasn't updated in 3 days. Very disappointed.", order_id=o["order_id"],
            created_at=now - timedelta(hours=20)))

    c, o = next_with_order()
    if c:
        t = create_ticket(
            db, c.customer_id, "Refund for returned item",
            f"I returned {o['items'][0]} last week. When will I get my refund?", order_id=o["order_id"],
            created_at=now - timedelta(days=2, hours=4))
        t.messages[0].created_at = t.created_at
        owner_reply(db, t, f"Hi {c.customer_name.split()[0]}, we received your return and the refund has been issued "
                           "to your original payment method. UPI refunds usually arrive within 2 to 3 working days.",
                    status="in_progress")
        made.append(t)

    c, o = next_with_order()
    if c:
        made.append(create_ticket(
            db, c.customer_id, "Size question before ordering",
            "Does the cotton kurta run true to size? I'm usually an L but between sizes. What's the fabric?",
            created_at=now - timedelta(hours=30)))

    c, o = next_with_order()
    if c:
        t = create_ticket(db, c.customer_id, "Wrong colour delivered",
                          f"I ordered {o['items'][0]} in blue but received black. Can I exchange it?",
                          order_id=o["order_id"], created_at=now - timedelta(days=4))
        owner_reply(db, t, f"Hi {c.customer_name.split()[0]}, sorry about the mix-up. We've arranged a free pickup "
                           "tomorrow and the blue one ships as soon as it's collected.", status="in_progress")
        t.messages.append(TicketMessage(author="customer", body="Pickup done, got the right one today. Thanks!",
                                        created_at=now - timedelta(days=1)))
        set_status(db, t, "resolved")
        made.append(t)

    # backdate owner/system messages so the timeline reads naturally
    for t in made:
        for i, m in enumerate(t.messages):
            if m.created_at is None or m.created_at > now - timedelta(minutes=1):
                m.created_at = t.created_at + timedelta(hours=2 + i)
        t.updated_at = max(m.created_at for m in t.messages)
        if t.resolved_at:
            t.resolved_at = t.updated_at
    db.commit()
    return len(made)
