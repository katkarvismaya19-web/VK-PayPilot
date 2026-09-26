"""Load warehouse tables into pandas DataFrames (analysis layer from SmartRetailAnalytics)."""
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.orm import Session

from app.models import Cart, Customer, Payment, Product, Sale


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sales_frame(db: Session) -> pd.DataFrame:
    q = (db.query(Sale.order_id, Sale.quantity, Sale.amount, Sale.transaction_date,
                  Customer.customer_id, Customer.customer_name, Customer.city,
                  Product.product_id, Product.product_name, Product.category, Product.margin_pct)
         .join(Customer, Sale.customer_key == Customer.customer_key)
         .join(Product, Sale.product_key == Product.product_key))
    df = pd.DataFrame(q.all(), columns=["order_id", "quantity", "amount", "transaction_date", "customer_id",
                                        "customer_name", "city", "product_id", "product_name", "category", "margin_pct"])
    if not df.empty:
        df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    return df


def customers_frame(db: Session) -> pd.DataFrame:
    rows = db.query(Customer).all()
    return pd.DataFrame([{"customer_id": c.customer_id, "customer_name": c.customer_name, "city": c.city,
                          "email": c.email, "phone": c.phone, "signup_date": c.signup_date,
                          "marketing_opt_in": c.marketing_opt_in} for c in rows])


def carts_frame(db: Session) -> pd.DataFrame:
    rows = db.query(Cart).all()
    return pd.DataFrame([{"cart_id": c.cart_id, "customer_id": c.customer_id, "status": c.status, "stage": c.stage,
                          "value": c.value, "items": c.items, "updated_at": c.updated_at} for c in rows])


def payments_frame(db: Session) -> pd.DataFrame:
    rows = db.query(Payment).all()
    return pd.DataFrame([{"payment_id": p.razorpay_payment_id, "customer_id": p.customer_id, "amount": p.amount,
                          "status": p.status, "method": p.method, "error_reason": p.error_reason,
                          "created_at": p.created_at} for p in rows])
