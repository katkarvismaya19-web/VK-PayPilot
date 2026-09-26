"""
Guardrails are enforced in code, after the LLM decides. The model can propose
anything; only policy-compliant actions reach customers.
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from app.analytics.frames import now
from app.config import get_settings
from app.models import AgentAction

SEGMENT_DISCOUNT_CAP = {"Champions": 0, "Loyal": 0, "Promising": 0, "Potential Loyalist": 0, "New": 0,
                        "At Risk": 15, "Can't Lose Them": 15, "Hibernating": 10, "Need Attention": 10, "Lost": 0}
KIND_DISCOUNT_CAP = {"payment_retry": 0, "cart_recovery": 10, "vip_reward": 0, "cross_sell": 0, "second_order": 0}


def recent_action_count(db: Session, customer_id: str, days: int = 7) -> int:
    since = now() - timedelta(days=days)
    return (db.query(AgentAction)
              .filter(AgentAction.customer_id == customer_id, AgentAction.created_at >= since,
                      AgentAction.status.in_(["proposed", "executed"]))
              .count())


def check_eligibility(db: Session, opp) -> str | None:
    """Return a reason string if the opportunity must be skipped, else None."""
    s = get_settings()
    if not opp.transactional and not opp.customer.get("opt_in", True):
        return "no marketing consent"
    if recent_action_count(db, opp.customer_id) >= s.max_actions_per_customer_per_week:
        return f"frequency cap ({s.max_actions_per_customer_per_week}/week) reached"
    return None


def clamp_discount(kind: str, segment: str, requested: int, margin_floor_cap: int | None = None) -> tuple[int, list[str]]:
    s = get_settings()
    notes = []
    cap = min(s.max_discount_percent, KIND_DISCOUNT_CAP.get(kind, SEGMENT_DISCOUNT_CAP.get(segment, 0)))
    if kind == "winback_offer":
        cap = min(s.max_discount_percent, SEGMENT_DISCOUNT_CAP.get(segment, 0))
    if margin_floor_cap is not None:
        cap = min(cap, margin_floor_cap)
    requested = max(0, int(requested or 0))
    if requested > cap:
        notes.append(f"discount reduced from {requested}% to {cap}% by policy")
        requested = cap
    return requested, notes


def risk_level(discount: int, value: float, store_aov: float, involves_payment: bool = False) -> str:
    """Risk = money the agent could give away or mishandle. Value only matters when a payment link is involved."""
    if discount > 15 or (involves_payment and value > 5 * store_aov):
        return "high"
    if discount > 0 or (involves_payment and value > 3 * store_aov):
        return "medium"
    return "low"
