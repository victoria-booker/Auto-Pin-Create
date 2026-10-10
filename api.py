"""
Small API wrapping the Pinterest pipeline (and product-idea research) for a
Custom GPT Action to call.

Run locally with:
    PIN_API_KEY=yourkey uvicorn api:app --reload

FastAPI serves the schema at /openapi.json — that URL is what you paste
into the GPT Action's "Import from URL" field.

Operation IDs are set explicitly so the GPT sees getBoards, createPin,
createPinsBatch, generateIdeas, etc. rather than FastAPI's auto-generated
names.

Endpoints live in routers/, grouped by concern:
    routers/boards.py    — getBoards, ensureBoard, ensureSection
    routers/pins.py       — createPin, createPinByBoard, and their batch forms
    routers/research.py   — generateIdeas (product_research/idea_generator.py)
    routers/schedule.py   — schedulePins, getScheduledPins, cancelScheduledPin
Auth (the X-API-Key header check) is shared via api_auth.py and applied
per-router, so individual endpoints don't repeat it.

Scheduled pins are posted by a background thread (pinterest/pin_worker.py)
started with the app; set PIN_WORKER_ENABLED=0 to turn it off.

pip install fastapi uvicorn
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pinterest.pin_worker import start_background_worker
from routers import boards, dashboard, pins, research, schedule


@asynccontextmanager
async def lifespan(app):
    stop_worker = start_background_worker()
    yield
    stop_worker()


app = FastAPI(
    lifespan=lifespan,
    title="Pinterest Pin API",
    description="Creates Pinterest pins from finished product images and publishing copy, "
                "and generates product ideas from cross-platform search signal.",
    version="1.2.0",
    servers=[{"url": os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000").rstrip("/")}],
)

app.include_router(boards.router)
app.include_router(pins.router)
app.include_router(research.router)
app.include_router(schedule.router)
app.include_router(dashboard.login_router)
app.include_router(dashboard.router)


@app.get("/health", include_in_schema=False)
def health_check():
    return {"status": "ok"}
