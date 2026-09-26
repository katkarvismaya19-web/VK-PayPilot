# PayPilot AI — Intelligent Commerce & Growth Agent

**Live demo:** [katkarvismaya19-web.github.io/PayPilot-AI](https://katkarvismaya19-web.github.io/PayPilot-AI/) — the owner dashboard and customer portal, running in the browser on a snapshot of the real pipeline's output

Built on two earlier projects: [SmartRetailAnalytics](https://github.com/katkarvismaya19-web/Vismayakatkar-SmartRetailAnalytics) (analytics layer) and [VeriRAG](https://github.com/katkarvismaya19-web/VeriRAG) (retrieval and citations).

PayPilot AI reads an online store's customers, orders, carts and payments, finds the revenue
worth chasing (abandoned carts, failed payments, lapsing customers, one-time buyers, VIPs,
cross-sell gaps), and drafts a personalised action for each opportunity. Every action cites the
policy that justifies it, passes hard guardrails, and can be executed through Razorpay payment
links — then the Razorpay webhook tells the agent whether it worked.

Customers get their own portal where they answer those messages (pay, ask a question, say
not interested, or stop messages) and raise support requests. The owner resolves them from a
triaged support inbox, with replies drafted from the store's support playbook.

```
 Customers, orders, carts, payments
            │
            ▼
 ┌─────────────────────┐   RFM segments, churn risk, CLV,        (SmartRetailAnalytics)
 │  Analytics layer    │── KPIs, category & payment-failure
 └─────────────────────┘   analysis, basket affinity
            │
            ▼
 ┌─────────────────────┐   finds opportunities, scores each by
 │  Perception         │── probability × value = expected value
 └─────────────────────┘
            │
            ▼
 ┌─────────────────────┐   retrieves playbook evidence with        (VeriRAG)
 │  RAG knowledge base │── citation ids from knowledge/*.md
 └─────────────────────┘
            │
            ▼
 ┌─────────────────────┐   LLM (optional) or built-in
 │  Planner            │── rule-based planner picks channel,
 └─────────────────────┘   offer, message, citations
            │
            ▼
 ┌─────────────────────┐   consent, frequency cap, discount caps,
 │  Guardrails (code)  │── margin floor, risk level — enforced
 └─────────────────────┘   AFTER the LLM, so it can't overspend
            │
            ▼
 ┌─────────────────────┐   Razorpay payment link, single-use
 │  Executor           │── coupon, IST quiet hours, outbox
 └─────────────────────┘
            │
            ▼
 Razorpay webhook ──► outcome attribution ──► "Recovered by PayPilot"
```

## Run it

```bash
pip install -r requirements.txt
python run.py
```

Open http://localhost:8000 for the owner dashboard and http://localhost:8000/portal for the
customer portal. On first start PayPilot generates a realistic sample store
(320 customers, 31 products, ~2,300 orders, ~1,400 payments, ~60 abandoned carts, and a
handful of support tickets built from real situations in that data) and loads it. No API keys are needed: without an LLM key the agent uses its rule-based planner,
and without Razorpay keys payment links are created in sandbox mode.

Interactive API docs: http://localhost:8000/docs

With Docker instead:

```bash
docker compose up --build
```

`docs/index.html` (served by GitHub Pages as the live demo) is a single-file, offline version of the dashboard built from a real
run of the pipeline (`python scripts/build_demo.py`, which also refreshes `dist/paypilot-demo.html`). Open it in any browser; the "Customer
portal" button switches to the customer side, so the whole loop works on one page, and changes
are kept in the browser until you press "Reset demo".

## Customer portal and support

The customer portal (`/portal`) shows a signed-in customer the messages PayPilot sent them,
their recent orders and their requests. On each message they can:

| Customer action | What happens |
|---|---|
| Pay / Order | Pays through the Razorpay link (sandbox mode records a test payment through the same webhook handler); revenue is attributed to the action |
| Ask a question | Opens a support ticket linked to that message |
| Not interested | Closes the action as not converted |
| Stop these messages | Turns off marketing consent; the agent's guardrails stop sending them offers |

They can also raise a request about a payment, order, refund, coupon, product or their account,
and reply to the store in the ticket thread. Replying to a resolved ticket reopens it.

The owner's **Support** tab lists tickets sorted by status and priority. Triage
(`app/support/triage.py`) sets the category and priority from the customer's words and their
value to the store: money debited or double charges are urgent; payment and refund issues,
upset customers and high-value customers are high. Each open ticket shows the customer's
segment, spend and churn risk, plus a suggested reply retrieved from
`knowledge/07_customer_support.md` with its citation. The LLM drafts it when configured,
otherwise a template uses the playbook's customer-facing "Say:" line. The owner edits and sends
it, can attach a fresh Razorpay payment link (a paid link posts a confirmation into the ticket
via the webhook), and resolves or reopens tickets. A "stop messages" request turns off
marketing consent as soon as it arrives.

The portal signs customers in by email only, which is fine for a demo. Before real use, put it
behind OTP or magic-link login and take the customer id from the session rather than the URL.

### Publishing the live demo

In the GitHub repository go to **Settings → Pages**, choose **Deploy from a branch**, branch
`main`, folder `/docs`, and save. After a minute the demo is live at
`https://<your-username>.github.io/PayPilot-AI/`.

## Configuration

Copy `.env.example` to `.env` and fill in what you need.

| Variable | Effect |
|---|---|
| `LLM_PROVIDER=openai` + `OPENAI_API_KEY` | An LLM writes the decisions, support replies and "Ask PayPilot" answers |
| `OPENAI_MODEL` | Which model to use (default `gpt-4o-mini`) |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Real payment links via Razorpay (use test keys first) |
| `RAZORPAY_WEBHOOK_SECRET` | Verifies webhook signatures (HMAC-SHA256) |
| `AUTO_EXECUTE=true` | Agent sends low-risk actions without waiting for approval |
| `MAX_DISCOUNT_PERCENT` | Absolute discount ceiling (default 20) |
| `MAX_ACTIONS_PER_CUSTOMER_PER_WEEK` | Frequency cap (default 2) |
| `DATABASE_URL` | SQLite by default; any SQLAlchemy URL (e.g. Postgres) works |
| `EMBEDDINGS=bge` | Use BAAI/bge-small embeddings (needs `sentence-transformers`) instead of TF-IDF |

### Razorpay webhook

In the Razorpay dashboard → Settings → Webhooks, add
`https://<your-host>/api/razorpay/webhook` with the events `payment.captured`,
`payment.failed` and `payment_link.paid`, and put the same secret in
`RAZORPAY_WEBHOOK_SECRET`. For local testing expose port 8000 with a tunnel (ngrok,
cloudflared). Each payment link carries `notes.paypilot_action_id`, which is how a paid link is
attributed back to the action that produced it. Webhooks are processed idempotently.

Without real traffic, the dashboard's "Simulate customer responses" button replays sent
actions through the same webhook handler.

## Using your own data

Replace the CSVs in `data/` (`customers.csv`, `products.csv`, `sales.csv`, and optionally
`payments.csv`, `carts.csv`), delete `paypilot.db`, and restart. The original
SmartRetailAnalytics CSVs load as-is; missing columns get sensible defaults.

## How the two repositories map in

| Source | Where it lives now | What changed |
|---|---|---|
| **SmartRetailAnalytics** — star schema (`dim_customer`, `dim_product`, `fact_sales`), pandas ETL, customer & product analytics | `app/models.py`, `app/analytics/` | Schema extended with payments, carts, agent actions and outcomes; added RFM segmentation, churn risk, CLV, payment-failure analysis and basket affinity |
| **VeriRAG** — chunking, embedding retrieval, evidence builder with citation ids, FastAPI backend, pytest | `app/rag/`, `app/api/`, `tests/` | Knowledge base of growth playbooks; retrieval gives the agent evidence it must cite; citations are validated, and unknown ones are dropped |

## Project layout

```
app/
  main.py              FastAPI app, seeds sample data, serves the dashboard
  config.py            settings from environment / .env
  models.py            SQLAlchemy models
  analytics/           KPIs, segments, churn, CLV, affinity, ETL
  rag/                 chunker, embeddings, knowledge base
  agent/
    perception.py      opportunity detection + expected value
    planner.py         LLM decisions with rule-based fallback
    guardrails.py      policy enforcement
    orchestrator.py    one agent run end to end
    executor.py        Razorpay links, coupons, quiet hours, delivery
    assistant.py       "Ask PayPilot" question answering with citations
  support/
    triage.py          ticket category + priority, playbook-grounded reply drafts
    service.py         tickets, customer responses, portal view
    seed.py            sample inbox built from the store's own data
  integrations/        Razorpay REST client, message outbox
  api/                 REST routes
knowledge/             playbooks the agent retrieves from (add your own .md files)
data/generate_data.py  seeded synthetic store generator
static/index.html      owner dashboard and customer portal (served at / and /portal)
scripts/build_demo.py  builds the offline demo (docs/index.html for GitHub Pages)
tests/                 pytest suite
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/analytics/kpis` | Revenue, orders, AOV, repeat rate, payment success, value at risk |
| GET | `/api/analytics/trend` | Weekly revenue |
| GET | `/api/analytics/segments` | RFM segment sizes and value |
| GET | `/api/analytics/categories` | Category performance |
| GET | `/api/analytics/payment-failures` | Failures by method and reason |
| GET | `/api/analytics/affinity` | Product pairs bought together (lift) |
| GET | `/api/analytics/customers` | Customer list with segment, churn risk, CLV |
| GET | `/api/analytics/customers/{id}` | One customer's profile, orders, recommendations |
| GET | `/api/analytics/carts` | Abandoned carts |
| GET | `/api/agent/status` | Planner engine, Razorpay mode, guardrail settings |
| POST | `/api/agent/run` | Scan for opportunities and propose actions |
| GET | `/api/agent/actions` | Proposed / sent / rejected actions |
| POST | `/api/agent/actions/{id}/execute` | Approve and send one action |
| POST | `/api/agent/actions/{id}/reject` | Reject an action |
| POST | `/api/agent/actions/execute-low-risk` | Send every low-risk proposal |
| POST | `/api/agent/ask` | Ask a question about the store, answered with citations |
| GET | `/api/agent/impact` | Revenue recovered and conversion of sent actions |
| GET | `/api/agent/outbox` | Messages that were "sent" |
| GET | `/api/knowledge/stats` · `/search` | Inspect the knowledge base |
| POST | `/api/knowledge/documents` | Add a playbook at runtime |
| POST | `/api/razorpay/webhook` | Razorpay events |
| GET | `/api/support/stats` | Open, urgent, waiting on you, resolution time, customer responses |
| GET | `/api/support/tickets?status=` | Triaged ticket list (`active`, `open`, `in_progress`, `resolved`) |
| GET | `/api/support/tickets/{id}` | Thread, customer context and suggested reply |
| POST | `/api/support/tickets/{id}/reply` | Reply (optionally with a Razorpay payment link) and set status |
| POST | `/api/support/tickets/{id}/status` | Resolve or reopen |
| GET | `/api/portal/customers?q=` | Sign-in lookup by name or email |
| GET | `/api/portal/{customer_id}` | Customer's messages, orders and requests |
| POST | `/api/portal/{customer_id}/messages/{action_id}/respond` | `paid`, `not_interested`, `stop` or `question` |
| POST | `/api/portal/{customer_id}/tickets` | Raise a request |
| POST | `/api/portal/{customer_id}/tickets/{id}/messages` | Reply in a ticket thread |
| POST | `/api/demo/simulate-outcomes` · `/reset` | Demo helpers |

## Tests

```bash
pytest
```

The suite covers analytics, retrieval, guardrails, the agent run, Razorpay webhook
verification and attribution, the customer portal and ticket lifecycle (including that one
customer can't read or answer another's messages or tickets), and an adversarial LLM test: a mocked model that proposes a
30% discount on a disallowed channel with an invented citation, which the guardrails clamp.

## Notes on safety

The LLM never has the last word. Discounts are capped per opportunity type and segment and
can't breach the margin floor, customers who haven't consented aren't messaged, promotional
messages are held outside 9 AM–9 PM IST, each customer gets at most one action per run and a
weekly cap, and anything above low risk waits for human approval unless you change that.
