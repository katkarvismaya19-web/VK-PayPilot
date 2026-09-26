"""
Data model.

dim_customer / dim_product / fact_sales extend the star schema from
SmartRetailAnalytics (sql/warehouse_schema.sql). PayPilot adds the operational
tables an agent needs: carts, payments (Razorpay mirror), the action log and outcomes.
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# ---------- Star schema (from SmartRetailAnalytics) ----------
class Customer(Base):
    __tablename__ = "dim_customer"
    customer_key: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(80), default="")
    email: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    signup_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, default=True)

    sales = relationship("Sale", back_populates="customer")


class Product(Base):
    __tablename__ = "dim_product"
    product_key: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    product_name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80))
    price: Mapped[float] = mapped_column(Float, default=0.0)
    margin_pct: Mapped[float] = mapped_column(Float, default=30.0)


class Sale(Base):
    __tablename__ = "fact_sales"
    transaction_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(40), index=True, default="")
    product_key: Mapped[int] = mapped_column(ForeignKey("dim_product.product_key"))
    customer_key: Mapped[int] = mapped_column(ForeignKey("dim_customer.customer_key"))
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)
    transaction_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    payment_id: Mapped[str] = mapped_column(String(40), default="")

    customer = relationship("Customer", back_populates="sales")
    product = relationship("Product")


# ---------- Operational tables (PayPilot) ----------
class Payment(Base):
    """Mirror of Razorpay payment events (from webhooks or sync)."""
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    razorpay_payment_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    razorpay_order_id: Mapped[str] = mapped_column(String(40), default="")
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    amount: Mapped[float] = mapped_column(Float)  # rupees
    status: Mapped[str] = mapped_column(String(20))  # captured | failed | refunded
    method: Mapped[str] = mapped_column(String(20), default="upi")
    error_reason: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Cart(Base):
    __tablename__ = "carts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cart_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | abandoned | recovered | converted
    stage: Mapped[str] = mapped_column(String(20), default="cart")  # cart | checkout | payment_failed
    value: Mapped[float] = mapped_column(Float, default=0.0)
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{product_id, name, qty, price}]
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    recovery_link: Mapped[str] = mapped_column(String(255), default="")


class AgentAction(Base):
    """Every recommendation the agent makes, with its reasoning and evidence."""
    __tablename__ = "agent_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    action_type: Mapped[str] = mapped_column(String(40))
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(String(200))
    reasoning: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, default="")
    channel: Mapped[str] = mapped_column(String(20), default="email")
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)
    expected_value: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    risk: Mapped[str] = mapped_column(String(10), default="low")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | executed | rejected | failed
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Outcome(Base):
    """Attribution: did an executed action lead to a purchase?"""
    __tablename__ = "outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("agent_actions.id"), index=True)
    converted: Mapped[bool] = mapped_column(Boolean, default=False)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------- customer support & responses

class SupportTicket(Base):
    """A request or concern raised by a customer, either from the portal or as a reply to a PayPilot message."""
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    customer_name: Mapped[str] = mapped_column(String(120), default="")
    subject: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(20), default="other")   # payment | order | refund | product | offer | account | other
    priority: Mapped[str] = mapped_column(String(10), default="normal")  # urgent | high | normal | low
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open | in_progress | resolved
    order_id: Mapped[str] = mapped_column(String(40), default="")
    related_action_id: Mapped[int | None] = mapped_column(ForeignKey("agent_actions.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="portal")  # portal | message_reply
    triage_reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    messages: Mapped[list["TicketMessage"]] = relationship(back_populates="ticket", order_by="TicketMessage.id",
                                                          cascade="all, delete-orphan")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    author: Mapped[str] = mapped_column(String(10))  # customer | owner | system
    body: Mapped[str] = mapped_column(Text)
    attachments: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"payment_link": {...}}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ticket: Mapped[SupportTicket] = relationship(back_populates="messages")


class CustomerResponse(Base):
    """How a customer answered a PayPilot message: paid, not interested, stop messages, or asked a question."""
    __tablename__ = "customer_responses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[int] = mapped_column(ForeignKey("agent_actions.id"), index=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    response: Mapped[str] = mapped_column(String(20))  # paid | not_interested | stop | question
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
