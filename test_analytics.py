def test_kpis_shape(client):
    k = client.get("/api/analytics/kpis").json()
    for key in ("revenue_30d", "orders_30d", "aov_30d", "repeat_rate_pct", "abandoned_value", "payment_success_rate_pct"):
        assert key in k
    assert k["revenue_30d"] > 0


def test_segments_cover_all_customers(client):
    segs = client.get("/api/analytics/segments").json()
    names = {s["segment"] for s in segs}
    assert {"Champions", "At Risk"} & names
    assert sum(s["customers"] for s in segs) == client.get("/api/analytics/kpis").json()["total_customers"]


def test_customer_profile_has_recommendations(client):
    cid = client.get("/api/analytics/customers?limit=1").json()[0]["customer_id"]
    p = client.get(f"/api/analytics/customers/{cid}").json()
    assert p["profile"]["customer_id"] == cid
    assert len(p["recommendations"]) > 0


def test_affinity_lift_positive(client):
    pairs = client.get("/api/analytics/affinity").json()
    assert all(p["lift"] > 0 for p in pairs)
