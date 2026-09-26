from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agent.assistant import ask
from app.agent.executor import execute_action
from app.agent.llm import LLMClient
from app.agent.orchestrator import run_agent
from app.config import get_settings
from app.database import get_db
from app.integrations.notifier import read_outbox
from app.integrations.razorpay_client import get_razorpay
from app.models import AgentAction, Outcome

router = APIRouter(prefix="/api/agent", tags=["agent"])


class RunRequest(BaseModel):
    max_actions: int = Field(25, ge=1, le=200)
    kinds: list[str] | None = None


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=500)


def serialize(a: AgentAction, outcome: Outcome | None = None) -> dict:
    return {"outcome": None if outcome is None else {"converted": outcome.converted, "revenue": round(outcome.revenue or 0)},
            "id": a.id, "run_id": a.run_id, "type": a.action_type, "customer_id": a.customer_id,
            "customer_name": a.customer_name, "title": a.title, "reasoning": a.reasoning, "message": a.message,
            "channel": a.channel, "discount_pct": a.discount_pct, "expected_value": a.expected_value,
            "confidence": a.confidence, "risk": a.risk, "citations": a.citations, "status": a.status,
            "result": a.result, "value": (a.payload or {}).get("value"),
            "segment": (a.payload or {}).get("customer", {}).get("segment"),
            "created_at": a.created_at.isoformat(), "executed_at": a.executed_at.isoformat() if a.executed_at else None}


@router.get("/status")
def status():
    s = get_settings()
    return {"engine": LLMClient().name, "llm_enabled": s.llm_enabled,
            "razorpay_mode": "live" if get_razorpay().live else "sandbox",
            "auto_execute": s.auto_execute, "max_discount_percent": s.max_discount_percent,
            "frequency_cap_per_week": s.max_actions_per_customer_per_week}


@router.post("/run")
def run(req: RunRequest, db: Session = Depends(get_db)):
    return run_agent(db, req.max_actions, req.kinds)


@router.get("/actions")
def list_actions(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(AgentAction)
    if status:
        q = q.filter(AgentAction.status == status)
    rows = q.order_by(AgentAction.expected_value.desc()).limit(limit).all()
    outcomes = {o.action_id: o for o in db.query(Outcome).filter(Outcome.action_id.in_([a.id for a in rows])).all()} if rows else {}
    return [serialize(a, outcomes.get(a.id)) for a in rows]


@router.post("/actions/{action_id}/execute")
def execute(action_id: int, db: Session = Depends(get_db)):
    a = db.get(AgentAction, action_id)
    if not a:
        raise HTTPException(404, "Action not found")
    if a.status == "rejected":
        raise HTTPException(409, "Action was rejected")
    return serialize(execute_action(db, a))


@router.post("/actions/{action_id}/reject")
def reject(action_id: int, db: Session = Depends(get_db)):
    a = db.get(AgentAction, action_id)
    if not a:
        raise HTTPException(404, "Action not found")
    if a.status == "executed":
        raise HTTPException(409, "Action already executed")
    a.status = "rejected"
    db.commit()
    return serialize(a)


@router.post("/actions/execute-low-risk")
def execute_low_risk(db: Session = Depends(get_db)):
    acts = db.query(AgentAction).filter(AgentAction.status == "proposed", AgentAction.risk == "low").all()
    return {"executed": [serialize(execute_action(db, a))["id"] for a in acts]}


@router.post("/ask")
def ask_paypilot(req: AskRequest, db: Session = Depends(get_db)):
    return ask(db, req.question)


@router.get("/impact")
def impact(db: Session = Depends(get_db)):
    executed = db.query(AgentAction).filter(AgentAction.status == "executed").count()
    proposed = db.query(AgentAction).filter(AgentAction.status == "proposed").count()
    conv = db.query(func.count(Outcome.id), func.coalesce(func.sum(Outcome.revenue), 0)).filter(Outcome.converted).one()
    responded = (db.query(func.count(Outcome.id)).join(AgentAction, Outcome.action_id == AgentAction.id)
                   .filter(AgentAction.status == "executed").scalar() or 0)
    by_type = (db.query(AgentAction.action_type, func.count(Outcome.id), func.coalesce(func.sum(Outcome.revenue), 0))
                 .join(Outcome, Outcome.action_id == AgentAction.id).filter(Outcome.converted)
                 .group_by(AgentAction.action_type).all())
    pipeline = db.query(func.coalesce(func.sum(AgentAction.expected_value), 0)).filter(AgentAction.status == "proposed").scalar()
    return {"executed": executed, "proposed": proposed, "conversions": conv[0], "no_response": responded - conv[0],
            "awaiting": executed - responded, "recovered_revenue": round(conv[1]),
            "conversion_rate_pct": round(conv[0] / executed * 100, 1) if executed else 0.0,
            "pipeline_expected_value": round(pipeline or 0),
            "by_type": [{"type": t, "conversions": c, "revenue": round(r)} for t, c, r in by_type]}


@router.get("/outbox")
def outbox(limit: int = 30):
    return read_outbox(limit)
