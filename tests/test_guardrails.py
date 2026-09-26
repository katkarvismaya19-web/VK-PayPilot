from app.agent.guardrails import clamp_discount, risk_level


def test_champions_never_discounted():
    assert clamp_discount("winback_offer", "Champions", 20)[0] == 0
    assert clamp_discount("vip_reward", "Champions", 15)[0] == 0


def test_at_risk_capped_at_15():
    pct, notes = clamp_discount("winback_offer", "At Risk", 30)
    assert pct == 15 and notes


def test_failed_payments_get_no_discount():
    assert clamp_discount("payment_retry", "Loyal", 10)[0] == 0


def test_margin_floor_applies():
    assert clamp_discount("cart_recovery", "Loyal", 10, margin_floor_cap=5)[0] == 5


def test_risk_levels():
    assert risk_level(0, 500, 1000) == "low"
    assert risk_level(10, 500, 1000) == "medium"
    assert risk_level(0, 6000, 1000, involves_payment=True) == "high"
