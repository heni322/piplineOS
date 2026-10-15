"""FastAPI application factory."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.api import ops, public, webhooks
from app.api.v1 import (
    auth,
    billing,
    clients,
    conversations,
    crm,
    governance,
    inbound,
    leads,
    operations,
    proof,
    sources,
    tasks,
)
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, correlation_id_var
from app.settings import get_settings

CSP = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    prod = settings.environment in ("staging", "prod")
    app = FastAPI(
        title="PipelineOS API",
        version="0.5.0",
        docs_url=None if prod else "/api/docs",
        redoc_url=None,
        openapi_url=None if prod else "/api/v1/openapi.json",
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def correlation_and_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        if len(cid) > 100:
            cid = str(uuid.uuid4())
        token = correlation_id_var.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id_var.reset(token)
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        if prod:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    @app.get("/api/v1/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment, "global_go_live": str(settings.global_go_live)}

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(leads.router, prefix="/api/v1")
    app.include_router(governance.router, prefix="/api/v1")
    app.include_router(operations.router, prefix="/api/v1")
    app.include_router(conversations.router, prefix="/api/v1")
    app.include_router(billing.router, prefix="/api/v1")
    app.include_router(clients.router, prefix="/api/v1")
    app.include_router(crm.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(sources.router, prefix="/api/v1")
    app.include_router(proof.router, prefix="/api/v1")
    app.include_router(inbound.router, prefix="/api/v1")
    app.include_router(public.router)
    app.include_router(webhooks.router)
    app.include_router(ops.router)
    return app


app = create_app()
