from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.analytics.affinity import product_pairs, recommend_for_customer
from app.analytics.customers import customer_features, segment_summary
from app.analytics.frames import carts_frame
from app.analytics.metrics import category_performance, kpis, payment_failures, revenue_trend
from app.database import get_db
from app.models import AgentAction

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/kpis")
def get_kpis(db: Session = Depends(get_db)):
    return kpis(db)


@router.get("/trend")
def get_trend(weeks: int = 16, db: Session = Depends(get_db)):
    return revenue_trend(db, weeks)


@router.get("/segments")
def get_segments(db: Session = Depends(get_db)):
    return segment_summary(customer_features(db))


@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    return category_performance(db)


@router.get("/payment-failures")
def get_failures(db: Session = Depends(get_db)):
    return payment_failures(db)


@router.get("/affinity")
def get_affinity(db: Session = Depends(get_db)):
    return product_pairs(db)


@router.get("/customers")
def list_customers(segment: str | None = None, sort: str = "value_at_risk", limit: int = 50, db: Session = Depends(get_db)):
    """Default sort: expected 12-month spend at risk = average yearly spend x churn risk."""
    f = customer_features(db)
    if f.empty:
        return []
    yearly = f["monetary"] / (f["tenure_days"].fillna(365) / 365).clip(lower=0.25)
    f["value_at_risk"] = (yearly.clip(upper=f["monetary"] * 4) * f["churn_risk"].fillna(0)).round(0)
    if segment:
        f = f[f.segment == segment]
    if sort in f.columns:
        f = f.sort_values(sort, ascending=False)
    cols = ["customer_id", "customer_name", "city", "segment", "frequency", "monetary", "aov", "recency_days",
            "churn_risk", "value_at_risk", "clv_12m", "favorite_category", "R", "F", "M"]
    return f[cols].head(limit).round(2).fillna("").to_dict("records")


@router.get("/customers/{customer_id}")
def customer_profile(customer_id: str, db: Session = Depends(get_db)):
    f = customer_features(db)
    row = f[f.customer_id == customer_id]
    if row.empty:
        raise HTTPException(404, "Customer not found")
    actions = (db.query(AgentAction).filter(AgentAction.customer_id == customer_id)
                 .order_by(AgentAction.created_at.desc()).limit(10).all())
    rec = row.iloc[0].to_dict()
    profile = {k: (str(v) if hasattr(v, "isoformat") else (round(v, 2) if isinstance(v, float) else v))
               for k, v in rec.items()}
    return {"profile": {k: ("" if v != v else v) for k, v in profile.items()},  # NaN -> ""
            "recommendations": recommend_for_customer(db, customer_id),
            "actions": [{"id": a.id, "type": a.action_type, "title": a.title, "status": a.status,
                         "created_at": a.created_at.isoformat()} for a in actions]}


@router.get("/carts")
def list_carts(status: str = "abandoned", db: Session = Depends(get_db)):
    c = carts_frame(db)
    if c.empty:
        return []
    c = c[c.status == status].sort_values("value", ascending=False)
    c["updated_at"] = c["updated_at"].astype(str)
    return c.to_dict("records")
