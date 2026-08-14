"""FastAPI application entrypoint.

Run from the backend directory:
    uvicorn main:app --reload
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.llm import configured_providers
from api.admin import router as admin_router
from api.chat import router as chat_router
from api.conversations import router as conversations_router
from auth.router import router as auth_router
from config import settings

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
        "claude_model": settings.claude_model,
        "gemini_model": settings.gemini_model,
        # In order: the first entry is primary, later entries are fallbacks.
        "providers": providers,
        "llm_configured": bool(providers),
    }
