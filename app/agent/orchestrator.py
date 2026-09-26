"""
Agent loop:  perceive -> prioritise -> retrieve evidence -> decide -> guardrails -> (execute) -> log
"""
import uuid

from sqlalchemy.orm import Session

from app.agent.executor import execute_action
from app.agent.guardrails import check_eligibility, clamp_discount, risk_level
from app.agent.perception import detect_opportunities
from app.agent.planner import decide
from app.config import get_settings
from app.models import AgentAction, Product
from app.rag.knowledge_base import get_kb


def _margin_cap(db: Session, items: list[dict]) -> int | None:
    ids = [i["product_id"] for i in items or []]
    if not ids:
        return None
    margins = [p.margin_pct for p in db.query(Product).filter(Product.product_id.in_(ids)).all()]
    return max(0, int(min(margins) - 10)) if margins else None  # keep >= 10% gross margin


def run_agent(db: Session, max_actions: int = 25, kinds: list[str] | None = None) -> dict:
    s = get_settings()
    run_id = "run_" + uuid.uuid4().hex[:10]
    opps, ctx = detect_opportunities(db)
    if kinds:
        opps = [o for o in opps if o.kind in kinds]
    opps.sort(key=lambda o: o.expected_value, reverse=True)

    skipped, chosen, seen = [], [], set()
    for o in opps:
        if o.customer_id in seen:
            skipped.append({"customer_id": o.customer_id, "kind": o.kind, "reason": "higher-value action already chosen for this customer"})
            continue
        if reason := check_eligibility(db, o):
            skipped.append({"customer_id": o.customer_id, "kind": o.kind, "reason": reason})
            continue
        seen.add(o.customer_id)
        chosen.append(o)
        if len(chosen) >= max_actions:
            break

    kb = get_kb()
    batch = [(i, o, kb.evidence(o.evidence_query, k=3)) for i, o in enumerate(chosen)]
    decisions, engine = decide(batch, ctx.get("store_aov", 1000))

    created = []
    for i, o, ev in batch:
        d = decisions[i]
        if not d.get("act", True):
            skipped.append({"customer_id": o.customer_id, "kind": o.kind, "reason": "agent chose not to act: " + d.get("reasoning", "")[:120]})
            continue
        margin_cap = _margin_cap(db, o.facts.get("items")) if o.kind == "cart_recovery" else None
        discount, notes = clamp_discount(o.kind, o.customer["segment"], d.get("discount_pct", 0), margin_cap)
        cited = [e for e in ev.citations() if e["id"] in set(d.get("citations") or [])] or ev.citations()[:1]
        channel = d.get("channel", "email") if d.get("channel") in ("email", "whatsapp", "sms") else "email"
        action = AgentAction(
            run_id=run_id, action_type=o.kind, customer_id=o.customer_id, customer_name=o.customer["name"],
            title=d.get("title") or o.kind, reasoning=d.get("reasoning", "") + (" " + "; ".join(notes) if notes else ""),
            message=d.get("message", ""), channel=channel, discount_pct=discount,
            expected_value=o.expected_value, confidence=round(o.probability, 3),
            risk=risk_level(discount, o.value, ctx.get("store_aov", 1000), o.kind in ("cart_recovery", "payment_retry")), citations=cited,
            payload={"facts": o.facts, "customer": o.customer, "value": round(o.value), "transactional": o.transactional},
        )
        db.add(action)
        db.flush()
        if s.auto_execute and action.risk == "low":
            execute_action(db, action)
        created.append(action)
    db.commit()

    return {
        "run_id": run_id, "engine": engine, "opportunities_found": len(opps),
        "actions_proposed": len(created), "skipped": len(skipped),
        "expected_value": round(sum(a.expected_value for a in created)),
        "by_type": {k: sum(1 for a in created if a.action_type == k) for k in {a.action_type for a in created}},
        "skip_log": skipped[:50], "action_ids": [a.id for a in created],
    }
