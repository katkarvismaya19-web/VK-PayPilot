"""Owner-side support inbox: triaged tickets, AI-drafted replies grounded in the support playbook, resolution."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SupportTicket
from app.support.service import SupportError, get_ticket, owner_reply, serialize_ticket, set_status, sort_tickets, support_stats
from app.support.triage import suggest_reply

router = APIRouter(prefix="/api/support", tags=["support"])


class ReplyIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    status: str = Field("in_progress", pattern="^(open|in_progress|resolved)$")
    payment_link_amount: float | None = Field(None, gt=0, le=500000)


class StatusIn(BaseModel):
    status: str = Field(..., pattern="^(open|in_progress|resolved)$")


def _guard(fn):
    try:
        return fn()
    except SupportError as e:
        raise HTTPException(e.status, str(e))


def _customer_context(db: Session, customer_id: str) -> dict:
    from app.analytics.customers import customer_features
    f = customer_features(db)
    row = f[f.customer_id == customer_id]
    if row.empty:
        return {}
    r = row.iloc[0]
    return {"segment": str(r["segment"]), "orders": int(r["frequency"]), "lifetime_spend": round(float(r["monetary"])),
            "clv_12m": round(float(r["clv_12m"])), "churn_risk": round(float(r["churn_risk"]), 2),
            "recency_days": int(r["recency_days"]), "city": str(r.get("city", ""))}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return support_stats(db)


@router.get("/tickets")
def list_tickets(status: str = "active", category: str = "", db: Session = Depends(get_db)):
    q = db.query(SupportTicket)
    if status == "active":
        q = q.filter(SupportTicket.status != "resolved")
    elif status:
        q = q.filter(SupportTicket.status == status)
    if category:
        q = q.filter(SupportTicket.category == category)
    return [serialize_ticket(t, db, with_messages=False) for t in sort_tickets(q.all())]


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: int, db: Session = Depends(get_db)):
    t = _guard(lambda: get_ticket(db, ticket_id))
    data = serialize_ticket(t, db)
    data["customer"] = _customer_context(db, t.customer_id)
    data["suggested_reply"] = suggest_reply(data, data["customer"]) if t.status != "resolved" else None
    return data


@router.post("/tickets/{ticket_id}/reply")
def reply(ticket_id: int, body: ReplyIn, db: Session = Depends(get_db)):
    t = _guard(lambda: get_ticket(db, ticket_id))
    return _guard(lambda: serialize_ticket(owner_reply(db, t, body.body, body.status, body.payment_link_amount), db))


@router.post("/tickets/{ticket_id}/status")
def change_status(ticket_id: int, body: StatusIn, db: Session = Depends(get_db)):
    t = _guard(lambda: get_ticket(db, ticket_id))
    return serialize_ticket(set_status(db, t, body.status), db)
