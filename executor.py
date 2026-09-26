"""Execute an approved action: payment link (Razorpay), coupon, message delivery, quiet hours."""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.integrations.notifier import deliver
from app.integrations.razorpay_client import get_razorpay
from app.models import AgentAction, Cart

IST = timezone(timedelta(hours=5, minutes=30))
NEEDS_LINK = {"payment_retry", "cart_recovery"}


def _quiet_hours_release(transactional: bool) -> str | None:
    """Promotional messages wait until 9 AM IST if it's between 9 PM and 9 AM."""
    if transactional:
        return None
    t = datetime.now(IST)
    if 9 <= t.hour < 21:
        return None
    release = t.replace(hour=9, minute=0, second=0, microsecond=0)
    if t.hour >= 21:
        release += timedelta(days=1)
    return release.isoformat()


def execute_action(db: Session, action: AgentAction) -> AgentAction:
    if action.status == "executed":
        return action
    payload = action.payload or {}
    customer, facts = payload.get("customer", {}), payload.get("facts", {})
    result: dict = {}
    message = action.message

    try:
        if action.action_type in NEEDS_LINK:
            amount = payload.get("value", 0) * (1 - action.discount_pct / 100)
            link = get_razorpay().create_payment_link(
                amount, customer, description=action.title, reference_id=f"pp{action.id}_{secrets.token_hex(3)}",
                notes={"paypilot_action_id": action.id, "cart_id": facts.get("cart_id") or "", "type": action.action_type})
            result["payment_link"] = link
            message = message.replace("{payment_link}", link["short_url"])
            if facts.get("cart_id"):
                cart = db.query(Cart).filter(Cart.cart_id == facts["cart_id"]).first()
                if cart:
                    cart.recovery_link = link["short_url"]
        if action.discount_pct:
            code = f"PP{action.discount_pct}-{secrets.token_hex(3).upper()}"
            result["coupon"] = {"code": code, "percent": action.discount_pct, "single_use": True,
                                "customer_id": action.customer_id,
                                "expires": (datetime.now(IST) + timedelta(days=7)).date().isoformat()}
            message = message.replace("{coupon}", code)
        message = message.replace("{payment_link}", "").replace("{coupon}", "")

        if (release := _quiet_hours_release(payload.get("transactional", False))):
            result["scheduled_for"] = release
        to = customer.get("phone") if action.channel in ("whatsapp", "sms") else customer.get("email")
        result["delivery"] = deliver(action.channel, to or "", action.title, message,
                                     {"action_id": action.id, "scheduled_for": result.get("scheduled_for")})
        action.message = message
        action.status = "executed"
    except Exception as e:  # keep the loop alive, surface the error
        result["error"] = str(e)
        action.status = "failed"

    action.result = result
    action.executed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return action
