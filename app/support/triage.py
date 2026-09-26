"""
Ticket triage and reply drafting.

classify()      category + priority from the customer's words and their value to the store
suggest_reply() a reply grounded in the support playbook (RAG), drafted by the LLM when one
                is configured, otherwise by category templates plus the best-matching policy
                sentence. The owner always reviews it before it is sent.
"""
from __future__ import annotations

import re

from app.agent.assistant import best_sentence  # noqa: F401  (fallback when a section has no Say: line)
from app.agent.llm import LLMClient, LLMError
from app.rag.knowledge_base import get_kb

CATEGORIES = {
    "payment": ("debited", "deducted", "charged", "payment", "paid", "upi", "card", "transaction", "double", "twice", "failed"),
    "refund": ("refund", "return", "money back", "reverse", "reversal", "cancel"),
    "order": ("order", "delivery", "delivered", "shipping", "courier", "track", "late", "delay", "arrive", "damaged", "wrong item"),
    "offer": ("coupon", "code", "discount", "offer", "promo", "voucher"),
    "account": ("stop", "unsubscribe", "spam", "too many messages", "email address", "phone number", "account", "login"),
    "product": ("size", "fit", "material", "colour", "color", "stock", "available", "compatible", "ingredient", "quality", "warranty"),
}
CATEGORY_LABELS = {"payment": "Payment", "refund": "Refund or return", "order": "Order or delivery", "offer": "Offer or coupon",
                   "account": "Account or messages", "product": "Product question", "other": "Other"}
OPT_OUT = ("stop sending", "stop messaging", "stop messages", "unsubscribe", "don't message", "do not message",
           "stop whatsapp", "stop these", "opt out")
URGENT = ("debited", "deducted", "charged twice", "double charge", "charged two", "fraud", "unauthori", "scam")
UPSET = ("worst", "angry", "disappointed", "pathetic", "terrible", "cheated", "consumer court", "never again", "!!!")
VALUABLE = ("Champions", "Can't Lose Them", "Loyal")
QUERIES = {"payment": "money debited payment failed order reversal fresh payment link",
           "refund": "refund return processed razorpay working days",
           "order": "order delivery delayed expected date goodwill free shipping",
           "offer": "coupon does not apply single-use reissue discount",
           "account": "stop receiving messages marketing consent opt out",
           "product": "product question size material frequently bought together",
           "other": "response times reply within 24 hours acknowledge customer"}


def _has(text: str, words) -> list[str]:
    t = text.lower()
    return [w for w in words if w in t]


def is_opt_out(text: str) -> bool:
    return bool(_has(text, OPT_OUT))


def classify(subject: str, body: str, segment: str = "", category_hint: str = "") -> dict:
    text = f"{subject} {body}"
    if category_hint in CATEGORY_LABELS and category_hint != "other":
        category = category_hint
    elif _has(text, OPT_OUT):
        category = "account"
    else:
        scores = {c: len(_has(text, words)) for c, words in CATEGORIES.items()}
        category = max(scores, key=scores.get) if max(scores.values()) else "other"
    reasons = []
    urgent_hits = _has(text, URGENT)
    if urgent_hits and category in ("payment", "refund"):
        priority = "urgent"
        reasons.append(f"customer reports money taken ({urgent_hits[0]})")
    elif category in ("payment", "refund") or segment in VALUABLE or _has(text, UPSET):
        priority = "high"
        if category in ("payment", "refund"):
            reasons.append("money involved")
        if segment in VALUABLE:
            reasons.append(f"{segment} customer")
        if _has(text, UPSET):
            reasons.append("customer is upset")
    elif category == "product":
        priority = "low"
        reasons.append("pre-sale question")
    else:
        priority = "normal"
    return {"category": category, "priority": priority, "reason": ", ".join(reasons) or "standard request"}


TEMPLATES = {
    "payment": "Hi {first}, thanks for flagging this, and sorry for the worry. {policy} I'm checking the payment on our side and will confirm your order status here within 2 hours.",
    "refund": "Hi {first}, thanks for reaching out about your refund. {policy} I'll share the refund reference here as soon as it's issued.",
    "order": "Hi {first}, thanks for your patience with this order. {policy} I'll update you here the moment it moves.",
    "offer": "Hi {first}, sorry the code didn't work. {policy} I'll check it and reply here today.",
    "account": "Hi {first}, understood. {policy} You'll still get updates about your own orders and payments.",
    "product": "Hi {first}, happy to help. {policy} Let me know if there's anything else you'd like to check before you order.",
    "other": "Hi {first}, thanks for getting in touch. {policy} I'll follow up here shortly.",
}
SAY = re.compile(r"^Say:\s*(.+)$", re.M)
SYSTEM = """You are the support assistant for an Indian online store. Draft a reply the store owner will review before sending.
Rules: use ONLY the numbered policy evidence for facts about timelines, refunds and offers; cite it inline as [n].
Be warm, brief (under 90 words), specific; one apology at most; end with exactly what happens next.
Never promise a discount above the policy caps. Reply with the message text only."""


def suggest_reply(ticket: dict, customer: dict | None = None) -> dict:
    """Draft a policy-grounded reply. Returns {text, citations, engine}."""
    customer = customer or {}
    first = (ticket.get("customer_name") or customer.get("customer_name") or "there").split()[0]
    thread = ticket.get("messages") or []
    last_customer = next((m["body"] for m in reversed(thread) if m.get("author") == "customer"), ticket.get("subject", ""))
    cat = ticket.get("category", "other")
    if cat == "account" and not is_opt_out(f"{ticket.get('subject', '')} {last_customer}"):
        cat = "other"  # an account change, not an opt-out: don't claim messages were turned off
    ev = get_kb().evidence(f"{QUERIES.get(cat, '')} {ticket.get('subject', '')} {last_customer}", k=3)
    support = [e for e in ev.items if "Support" in e.title] or ev.items

    llm = LLMClient()
    if llm.available and ev.sufficient:
        try:
            convo = "\n".join(f"{m['author']}: {m['body']}" for m in thread[-6:])
            text = llm.complete(SYSTEM, f"Customer: {first} (segment: {customer.get('segment', 'unknown')})\n"
                                        f"Ticket: {ticket.get('subject')} [{cat}]\nConversation:\n{convo}\n\n"
                                        f"Policy evidence:\n{ev.as_prompt()}", max_tokens=400).strip()
            used = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
            valid = {e.citation_id for e in ev.items}
            text = re.sub(r"\s*\[(\d+)\]", lambda m: m.group(0) if int(m.group(1)) in valid else "", text)
            return {"text": text, "engine": llm.name,
                    "citations": [c for c in ev.citations() if c["id"] in used & valid]}
        except LLMError:
            pass

    if not support:
        return {"text": TEMPLATES["other"].format(first=first, policy="I'm looking into this now"),
                "citations": [], "engine": "template"}
    # prefer the section written for this category; its "Say:" line is the customer-facing wording
    top = next((e for e in support if SAY.search(e.text)), support[0])
    say = SAY.search(top.text)
    policy = say.group(1).rstrip(".") if say else best_sentence(f"{QUERIES.get(cat, '')} {last_customer}", top.text)
    policy = policy[0].upper() + policy[1:]
    text = TEMPLATES.get(cat, TEMPLATES["other"]).format(first=first, policy=f"{policy} [{top.citation_id}].")
    return {"text": text, "engine": "rule-based drafter",
            "citations": [c for c in ev.citations() if c["id"] == top.citation_id]}
