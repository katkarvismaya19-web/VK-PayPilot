"""
ETL: CSV -> star schema.

Generalized from SmartRetailAnalytics/etl/ETL.py. Accepts both the original
minimal SmartRetailAnalytics CSVs (customer_id, customer_name, city ...) and the
richer PayPilot CSVs; missing optional columns get sensible defaults.
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.models import Cart, Customer, Payment, Product, Sale


def _dt(v) -> datetime:
    if isinstance(v, datetime):
        return v
    return pd.to_datetime(v).to_pydatetime()


def _opt(row, key, default):
    v = row.get(key, default)
    return default if v is None or (isinstance(v, float) and pd.isna(v)) else v


def load_csv_dir(db: Session, data_dir: str | Path, reset: bool = True) -> dict:
    data_dir = Path(data_dir)
    if reset:
        for model in (Sale, Payment, Cart, Customer, Product):
            db.query(model).delete()
        db.commit()

    counts = {}

    products = pd.read_csv(data_dir / "products.csv")
    for r in products.to_dict("records"):
        db.add(Product(product_id=r["product_id"], product_name=r["product_name"], category=r["category"],
                       price=float(_opt(r, "price", 0)), margin_pct=float(_opt(r, "margin_pct", 30))))
    counts["products"] = len(products)

    customers = pd.read_csv(data_dir / "customers.csv")
    for r in customers.to_dict("records"):
        db.add(Customer(customer_id=r["customer_id"], customer_name=r["customer_name"], city=_opt(r, "city", ""),
                        email=_opt(r, "email", ""), phone=str(_opt(r, "phone", "")),
                        signup_date=_dt(_opt(r, "signup_date", datetime.utcnow())),
                        marketing_opt_in=str(_opt(r, "marketing_opt_in", True)).lower() in ("true", "1", "yes")))
    counts["customers"] = len(customers)
    db.flush()

    pkey = {p.product_id: p.product_key for p in db.query(Product).all()}
    ckey = {c.customer_id: c.customer_key for c in db.query(Customer).all()}

    sales = pd.read_csv(data_dir / "sales.csv")
    rows = []
    for r in sales.to_dict("records"):
        if r["product_id"] not in pkey or r["customer_id"] not in ckey:
            continue  # same integrity rule as the original ETL's key lookups
        rows.append(Sale(order_id=str(_opt(r, "order_id", f"T{r['transaction_id']}")),
                         product_key=pkey[r["product_id"]], customer_key=ckey[r["customer_id"]],
                         quantity=int(r["quantity"]), amount=float(r["amount"]),
                         transaction_date=_dt(r["transaction_date"]), payment_id=str(_opt(r, "payment_id", ""))))
    db.add_all(rows)
    counts["sales"] = len(rows)

    if (data_dir / "payments.csv").exists():
        pay = pd.read_csv(data_dir / "payments.csv")
        db.add_all([Payment(razorpay_payment_id=r["razorpay_payment_id"], razorpay_order_id=str(_opt(r, "razorpay_order_id", "")),
                            customer_id=r["customer_id"], amount=float(r["amount"]), status=r["status"],
                            method=_opt(r, "method", "upi"), error_reason=str(_opt(r, "error_reason", "")),
                            created_at=_dt(r["created_at"])) for r in pay.to_dict("records")])
        counts["payments"] = len(pay)

    if (data_dir / "carts.csv").exists():
        carts = pd.read_csv(data_dir / "carts.csv")
        db.add_all([Cart(cart_id=r["cart_id"], customer_id=r["customer_id"], status=r["status"], stage=r["stage"],
                         value=float(r["value"]), items=json.loads(r["items"]), updated_at=_dt(r["updated_at"]))
                    for r in carts.to_dict("records")])
        counts["carts"] = len(carts)

    db.commit()
    return counts
