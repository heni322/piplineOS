"""RFC 9457 problem+json errors used by every API endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import correlation_id_var, get_logger

log = get_logger(__name__)


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        *,
        type_: str = "about:blank",
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(title)
        self.status, self.title, self.detail, self.type, self.errors = status, title, detail, type_, errors or []


def problem(
    status: int,
    title: str,
    detail: str | None,
    request: Request,
    type_: str = "about:blank",
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": type_,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "correlation_id": correlation_id_var.get(),
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem(exc.status, exc.title, exc.detail, request, exc.type, exc.errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(exc.status_code, str(exc.detail), None, request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return problem(422, "Validation error", "Request body or parameters are invalid.", request, errors=errors)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_type=type(exc).__name__)
        return problem(500, "Internal server error", "An unexpected error occurred.", request)
