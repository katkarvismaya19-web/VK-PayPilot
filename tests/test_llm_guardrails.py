"""The LLM can propose anything; guardrails decide what reaches customers."""
from app.agent import planner
from app.agent.orchestrator import run_agent
from app.models import AgentAction


class GreedyLLM:
    """A misbehaving model: tries to give everyone 30% off and cites evidence that doesn't exist."""
    available = True
    name = "mock-llm"

    def complete_json(self, system, user, max_tokens=3000):
        import json
        import re
        cases = json.loads(re.search(r"CASES:\n(.*)\n\nReturn", user, re.S).group(1))
        return [{"case": c["case"], "act": True, "title": "LLM action", "channel": "carrier-pigeon",
                 "discount_pct": 30, "message": "Hi {payment_link} {coupon}", "reasoning": "Because [99].",
                 "citations": [99, 1]} for c in cases]


def test_guardrails_override_llm(monkeypatch, db):
    monkeypatch.setattr(planner, "LLMClient", lambda: GreedyLLM())
    r = run_agent(db, max_actions=15)
    assert r["engine"] == "mock-llm"
    acts = db.query(AgentAction).filter(AgentAction.run_id == r["run_id"]).all()
    assert acts
    for a in acts:
        assert a.discount_pct <= 15                      # global/segment caps
        if a.action_type in ("payment_retry", "vip_reward", "cross_sell", "second_order"):
            assert a.discount_pct == 0                   # no discounts for these, whatever the model says
        assert a.channel in ("email", "whatsapp", "sms")  # invalid channel replaced
        assert all(c["id"] != 99 for c in a.citations)   # hallucinated citation dropped
        assert a.citations                               # still grounded in real evidence
