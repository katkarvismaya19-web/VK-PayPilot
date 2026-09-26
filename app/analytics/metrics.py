"""Store-level KPIs and trends."""
from datetime import timedelta

import pandas as pd
from sqlalchemy.orm import Session

from app.analytics.frames import carts_frame, now, payments_frame, sales_frame


def _pct(a: float, b: float) -> float | None:
    return None if not b else round((a - b) / b * 100, 1)


def kpis(db: Session) -> dict:
    sales, carts, pays = sales_frame(db), carts_frame(db), payments_frame(db)
    t = pd.Timestamp(now())
    cur = sales[sales.transaction_date > t - timedelta(days=30)]
    prev = sales[(sales.transaction_date <= t - timedelta(days=30)) & (sales.transaction_date > t - timedelta(days=60))]

    def block(df):
        orders = df.order_id.nunique()
        rev = float(df.amount.sum())
        return rev, orders, (rev / orders if orders else 0.0), df.customer_id.nunique()

    rev, orders, aov, active = block(cur)
    prev_rev, prev_orders, prev_aov, prev_active = block(prev)

    per_cust = sales.groupby("customer_id").order_id.nunique()
    repeat_rate = float((per_cust > 1).mean() * 100) if len(per_cust) else 0.0

    abandoned = carts[carts.status == "abandoned"] if not carts.empty else carts
    recent_pays = pays[pays.created_at > t - timedelta(days=30)] if not pays.empty else pays
    success = float((recent_pays.status == "captured").mean() * 100) if len(recent_pays) else 100.0
    failed_value = float(recent_pays[recent_pays.status == "failed"].amount.sum()) if len(recent_pays) else 0.0

    return {
        "revenue_30d": round(rev), "revenue_change_pct": _pct(rev, prev_rev),
        "orders_30d": int(orders), "orders_change_pct": _pct(orders, prev_orders),
        "aov_30d": round(aov), "aov_change_pct": _pct(aov, prev_aov),
        "active_customers_30d": int(active), "active_change_pct": _pct(active, prev_active),
        "repeat_rate_pct": round(repeat_rate, 1),
        "abandoned_carts": int(len(abandoned)),
        "abandoned_value": round(float(abandoned.value.sum())) if len(abandoned) else 0,
        "payment_success_rate_pct": round(success, 1),
        "failed_payment_value_30d": round(failed_value),
        "total_customers": int(sales.customer_id.nunique()),
    }


def revenue_trend(db: Session, weeks: int = 16) -> list[dict]:
    sales = sales_frame(db)
    if sales.empty:
        return []
    start = pd.Timestamp(now()) - timedelta(weeks=weeks)
    s = sales[sales.transaction_date >= start].set_index("transaction_date")
    w = s.resample("W-MON").agg({"amount": "sum", "order_id": "nunique"}).reset_index()
    return [{"week": d.strftime("%d %b"), "revenue": round(float(a)), "orders": int(o)}
            for d, a, o in zip(w.transaction_date, w.amount, w.order_id)]


def category_performance(db: Session) -> list[dict]:
    sales = sales_frame(db)
    if sales.empty:
        return []
    sales["profit"] = sales.amount * sales.margin_pct / 100
    g = (sales.groupby("category").agg(revenue=("amount", "sum"), profit=("profit", "sum"),
                                       units=("quantity", "sum"), customers=("customer_id", "nunique"))
              .reset_index().sort_values("revenue", ascending=False))
    return [{k: (round(float(v)) if k != "category" else v) for k, v in r.items()} for r in g.to_dict("records")]


def payment_failures(db: Session) -> list[dict]:
    pays = payments_frame(db)
    if pays.empty:
        return []
    f = pays[pays.status == "failed"]
    g = f.groupby(["error_reason", "method"]).agg(count=("payment_id", "count"), value=("amount", "sum")).reset_index()
    return [{**r, "value": round(float(r["value"]))} for r in g.sort_values("value", ascending=False).to_dict("records")]
