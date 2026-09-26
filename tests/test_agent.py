from app.models import AgentAction, Customer


def test_agent_run_proposes_grounded_actions(client, db):
    r = client.post("/api/agent/run", json={"max_actions": 40}).json()
    assert r["actions_proposed"] > 0 and r["opportunities_found"] >= r["actions_proposed"]
    actions = db.query(AgentAction).filter(AgentAction.run_id == r["run_id"]).all()
    # one action per customer per run
    assert len({a.customer_id for a in actions}) == len(actions)
    # every action cites evidence and respects policy
    for a in actions:
        assert a.citations, a.title
        if a.action_type in ("payment_retry", "vip_reward", "cross_sell", "second_order"):
            assert a.discount_pct == 0
        assert a.discount_pct <= 15


def test_no_marketing_to_non_consenting(client, db):
    no_consent = {c.customer_id for c in db.query(Customer).filter(Customer.marketing_opt_in.is_(False))}
    for a in db.query(AgentAction).all():
        if a.customer_id in no_consent:
            assert a.action_type == "payment_retry"  # transactional only


def test_execute_creates_payment_link_and_message(client):
    acts = client.get("/api/agent/actions", params={"status": "proposed"}).json()
    target = next(a for a in acts if a["type"] in ("payment_retry", "cart_recovery"))
    done = client.post(f"/api/agent/actions/{target['id']}/execute").json()
    assert done["status"] == "executed"
    assert done["result"]["payment_link"]["short_url"].startswith("https://rzp.io/")
    assert "{payment_link}" not in done["message"]


def test_reject_then_cannot_execute(client):
    acts = client.get("/api/agent/actions", params={"status": "proposed"}).json()
    aid = acts[-1]["id"]
    assert client.post(f"/api/agent/actions/{aid}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/agent/actions/{aid}/execute").status_code == 409


def test_ask_is_grounded(client):
    r = client.post("/api/agent/ask", json={"question": "What should I do about failed UPI payments?"}).json()
    assert r["evidence_sufficient"] and r["citations"]
    off = client.post("/api/agent/ask", json={"question": "Who won the cricket world cup?"}).json()
    assert not off["evidence_sufficient"]


def test_simulate_reports_no_response_and_outcomes(client):
    client.post("/api/demo/reset")
    client.post("/api/agent/run", json={"max_actions": 5})
    acts = client.get("/api/agent/actions?status=proposed").json()
    client.post(f"/api/agent/actions/{acts[0]['id']}/execute")
    r = client.post("/api/demo/simulate-outcomes?seed=1").json()
    assert r["checked"] == 1 and r["conversions"] + r["no_response"] == 1
    imp = client.get("/api/agent/impact").json()
    assert imp["awaiting"] == 0 and imp["conversions"] + imp["no_response"] == 1
    sent = client.get("/api/agent/actions?status=executed").json()
    assert sent[0]["outcome"] is not None
    assert client.post("/api/demo/simulate-outcomes").json()["checked"] == 0
    client.post("/api/demo/reset")
