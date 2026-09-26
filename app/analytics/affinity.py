"""Product affinity (market-basket lift) and next-best-product recommendations."""
from collections import Counter
from itertools import combinations

import pandas as pd
from sqlalchemy.orm import Session

from app.analytics.frames import sales_frame


def product_pairs(db: Session, min_support: int = 3, top: int = 15) -> list[dict]:
    sales = sales_frame(db)
    if sales.empty:
        return []
    baskets = sales.groupby("order_id")["product_id"].apply(lambda s: sorted(set(s)))
    n = len(baskets)
    single = Counter(p for b in baskets for p in b)
    pairs = Counter(pair for b in baskets if len(b) > 1 for pair in combinations(b, 2))
    names = sales.drop_duplicates("product_id").set_index("product_id")["product_name"].to_dict()
    out = []
    for (a, b), c in pairs.items():
        if c < min_support:
            continue
        lift = (c / n) / ((single[a] / n) * (single[b] / n))
        out.append({"a": names[a], "b": names[b], "a_id": a, "b_id": b, "together": c, "lift": round(lift, 2)})
    return sorted(out, key=lambda x: (x["lift"], x["together"]), reverse=True)[:top]


def recommend_for_customer(db: Session, customer_id: str, k: int = 3, sales: pd.DataFrame | None = None) -> list[dict]:
    """Popular products in the customer's favourite categories that they haven't bought yet."""
    sales = sales_frame(db) if sales is None else sales
    mine = sales[sales.customer_id == customer_id]
    if mine.empty:
        top = sales.groupby(["product_id", "product_name"]).quantity.sum().nlargest(k).reset_index()
        return [{"product_id": r.product_id, "name": r.product_name, "reason": "bestseller"} for r in top.itertuples()]
    fav_cats = mine.groupby("category").amount.sum().nlargest(2).index.tolist()
    owned = set(mine.product_id)
    pool = sales[sales.category.isin(fav_cats) & ~sales.product_id.isin(owned)]
    top = pool.groupby(["product_id", "product_name", "category"]).customer_id.nunique().nlargest(k).reset_index()
    return [{"product_id": r.product_id, "name": r.product_name, "reason": f"popular in {r.category}"}
            for r in top.itertuples()]
