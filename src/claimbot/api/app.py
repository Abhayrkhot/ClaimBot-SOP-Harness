"""HTTP boundary. Validates transport inputs and delegates every decision to the engine."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from ..config import Settings
from ..obs.logging import configure_logging, request_id_var
from ..obs.tracing import configure_tracing
from ..runtime import Runtime, build_runtime, open_store
from ..sop.engine import SessionNotFound, TurnResult
from ..store.base import ConcurrentTurnError
from .outbox import OutboxDispatcher
from .ratelimit import RateLimiter

LOG = logging.getLogger("claimbot.api")
WEB = Path(__file__).resolve().parents[1] / "web"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
}


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    message: str | None = Field(default=None, min_length=1, max_length=4000)
    text: str | None = Field(default=None, min_length=1, max_length=4000, description="v1 alias of message")
    request_id: str | None = Field(default=None, max_length=64)


class EndRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


def _public(result: TurnResult, email_preview: dict[str, str] | None) -> dict[str, Any]:
    s: Any = result.session
    return {
        "session_id": result.session_id,
        "reply": result.reply,
        "phase": s.phase,
        "verified": s.verified,
        "case_id": s.case_id,
        "awaiting": s.awaiting,
        "captured_fields": sorted(s.evidence),
        "handoff": s.handoff,
        "email_choice": s.consents.get("email_summary"),
        "email_preview": email_preview,
        "response_mode": s.response_mode,
        "render_mode": result.render_mode,
        "turn": s.turn,
        "latency_ms": round(result.latency_ms, 1),
        "llm_calls": len(result.usage),
        "policy": {
            "disclosed": [e.data["fact"] for e in result.events if e.kind == "fact_disclosed"],
            "blocked": [
                {"fact": e.data["fact"], "reason": e.data["reason"]}
                for e in result.events
                if e.kind == "disclosure_blocked"
            ],
            "transitions": [
                f"{e.data['source']}->{e.data['target']}"
                for e in result.events
                if e.kind == "phase_transition"
            ],
        },
    }


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level, settings.log_format)
    configure_tracing(settings.otel_exporter_otlp_endpoint)
    limiter = RateLimiter(settings.rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = runtime or build_runtime(settings, await open_store(settings))
        app.state.runtime = rt
        dispatcher = OutboxDispatcher(rt.store, settings)
        task = asyncio.create_task(dispatcher.run()) if settings.smtp_host else None
        LOG.info("started", extra={"mode": rt.mode, "store": type(rt.store).__name__})
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if runtime is None:
                await rt.store.close()

    app = FastAPI(title="ClaimBot SOP Harness", version="2.0.0", lifespan=lifespan)

    def rt_dep(request: Request) -> Runtime:
        return request.app.state.runtime  # type: ignore[no-any-return]

    def client_key(request: Request) -> str:
        if settings.trust_forwarded_for and (fwd := request.headers.get("x-forwarded-for")):
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def boundary(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            if request.method == "POST" and not limiter.allow(client_key(request)):
                response: Response = JSONResponse({"detail": "Too many requests"}, status_code=429)
            else:
                response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers.update(SECURITY_HEADERS)
        response.headers["X-Request-ID"] = rid
        if request.url.path not in ("/metrics", "/healthz"):
            LOG.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                    "request_id": rid,
                },
            )
        return response

    def require_admin(authorization: str | None = Header(default=None)) -> None:
        token = settings.admin_api_token
        if not token:
            raise HTTPException(404, "Not found")
        if not authorization or not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "Authentication required")

    async def chat(body: ChatRequest, rt: Runtime = Depends(rt_dep)) -> dict[str, Any]:
        message = (body.message or body.text or "").strip()
        if not message:
            raise HTTPException(422, "Message cannot be blank")
        try:
            result = await rt.engine.handle(body.session_id, message, body.request_id)
        except SessionNotFound as exc:
            raise HTTPException(404, "Session expired or not found. Start a new chat.") from exc
        except ConcurrentTurnError as exc:
            raise HTTPException(409, "Another message for this conversation is in progress. Retry.") from exc
        preview = None
        if result.session.consents.get("email_summary") == "granted":
            messages = await rt.store.outbox_for(result.session_id)
            if messages:
                m = messages[0]
                preview = {"to": m.recipient, "subject": m.subject, "body": m.body, "status": m.status}
        return _public(result, preview)

    app.post("/v1/chat")(chat)
    app.post("/api/chat", include_in_schema=False)(chat)

    @app.post("/v1/sessions/{session_id}/end")
    async def end(session_id: str, rt: Runtime = Depends(rt_dep)) -> dict[str, bool]:
        try:
            ok = await rt.engine.end(session_id)
        except ConcurrentTurnError as exc:
            raise HTTPException(409, "Conversation changed; retry") from exc
        if not ok:
            raise HTTPException(404, "Session expired or not found")
        return {"ended": True}

    @app.post("/api/end", include_in_schema=False)
    async def end_v1(body: EndRequest, rt: Runtime = Depends(rt_dep)) -> dict[str, bool]:
        return await end(body.session_id, rt)

    @app.get("/v1/sessions/{session_id}/audit", dependencies=[Depends(require_admin)])
    async def audit(session_id: str, rt: Runtime = Depends(rt_dep)) -> dict[str, Any]:
        return {"session_id": session_id, "events": await rt.store.audit(session_id)}

    @app.get("/v1/outbox", dependencies=[Depends(require_admin)])
    async def outbox(rt: Runtime = Depends(rt_dep)) -> dict[str, Any]:
        rows = await rt.store.outbox_all()
        return {"count": len(rows), "messages": [r.__dict__ for r in rows]}

    app.get("/api/outbox", include_in_schema=False, dependencies=[Depends(require_admin)])(outbox)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz")
    async def readyz(rt: Runtime = Depends(rt_dep)) -> JSONResponse:
        db_ok = await rt.store.ping()
        breakers = rt.llm.health() if rt.llm else {}
        model_ok = not breakers or any(state != "open" for state in breakers.values())
        body = {"ok": db_ok and model_ok, "db": db_ok, "mode": rt.mode, "providers": breakers}
        return JSONResponse(body, status_code=200 if body["ok"] else 503)

    @app.get("/api/health")
    async def health(rt: Runtime = Depends(rt_dep)) -> dict[str, Any]:
        provider = rt.llm.primary.name if rt.llm else None
        return {"ok": True, "mode": rt.mode, "provider": provider, "response_mode": settings.response_mode}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    async def ui() -> HTMLResponse:
        return HTMLResponse((WEB / "index.html").read_text())

    return app


def app_factory() -> FastAPI:
    return create_app()
