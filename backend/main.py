"""FastAPI application entrypoint.

Run from the backend directory:
    uvicorn main:app --reload
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agents.llm import configured_providers, model_is_available
from api.admin import router as admin_router
from api.chat import router as chat_router
from api.conversations import router as conversations_router
from auth.router import router as auth_router
from config import settings
from rbac.model_catalog import MODEL_CATALOGUE

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="AI + RBAC Agentic Orchestration POC",
    description=(
        "One chat endpoint. A planner agent routes each prompt to a role-based agent, "
        "the agent selects a tool, and the tool checks RBAC before touching PostgreSQL."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(conversations_router)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    providers = configured_providers()
    return {
        "status": "ok",
        # The catalogue a super admin can hand out; `usable` is false when the
        # provider behind a model has no credentials on this server.
        "models": [
            {"key": model.key, "model_id": model.model_id, "usable": model_is_available(model)}
            for model in MODEL_CATALOGUE
        ],
        "providers": providers,
        "llm_configured": bool(providers),
    }


# The built frontend, when this deployment carries one. The Docker build drops
# frontend/dist here so a single service answers for both the app and the API on
# one origin — which is also why CORS stops mattering there. In development the
# directory is absent and Vite serves the frontend instead.
STATIC_DIR = Path(__file__).resolve().parent / "static"

if STATIC_DIR.is_dir():
    # Mounted last, so /api/*, /docs and /openapi.json still match first.
    # html=True resolves directory indexes, which is what /demo/ needs.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
