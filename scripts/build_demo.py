"""
Build a standalone demo of the dashboard (dist/paypilot-demo.html) that runs
without a backend. It executes the real pipeline on the sample data and
embeds the results, so the demo shows genuine PayPilot output.

    python scripts/build_demo.py
"""
import json
from datetime import datetime
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/demo.db"
os.environ.setdefault("LLM_PROVIDER", "none")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.rag.embeddings import STOP  # noqa: E402
from app.rag.knowledge_base import get_kb  # noqa: E402
from app.support import triage  # noqa: E402
from app.support.service import MESSAGE_LABELS  # noqa: E402

QUESTIONS = ["How is the store doing this month?", "What should I do about failed UPI payments?",
             "Which customers are at risk of churning?", "Can I give a discount to my best customers?",
             "Which products sell well together?"]

with TestClient(app) as c:
    run = c.post("/api/agent/run", json={"max_actions": 25}).json()
    actions = c.get("/api/agent/actions?status=proposed").json()
    tickets = [c.get(f"/api/support/tickets/{t['id']}").json() for t in c.get("/api/support/tickets?status=").json()]
    everyone = c.get("/api/analytics/customers?limit=1000").json()
    wanted = {a["customer_id"] for a in actions} | {t["customer_id"] for t in tickets}
    customers = everyone[:120] + [x for x in everyone[120:] if x["customer_id"] in wanted]
    portal_ids = list(dict.fromkeys([a["customer_id"] for a in actions] + [t["customer_id"] for t in tickets]))
    portal = {}
    for cid in portal_ids:
        v = c.get(f"/api/portal/{cid}").json()
        portal[cid] = {"customer": v["customer"], "orders": v["orders"]}
    samples = {"payment": "money debited but order failed", "refund": "where is my refund", "order": "order not delivered yet",
               "offer": "coupon code not working", "product": "what size and material", "account": "please stop sending me messages",
               "other": "I have a question"}
    suggest = {cat: triage.suggest_reply({"customer_name": "{first}", "subject": text, "category": cat,
                                           "messages": [{"author": "customer", "body": text}]}) for cat, text in samples.items()}
    for cust in customers:
        cust["recs"] = c.get(f"/api/analytics/customers/{cust['customer_id']}").json()["recommendations"]
    kb = get_kb()
    data = {
        "status": {**c.get("/api/agent/status").json(), "engine": "rule-based planner (demo snapshot)"},
        "kpis": c.get("/api/analytics/kpis").json(), "trend": c.get("/api/analytics/trend").json(),
        "segments": c.get("/api/analytics/segments").json(),
        "failures": c.get("/api/analytics/payment-failures").json(),
        "customers": customers,
        "run": {k: v for k, v in run.items() if k not in ("skip_log", "action_ids")},
        "actions": actions,
        "answers": [{"q": q, "r": c.post("/api/agent/ask", json={"question": q}).json()} for q in QUESTIONS],
        "kb": {**kb.stats(), "chunks": [{"title": ch.title, "section": ch.section, "text": ch.text} for ch in kb.chunks]},
        "stop": sorted(STOP),
        "built_at": datetime.utcnow().isoformat(),
        "portal": portal,
        "support": {
            "tickets": [{k: v for k, v in t.items() if k not in ("customer", "suggested_reply")} for t in tickets],
            "ctx": {t["customer_id"]: t["customer"] for t in tickets},
            "suggest": suggest,
            "labels": MESSAGE_LABELS,
            "rules": {"categories": triage.CATEGORIES, "labels": triage.CATEGORY_LABELS, "urgent": triage.URGENT,
                      "upset": triage.UPSET, "valuable": triage.VALUABLE, "opt_out": triage.OPT_OUT},
        },
    }

html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
html = html.replace("/*__DEMO_DATA__*/null", json.dumps(data, default=str).replace("</", "<\\/"))
out = ROOT / "dist" / "paypilot-demo.html"
out.parent.mkdir(exist_ok=True)
out.write_text(html, encoding="utf-8")
pages = ROOT / "docs" / "index.html"  # GitHub Pages serves the live demo from /docs
pages.parent.mkdir(exist_ok=True)
pages.write_text(html, encoding="utf-8")
print(f"Wrote {out} ({out.stat().st_size // 1024} KB) with {len(actions)} actions")
