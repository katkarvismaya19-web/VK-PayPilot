"""
Perception: turn analytics into scored growth opportunities.

Each opportunity has an estimated conversion probability and expected value,
so the agent can prioritise by money at stake rather than by rule order.
"""
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy.orm import Session

from app.analytics.affinity import recommend_for_customer
from app.analytics.customers import customer_features
from app.analytics.frames import carts_frame, now, payments_frame, sales_frame

RETRY_PROB = {"upi_pin_incorrect": 0.55, "upi_app_declined": 0.5, "payment_timeout": 0.5, "bank_server_down": 0.45,
              "insufficient_funds": 0.2, "bank_declined": 0.3, "authentication_failed": 0.35, "card_expired": 0.3}
WINBACK = {"Can't Lose Them": 0.15, "At Risk": 0.12, "Hibernating": 0.06}


@dataclass
class Opportunity:
    kind: str                 # cart_recovery | payment_retry | winback_offer | second_order | vip_reward | cross_sell
    customer_id: str
    customer: dict
    value: float              # money at stake (cart value / expected order)
    probability: float
    evidence_query: str
    facts: dict = field(default_factory=dict)
    transactional: bool = False

    @property
    def expected_value(self) -> float:
        return round(self.value * self.probability, 0)


def detect_opportunities(db: Session) -> tuple[list[Opportunity], dict]:
    feats = customer_features(db)
    if feats.empty:
        return [], {}
    sales = sales_frame(db)
    carts, pays = carts_frame(db), payments_frame(db)
    t = pd.Timestamp(now())
    store_aov = float(feats["aov"].median())
    idx = feats.set_index("customer_id")

    def cust(cid):
        r = idx.loc[cid]
        return {"id": cid, "name": r.customer_name, "first_name": r.customer_name.split()[0], "email": r.email,
                "phone": r.phone, "city": r.city, "segment": r.segment, "opt_in": bool(r.marketing_opt_in),
                "orders": int(r.frequency), "aov": float(r.aov) if pd.notna(r.aov) else 0.0,
                "favorite_category": r.favorite_category if pd.notna(r.favorite_category) else None,
                "recency_days": int(r.recency_days) if pd.notna(r.recency_days) else None,
                "churn_risk": float(r.churn_risk) if pd.notna(r.churn_risk) else None}

    opps: list[Opportunity] = []
    failed_customers = set()

    # 1. Failed payments (last 7 days) -> retry link, transactional
    if not pays.empty:
        recent_fail = pays[(pays.status == "failed") & (pays.created_at > t - pd.Timedelta(days=7))]
        for p in recent_fail.itertuples():
            if p.customer_id not in idx.index:
                continue
            failed_customers.add(p.customer_id)
            cart = carts[(carts.customer_id == p.customer_id) & (carts.stage == "payment_failed")]
            items = cart.iloc[0]["items"] if len(cart) else []
            opps.append(Opportunity(
                "payment_retry", p.customer_id, cust(p.customer_id), float(p.amount),
                RETRY_PROB.get(p.error_reason, 0.3),
                f"payment failed {p.error_reason.replace('_', ' ')} {p.method} retry recovery",
                {"failure_reason": p.error_reason, "method": p.method, "payment_id": p.payment_id,
                 "cart_id": cart.iloc[0]["cart_id"] if len(cart) else None, "items": items,
                 "hours_ago": round((t - p.created_at).total_seconds() / 3600, 1)},
                transactional=True))

    # 2. Abandoned carts (2h - 14 days)
    if not carts.empty:
        ab = carts[(carts.status == "abandoned") & (carts.stage != "payment_failed")]
        for c in ab.itertuples():
            age_h = (t - c.updated_at).total_seconds() / 3600
            if age_h < 2 or age_h > 14 * 24 or c.customer_id not in idx.index:
                continue
            base = 0.24 if c.stage == "checkout" else 0.13
            prob = base * (1 - min(age_h / (14 * 24), 1) * 0.6)
            opps.append(Opportunity(
                "cart_recovery", c.customer_id, cust(c.customer_id), float(c.value), round(prob, 3),
                f"abandoned cart {c.stage} stage recovery reminder payment link discount",
                {"cart_id": c.cart_id, "stage": c.stage, "items": c.items, "hours_since": round(age_h, 1),
                 "store_aov": round(store_aov), "high_value": c.value > 3 * store_aov}))

    # 3. Segment-driven retention plays
    for r in feats.itertuples():
        if r.customer_id in failed_customers or pd.isna(r.segment):
            continue
        c = None
        recs = None
        if r.segment in WINBACK:
            c = cust(r.customer_id)
            recs = recommend_for_customer(db, r.customer_id, 3, sales)
            opps.append(Opportunity("winback_offer", r.customer_id, c, float(r.aov), WINBACK[r.segment],
                                    f"win-back {r.segment} customer churn risk offer discount",
                                    {"days_since_order": int(r.recency_days), "usual_gap_days": int(r.avg_gap_days),
                                     "lifetime_spend": round(float(r.monetary)), "recommendations": recs}))
        elif r.segment == "New" and 5 <= r.recency_days <= 30:
            c = cust(r.customer_id)
            recs = recommend_for_customer(db, r.customer_id, 3, sales)
            opps.append(Opportunity("second_order", r.customer_id, c, float(r.aov), 0.18,
                                    "new customer second purchase follow-up pairing product",
                                    {"days_since_first_order": int(r.recency_days), "recommendations": recs}))
        elif r.segment == "Champions" and r.churn_risk >= 0.3:
            c = cust(r.customer_id)
            opps.append(Opportunity("vip_reward", r.customer_id, c, float(r.aov), 0.25,
                                    "champions reward no discount early access loyalty",
                                    {"lifetime_spend": round(float(r.monetary)), "days_since_order": int(r.recency_days),
                                     "usual_gap_days": int(r.avg_gap_days)}))
        elif r.segment in ("Loyal", "Promising", "Potential Loyalist") and r.recency_days >= r.avg_gap_days * 0.8:
            c = cust(r.customer_id)
            recs = recommend_for_customer(db, r.customer_id, 3, sales)
            opps.append(Opportunity("cross_sell", r.customer_id, c, float(r.aov), 0.12,
                                    "next best product recommendation cross-sell loyal customers",
                                    {"days_since_order": int(r.recency_days), "recommendations": recs}))

    context = {"store_aov": round(store_aov), "customers_analyzed": int(len(feats))}
    return opps, context
