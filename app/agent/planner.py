"""
Decision layer: for each prioritised opportunity, retrieve policy evidence
(RAG) and decide channel, discount and message, either with an LLM or with the
built-in rule planner. Output is always grounded in cited evidence.
"""
import json

from app.agent.llm import LLMClient, LLMError
from app.rag.knowledge_base import EvidenceContext

TITLES = {
    "payment_retry": "Retry failed payment",
    "cart_recovery": "Recover abandoned cart",
    "winback_offer": "Win back lapsing customer",
    "second_order": "Nudge toward second order",
    "vip_reward": "Thank a top customer",
    "cross_sell": "Recommend next product",
}
FAILURE_TIP = {
    "upi_pin_incorrect": "If the UPI PIN step was the problem, you can also try a different UPI app.",
    "upi_app_declined": "If your UPI app declined it, another UPI app usually works.",
    "payment_timeout": "It looks like the bank took too long to respond. That's usually temporary.",
    "bank_server_down": "Your bank's server had a hiccup. It should go through now.",
    "insufficient_funds": "No rush, the link stays open for 3 days.",
    "bank_declined": "If your card was declined, UPI or netbanking is often quicker.",
    "authentication_failed": "If the OTP step failed, UPI or netbanking avoids it.",
    "card_expired": "Your card seems to have expired, so UPI or netbanking will be easiest.",
}
CONSUMABLE = {"Grocery", "Beauty"}


def _ago(hours: float) -> str:
    return f"{hours:.0f} hours ago" if hours < 48 else f"{hours / 24:.0f} days ago"


def _inr(v: float) -> str:
    return f"₹{v:,.0f}"


def _items_text(items: list[dict]) -> str:
    names = list(dict.fromkeys(i["name"] for i in items))[:3]
    return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1] if names else "your items"


def _recs_text(recs: list[dict] | None) -> str:
    return ", ".join(r["name"] for r in (recs or [])[:3]) or "our new arrivals"


def rule_decision(opp, evidence: EvidenceContext, store_aov: float) -> dict:
    c, f, k = opp.customer, opp.facts, opp.kind
    cite = [e.citation_id for e in evidence.items[:2]]
    name = c["first_name"]
    d = {"channel": "email", "discount_pct": 0, "citations": cite}

    if k == "payment_retry":
        d["channel"] = "whatsapp" if c.get("phone") else "email"
        d["title"] = f"{TITLES[k]} of {_inr(opp.value)} ({f['failure_reason'].replace('_', ' ')})"
        d["message"] = (f"Hi {name}, your payment of {_inr(opp.value)} didn't go through, but your order is saved. "
                        f"{FAILURE_TIP.get(f['failure_reason'], '')} Complete it here: {{payment_link}}")
        d["reasoning"] = (f"Payment failed {_ago(f['hours_ago'])} via {f['method']} ({f['failure_reason']}). The customer "
                          f"already chose to buy, so the playbook says fix the payment path with a fresh Razorpay link "
                          f"and no discount [{cite[0] if cite else 1}].")
    elif k == "cart_recovery":
        second = f["hours_since"] >= 24 and opp.value > store_aov
        d["discount_pct"] = 10 if second else 0
        d["channel"] = "whatsapp" if f.get("high_value") else "email"
        items = _items_text(f["items"])
        offer = f" Here's {d['discount_pct']}% off with code {{coupon}}." if d["discount_pct"] else ""
        d["title"] = f"{TITLES[k]} worth {_inr(opp.value)} ({f['stage']} stage)"
        d["message"] = (f"Hi {name}, you left {items} in your cart. It's still saved for you.{offer} "
                        f"Pay in one tap: {{payment_link}}")
        why = ("It's over 24h old and above the store AOV, so a small incentive is allowed for this second reminder"
               if second else "A plain reminder with a payment link comes first; no discount is needed yet")
        d["reasoning"] = (f"Cart of {_inr(opp.value)} abandoned at the {f['stage']} stage {_ago(f['hours_since'])}. "
                          f"{why} [{cite[0] if cite else 1}].")
    elif k == "winback_offer":
        d["discount_pct"] = 15 if c["segment"] == "Can't Lose Them" else 10
        d["title"] = f"{TITLES[k]} ({c['segment']}, {_inr(f['lifetime_spend'])} lifetime)"
        d["message"] = (f"Hi {name}, it's been a while! We've added new pieces in {c['favorite_category']} we think "
                        f"you'll like: {_recs_text(f.get('recommendations'))}. Here's {d['discount_pct']}% off your next "
                        f"order with code {{coupon}}, valid for 7 days.")
        d["reasoning"] = (f"{c['segment']} customer: last order {f['days_since_order']} days ago against a usual gap of "
                          f"{f['usual_gap_days']} days, {_inr(f['lifetime_spend'])} lifetime spend. Policy allows up to "
                          f"{d['discount_pct']}% for this segment [{cite[0] if cite else 1}].")
    elif k == "second_order":
        d["title"] = f"{TITLES[k]} ({f['days_since_first_order']} days after first purchase)"
        d["message"] = (f"Hi {name}, hope you're enjoying your order! Customers who bought what you did also love "
                        f"{_recs_text(f.get('recommendations'))}. Take a look when you have a minute.")
        d["reasoning"] = (f"New customer, first order {f['days_since_first_order']} days ago. The goal is the second "
                          f"purchase through a relevant pairing, not a discount [{cite[0] if cite else 1}].")
    elif k == "vip_reward":
        d["title"] = f"{TITLES[k]} ({_inr(f['lifetime_spend'])} lifetime)"
        d["message"] = (f"Hi {name}, thank you for being one of our favourite customers. You get early access to our "
                        f"new {c['favorite_category']} drop before anyone else. We'll send it your way first.")
        d["reasoning"] = (f"Champion whose order gap is stretching ({f['days_since_order']} days vs usual "
                          f"{f['usual_gap_days']}). Champions get recognition, not discounts [{cite[0] if cite else 1}].")
    else:  # cross_sell
        fav = c.get("favorite_category")
        repl = fav in CONSUMABLE
        d["title"] = f"{TITLES[k]} ({fav} fan" + (", replenishment)" if repl else ")")
        d["message"] = (f"Hi {name}, " + (f"running low on your {fav.lower()} favourites? " if repl else "")
                        + f"we picked these for you: {_recs_text(f.get('recommendations'))}. "
                        + "Orders over ₹999 ship free.")
        d["reasoning"] = (f"{c['segment']} customer is near their usual reorder point ({f['days_since_order']} days). "
                          f"Recommend popular {fav} products they haven't bought [{cite[0] if cite else 1}].")
    return d


