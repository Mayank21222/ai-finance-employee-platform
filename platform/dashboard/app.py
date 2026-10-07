"""Dashboard FastAPI app.

Run it with:
    uvicorn platform.dashboard.app:app --port 8001
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from platform.dashboard import db, html

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Comp Ops dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/runs", status_code=303)


@app.get("/runs", response_class=HTMLResponse, include_in_schema=False)
def runs_page() -> str:
    return html.runs_page(db.list_runs(), db.list_sessions())


@app.get("/sessions", response_class=HTMLResponse, include_in_schema=False)
def sessions_get(error: str = "") -> str:
    return html.sessions_page(db.list_sessions(), error=error)


@app.post("/sessions", include_in_schema=False)
def sessions_create(tenant: str = Form(...), currency: str = Form(...),
                    approval_threshold: str = Form(...),
                    user_role: str = Form(...)) -> Response:
    try:
        threshold = float(approval_threshold)
    except ValueError:
        return HTMLResponse(
            html.sessions_page(db.list_sessions(),
                               error="Approval threshold must be a number."),
            status_code=400,
        )
    db.add_session(tenant.strip(), currency.strip(), threshold, user_role.strip())
    return RedirectResponse("/sessions", status_code=303)


@app.post("/sessions/{session_id}/delete", include_in_schema=False)
def sessions_delete(session_id: int) -> Response:
    db.delete_session(session_id)
    return RedirectResponse("/sessions", status_code=303)
