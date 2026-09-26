"""
Customer intelligence: RFM scoring, segmentation, churn risk and CLV.

Segments follow the standard RFM grid:
  Champions, Loyal, Potential Loyalist, New, Promising, Need Attention,
  At Risk, Can't Lose Them, Hibernating, Lost
"""
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.analytics.frames import customers_frame, now, sales_frame


def _score(series: pd.Series, reverse: bool = False) -> pd.Series:
    """Quintile score 1..5 that is robust to ties and tiny datasets."""
    ranks = series.rank(method="first", ascending=not reverse)
    return np.ceil(ranks / len(series) * 5).clip(1, 5).astype(int)


def _segment(r: int, f: int, m: int, orders: int, recency: int) -> str:
    if orders == 1 and recency <= 45:
        return "New"
    if r >= 4 and f >= 4:
        return "Champions"
    if r >= 3 and f >= 3:
        return "Loyal"
    if r >= 4 and f <= 2:
        return "Promising"
    if r >= 3 and f <= 2:
        return "Potential Loyalist"
    if r <= 2 and f >= 4 and m >= 4:
        return "Can't Lose Them"
    if r <= 2 and f >= 3:
        return "At Risk"
    if r == 3:
        return "Need Attention"
    if r == 2:
        return "Hibernating"
    return "Lost"


def customer_features(db: Session) -> pd.DataFrame:
    sales = sales_frame(db)
    custs = customers_frame(db)
    if sales.empty:
        return pd.DataFrame()

    today = pd.Timestamp(now())
    orders = (sales.groupby(["customer_id", "order_id"])
                   .agg(order_value=("amount", "sum"), order_date=("transaction_date", "min"))
                   .reset_index())

    agg = orders.groupby("customer_id").agg(
        frequency=("order_id", "nunique"),
        monetary=("order_value", "sum"),
        aov=("order_value", "mean"),
        first_order=("order_date", "min"),
        last_order=("order_date", "max"),
    )
    agg["recency_days"] = (today - agg["last_order"]).dt.days
    agg["tenure_days"] = (today - agg["first_order"]).dt.days.clip(lower=1)

    # Typical gap between orders (median), fallback to store-wide median
    gaps = (orders.sort_values("order_date").groupby("customer_id")["order_date"]
                  .apply(lambda s: s.diff().dt.days.median()))
    agg["avg_gap_days"] = gaps
    store_gap = float(np.nanmedian(agg["avg_gap_days"])) if agg["avg_gap_days"].notna().any() else 45.0
    agg["avg_gap_days"] = agg["avg_gap_days"].fillna(store_gap).clip(lower=7)

    fav = (sales.groupby(["customer_id", "category"])["amount"].sum()
                .reset_index().sort_values("amount", ascending=False)
                .drop_duplicates("customer_id").set_index("customer_id")["category"])
    agg["favorite_category"] = fav

    agg["R"] = _score(agg["recency_days"], reverse=True)
    agg["F"] = _score(agg["frequency"])
    agg["M"] = _score(agg["monetary"])
    agg["segment"] = [_segment(r.R, r.F, r.M, r.frequency, r.recency_days) for r in agg.itertuples()]

    # Churn risk: how overdue is the customer relative to their own rhythm (logistic curve)
    overdue = agg["recency_days"] / agg["avg_gap_days"]
    agg["churn_risk"] = (1 / (1 + np.exp(-1.6 * (overdue - 2.0)))).round(3)
    one_timers = agg["frequency"] == 1
    agg.loc[one_timers, "churn_risk"] = (1 / (1 + np.exp(-(agg.loc[one_timers, "recency_days"] - 60) / 15))).round(3)

    # Simple 12-month CLV: orders/yr * AOV * (1 - churn risk)
    orders_per_year = agg["frequency"] / (agg["tenure_days"] / 365).clip(lower=0.25)
    agg["clv_12m"] = (orders_per_year.clip(upper=24) * agg["aov"] * (1 - agg["churn_risk"])).round(0)

    out = custs.merge(agg.reset_index(), on="customer_id", how="left")
    out["frequency"] = out["frequency"].fillna(0).astype(int)
    out["monetary"] = out["monetary"].fillna(0.0)
    out["segment"] = out["segment"].fillna("Prospect")
    return out


def segment_summary(features: pd.DataFrame) -> list[dict]:
    if features.empty:
        return []
    g = (features.groupby("segment")
                 .agg(customers=("customer_id", "count"), revenue=("monetary", "sum"),
                      avg_churn_risk=("churn_risk", "mean"), avg_clv=("clv_12m", "mean"))
                 .reset_index().sort_values("revenue", ascending=False))
    g["revenue"] = g["revenue"].round(0)
    g["avg_churn_risk"] = g["avg_churn_risk"].round(3)
    g["avg_clv"] = g["avg_clv"].round(0)
    return g.to_dict("records")
