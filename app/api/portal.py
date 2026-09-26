"""
Customer portal API: what a shopper sees and does.

Identity here is a customer id (demo sign-in by email or from a list). In production put this
behind OTP or magic-link login and derive customer_id from the session, never from the URL.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AgentAction, Customer, SupportTicket
from app.support.service import (SupportError, create_ticket, customer_message, get_ticket, portal_view,
                                 respond_to_action, serialize_ticket)

router = APIRouter(prefix="/api/portal", tags=["customer portal"])


class TicketIn(BaseModel):
    subject: str = Field("", max_length=200)
    message: str = Field(..., min_length=3, max_length=4000)
    category: str = ""
    order_id: str = ""


class MessageIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)


class ResponseIn(BaseModel):
    response: str = Field(..., pattern="^(paid|not_interested|stop|question)$")
    note: str = Field("", max_length=4000)


def _guard(fn):
    try:
        return fn()
    except SupportError as e:
        raise HTTPException(e.status, str(e))


@router.get("/customers")
def find_customers(q: str = "", limit: int = 8, db: Session = Depends(get_db)):
    """Demo sign-in helper: search by name or email; with no query, customers who have messages or tickets come first."""
    query = db.query(Customer)
    if q.strip():
        like = f"%{q.strip()}%"
        rows = query.filter(or_(Customer.customer_name.ilike(like), Customer.email.ilike(like))).limit(limit).all()
    else:
        messaged = [cid for (cid,) in db.query(AgentAction.customer_id).filter(AgentAction.status == "executed")
                                         .order_by(AgentAction.executed_at.desc())]
        ticketed = [cid for (cid,) in db.query(SupportTicket.customer_id).order_by(SupportTicket.updated_at.desc())]
        order = list(dict.fromkeys(messaged + ticketed))[:limit]  # customers with store messages first
        active = set(order)
        by_id = {c.customer_id: c for c in db.query(Customer).filter(Customer.customer_id.in_(active))} if active else {}
        first = [by_id[cid] for cid in order if cid in by_id]
        rest = query.filter(~Customer.customer_id.in_(active)).limit(max(0, limit - len(first))).all()
        rows = first + rest
    return [{"customer_id": c.customer_id, "name": c.customer_name, "email": c.email, "city": c.city} for c in rows]


@router.get("/{customer_id}")
def view(customer_id: str, db: Session = Depends(get_db)):
    return _guard(lambda: portal_view(db, customer_id))


@router.post("/{customer_id}/messages/{action_id}/respond")
def respond(customer_id: str, action_id: int, body: ResponseIn, db: Session = Depends(get_db)):
    if body.response == "question" and len(body.note.strip()) < 3:
        raise HTTPException(422, "Please write your question")
    return _guard(lambda: respond_to_action(db, customer_id, action_id, body.response, body.note))


@router.post("/{customer_id}/tickets")
def raise_ticket(customer_id: str, body: TicketIn, db: Session = Depends(get_db)):
    return _guard(lambda: serialize_ticket(
        create_ticket(db, customer_id, body.subject, body.message, body.category, body.order_id), db))


@router.post("/{customer_id}/tickets/{ticket_id}/messages")
def add_message(customer_id: str, ticket_id: int, body: MessageIn, db: Session = Depends(get_db)):
    return _guard(lambda: serialize_ticket(customer_message(db, get_ticket(db, ticket_id, customer_id), body.body), db))