SYSTEM = """You are PayPilot, a growth agent for an Indian D2C e-commerce store that uses Razorpay.
For each opportunity you decide whether to act, the channel, discount and a short personalised message.
Ground every decision in the numbered policy evidence and cite it by id. Follow the evidence strictly:
never exceed the discount limits it states, never discount failed payments, never discount Champions or Loyal.
Messages: under 60 words, first name, name real products, one call to action, no fake urgency.
Use {payment_link} where a payment link belongs and {coupon} where a coupon code belongs."""


def llm_decisions(llm: LLMClient, batch: list[tuple], store_aov: float) -> dict[int, dict]:
    """batch: list of (index, opportunity, EvidenceContext). Returns {index: decision}."""
    evidence_map, cases = {}, []
    for i, opp, ev in batch:
        ids = []
        for e in ev.items:
            key = (e.title, e.section)
            if key not in evidence_map:
                evidence_map[key] = {"id": len(evidence_map) + 1, "source": f"{e.title} / {e.section}", "text": e.text}
            ids.append(evidence_map[key]["id"])
        cases.append({"case": i, "type": opp.kind, "customer": {k: opp.customer[k] for k in
                      ("first_name", "segment", "orders", "favorite_category", "opt_in")},
                      "money_at_stake": round(opp.value), "facts": opp.facts, "relevant_evidence_ids": ids})
    prompt = (f"Store average order value: ₹{store_aov:,.0f}\n\nEVIDENCE:\n"
              + "\n".join(f"[{v['id']}] {v['source']}: {v['text']}" for v in evidence_map.values())
              + "\n\nCASES:\n" + json.dumps(cases, default=str)
              + '\n\nReturn a JSON array with one object per case: {"case": int, "act": bool, "title": str, '
                '"channel": "email"|"whatsapp"|"sms", "discount_pct": int, "message": str, '
                '"reasoning": str (2 sentences, cite like [2]), "citations": [int]}')
    raw = llm.complete_json(SYSTEM, prompt, max_tokens=4000)
    valid_ids = {v["id"] for v in evidence_map.values()}
    # map global evidence ids back to each case's local citation ids
    out = {}
    for d in raw if isinstance(raw, list) else []:
        i = d.get("case")
        match = next((b for b in batch if b[0] == i), None)
        if match is None:
            continue
        _, _, ev = match
        local = {evidence_map[(e.title, e.section)]["id"]: e.citation_id for e in ev.items}
        d["citations"] = [local[c] for c in d.get("citations", []) if c in valid_ids and c in local]
        out[i] = d
    return out


def decide(opps_with_evidence: list[tuple], store_aov: float) -> tuple[dict[int, dict], str]:
    llm = LLMClient()
    decisions: dict[int, dict] = {}
    engine = "rule-based planner"
    if llm.available and opps_with_evidence:
        try:
            for start in range(0, len(opps_with_evidence), 10):
                decisions.update(llm_decisions(llm, opps_with_evidence[start:start + 10], store_aov))
            engine = llm.name
        except (LLMError, ValueError, KeyError) as e:
            engine = f"rule-based planner (LLM error: {e})"
    for i, opp, ev in opps_with_evidence:
        if i not in decisions or not decisions[i].get("message"):
            decisions[i] = rule_decision(opp, ev, store_aov) | {"act": True}
    return decisions, engine
