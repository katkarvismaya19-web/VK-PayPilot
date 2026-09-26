"""
Outbound messaging. Default is an outbox (messages are recorded, not sent) so
nothing reaches real customers during development. Plug a provider into
`deliver` (SMTP, SendGrid, WhatsApp Cloud API, MSG91 ...) for production.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

OUTBOX = Path(__file__).resolve().parents[2] / "outbox.jsonl"


def deliver(channel: str, to: str, subject: str, body: str, meta: dict | None = None) -> dict:
    record = {"channel": channel, "to": to, "subject": subject, "body": body, "meta": meta or {},
              "queued_at": datetime.now(timezone.utc).isoformat(), "provider": "outbox"}
    with OUTBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return {"delivered": True, "provider": "outbox", "channel": channel, "to": to}


def read_outbox(limit: int = 50) -> list[dict]:
    if not OUTBOX.exists():
        return []
    lines = OUTBOX.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(l) for l in lines[-limit:]][::-1]
