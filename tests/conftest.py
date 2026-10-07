"""Shared fixtures for the operator test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def payables_db():
    """Isolate the shared SQLite payables store around one test."""
    from mock_app import db

    db.reset()
    yield db
    db.reset()


@pytest.fixture()
def asgi_transport():
    """Route verifier HTTP calls into the mock FastAPI app in-process.

    httpx.ASGITransport is async-only; the verifier uses a sync Client, so wrap
    the async transport with a sync BaseTransport that runs each request on a
    throwaway event loop.
    """
    import asyncio

    import httpx

    from mock_app.app import app

    class SyncASGITransport(httpx.BaseTransport):
        def __init__(self, asgi_app):
            self._async = httpx.ASGITransport(app=asgi_app)

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            async def go() -> httpx.Response:
                resp = await self._async.handle_async_request(request)
                body = b"".join([chunk async for chunk in resp.stream])
                headers = [
                    (k, v) for k, v in resp.headers.items()
                    if k.lower() not in ("content-length", "transfer-encoding")
                ]
                return httpx.Response(
                    status_code=resp.status_code, headers=headers,
                    content=body, request=request,
                )

            return asyncio.run(go())

    return SyncASGITransport(app)
