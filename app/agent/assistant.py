"""
Ask PayPilot: answers merchant questions using live store analytics + policy
evidence from the knowledge base. With an LLM it writes a grounded answer; without
one it routes by intent and composes an answer from the same data.
"""
import json
import re

from app.rag.embeddings import tokenize

from sqlalchemy.orm import Session

from app.agent.llm import LLMClient, LLMError
from app.analytics.affinity import product_pairs
from app.analytics.customers import customer_features, segment_summary
from app.analytics.metrics import category_performance, kpis, payment_failures
from app.rag.knowledge_base import get_kb


def _inr(v):
    return f"₹{v:,.0f}"


def store_snapshot(db: Session) -> dict:
    feats = customer_features(db)
    return {"kpis": kpis(db), "segments": segment_summary(feats), "categories": category_performance(db),
            "payment_failures": payment_failures(db)[:6], "product_pairs": product_pairs(db, top=5)}


def best_sentence(question: str, text: str) -> str:
    """Pick the evidence sentence that overlaps most with the question (extractive answer)."""
    q = set(tokenize(question))
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", text) if len(x.strip()) > 15]
    return max(sents, key=lambda x: len(q & set(tokenize(x))), default=text).rstrip(".")


POLICY_WORDS = ("can i", "should i", "should we", "can we", "allowed", "policy", "rule", "ok to", "okay to",
                "how do i", "how should", "what should", "is it", "limit", "max")


def _fallback(q: str, snap: dict, ev) -> tuple[str, list[int]]:
    """Intent-routed answer from store data + extractive policy evidence. Returns (answer, citation ids used)."""
    k, ql = snap["kpis"], q.lower()
    has = lambda *ws: any(w in ql for w in ws)
    policy_q = has(*POLICY_WORDS)
    parts, used = [], []
    if has("revenue", "sales", "doing", "performance", "overview", "summary", "kpi", "this month"):
        ch = k["revenue_change_pct"]
        parts.append(f"Revenue over the last 30 days is {_inr(k['revenue_30d'])} "
                     f"({'+' if ch and ch > 0 else ''}{ch}% vs the previous 30 days) from {k['orders_30d']} orders, "
                     f"with an average order value of {_inr(k['aov_30d'])}. Repeat purchase rate is {k['repeat_rate_pct']}%. "
                     f"{k['abandoned_carts']} carts worth {_inr(k['abandoned_value'])} are abandoned, and "
                     f"{_inr(k['failed_payment_value_30d'])} of payments failed this month.")
    elif has("cart", "abandon", "checkout"):
        parts.append(f"There are {k['abandoned_carts']} abandoned carts worth {_inr(k['abandoned_value'])} in total.")
    if has("payment", "fail", "razorpay", "upi", "success rate"):
        top = snap["payment_failures"][:2]
        detail = "; ".join(f"{t['error_reason'].replace('_', ' ')} on {t['method']} ({t['count']}x, {_inr(t['value'])})" for t in top)
        parts.append(f"Payment success rate is {k['payment_success_rate_pct']}% over 30 days, with "
                     f"{_inr(k['failed_payment_value_30d'])} in failed payments. Biggest causes: {detail}.")
    if has("churn", "segment", "at risk", "retention", "lapsing", "which customers", "who should"):
        segs = {s["segment"]: s for s in snap["segments"]}
        risky = [segs[n] for n in ("Can't Lose Them", "At Risk", "Hibernating") if n in segs]
        if risky:
            parts.append("Customers most at risk: " + ", ".join(f"{s['customers']} {s['segment']}" for s in risky)
                         + f". Together they account for {_inr(sum(s['revenue'] for s in risky))} of historical revenue. "
                         "Can't Lose Them and At Risk customers are the best win-back targets; see the Customers tab for names.")
    if has("product", "category", "bundle", "together", "upsell", "cross"):
        c = snap["categories"][0]
        pairs = snap["product_pairs"][:3]
        parts.append(f"{c['category']} is the top category ({_inr(c['revenue'])} revenue). "
                     + ("Strongest pairings: " + "; ".join(f"{p['a']} + {p['b']} (bought together {p['together']}x, lift {p['lift']})" for p in pairs) + "." if pairs else ""))
    if ev.sufficient and (policy_q or not parts or has("discount", "offer", "message", "night", "consent")):
        e = ev.items[0]
        parts.insert(0 if policy_q else len(parts), f"Per your {e.title.lower()} ({e.section.lower()}): {best_sentence(q, e.text)}. [{e.citation_id}]")
        used.append(e.citation_id)
        if policy_q and len(ev.items) > 1 and ev.items[1].score >= 0.9 * e.score and ev.items[1].title != e.title:
            e2 = ev.items[1]
            parts.insert(1, f"Also, from your {e2.title.lower()}: {best_sentence(q, e2.text)}. [{e2.citation_id}]")
            used.append(e2.citation_id)
    if not parts:
        return ("I couldn't find store data or policy evidence that answers that. Try asking about revenue, abandoned "
                "carts, failed payments, customer segments, products or your discount and messaging policy."), []
    return " ".join(parts), used


def ask(db: Session, question: str) -> dict:
    kb = get_kb()
    ev = kb.evidence(question, k=4)
    snap = store_snapshot(db)
    llm = LLMClient()
    engine = "rule-based (no LLM key configured)"
    used = None
    if llm.available:
        try:
            answer = llm.complete(
                "You are PayPilot, a growth analyst for an Indian D2C store. Answer the merchant's question using the "
                "STORE DATA and POLICY EVIDENCE. Cite policy evidence like [2]. Use ₹. Be specific and concise "
                "(under 150 words). If neither source answers the question, say so instead of guessing.",
                f"STORE DATA:\n{json.dumps(snap, default=str)}\n\nPOLICY EVIDENCE:\n{ev.as_prompt() or '(none)'}"
                f"\n\nQUESTION: {question}", max_tokens=700)
            engine = llm.name
        except LLMError:
            answer, used = _fallback(question, snap, ev)
            engine = "rule-based (LLM unavailable)"
    else:
        answer, used = _fallback(question, snap, ev)
    cites = ev.citations() if ev.sufficient else []
    if used is not None:
        cites = [c for c in cites if c["id"] in used]
    return {"answer": answer, "citations": cites,
            "evidence_sufficient": ev.sufficient, "engine": engine}
