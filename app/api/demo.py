"""Demo utilities: simulate customer responses so the full loop can be shown without real traffic."""
import random
import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.analytics.etl import load_csv_dir
from app.api.razorpay import record_event
from app.database import get_db
from app.models import AgentAction, Outcome

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.post("/simulate-outcomes")
def simulate_outcomes(seed: int | None = None, db: Session = Depends(get_db)):
    """Each executed action converts with its estimated probability (boosted x2 for demo visibility).
    Conversions arrive as Razorpay-shaped webhook events, exercising the real attribution path."""
    rng = random.Random(seed)
    done = {o.action_id for o in db.query(Outcome).all()}
    results = []
    checked = 0
    revenue = 0.0
    for a in db.query(AgentAction).filter(AgentAction.status == "executed").all():
        if a.id in done:
            continue
        checked += 1
        converted = rng.random() < min(0.9, a.confidence * 2)
        if not converted:
            db.add(Outcome(action_id=a.id, converted=False, revenue=0))
            db.commit()
            continue
        value = (a.payload or {}).get("value") or 1000
        amount = value * (1 - a.discount_pct / 100)
        revenue += amount
        customer = (a.payload or {}).get("customer", {})
        event = {"payment": {"entity": {"id": "pay_sim" + uuid.uuid4().hex[:10], "amount": int(amount * 100),
                                        "method": rng.choice(["upi", "upi", "card"]), "created_at": int(time.time()),
                                        "email": customer.get("email"),
                                        "notes": {"paypilot_action_id": str(a.id), "customer_id": a.customer_id,
                                                  "cart_id": ((a.payload or {}).get("facts") or {}).get("cart_id") or ""}}}}
        results.append(record_event(db, "payment_link.paid" if a.action_type in ("cart_recovery", "payment_retry")
                                    else "payment.captured", event))
    return {"checked": checked, "conversions": len(results), "no_response": checked - len(results),
            "revenue": round(revenue), "events": results}


@router.post("/reset")
def reset(db: Session = Depends(get_db)):
    from data.generate_data import main as generate
    from pathlib import Path
    from app.models import CustomerResponse, SupportTicket, TicketMessage
    from app.support.seed import seed_tickets
    for model in (TicketMessage, SupportTicket, CustomerResponse, Outcome):
        db.query(model).delete()
    db.query(AgentAction).delete()
    db.commit()
    generate()
    loaded = load_csv_dir(db, Path(__file__).resolve().parents[2] / "data")
    loaded["tickets"] = seed_tickets(db)
    return loaded
