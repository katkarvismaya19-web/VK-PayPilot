"""PayPilot AI - Intelligent Commerce & Growth Agent (FastAPI entrypoint)."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import agent, analytics, demo, knowledge, portal, razorpay, support
from app.database import SessionLocal, init_db
from app.models import Customer

ROOT = Path(__file__).resolve().parents[1]


def seed_if_empty() -> None:
    db = SessionLocal()
    try:
        if db.query(Customer).count() == 0:
            from app.analytics.etl import load_csv_dir
            if not (ROOT / "data" / "customers.csv").exists():
                from data.generate_data import main as generate
                generate()
            print("Seeding database:", load_csv_dir(db, ROOT / "data"))
        from app.support.seed import seed_tickets
        seed_tickets(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_if_empty()
    yield


app = FastAPI(title="PayPilot AI", version="1.0.0", lifespan=lifespan,
              description="Intelligent commerce & growth agent: analytics + RAG-grounded decisions + Razorpay actions.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (analytics.router, agent.router, knowledge.router, razorpay.router, demo.router, portal.router, support.router):
    app.include_router(r)


@app.get("/api/health", tags=["system"])
def health():
    return {"status": "ok", "service": "paypilot-ai"}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/portal", include_in_schema=False)
def customer_portal():
    """Customer-facing page: same app, opened in customer mode."""
    return FileResponse(ROOT / "static" / "index.html")
