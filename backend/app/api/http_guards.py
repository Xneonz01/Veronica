"""What every HTTP request passes through: the cross-origin write check and the error
handlers that keep a raw error from ever reaching the client, and a route's refusal
with its code."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.refusals import Refusal
from app.core.logging import log_failure
from app.core.origins import is_trusted_origin

logger = logging.getLogger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def install(app: FastAPI) -> None:
    """Register the write check and the error handlers on `app`."""

    @app.middleware("http")
    async def block_untrusted_writes(request: Request, call_next: Any) -> Response:
        """Refuse cross-origin writes (CSRF).

        CORS only stops the browser from reading the response. A bodyless or multipart
        POST is a "simple request" sent without a preflight, so its side effect still
        lands. Without this check any page the user visits could delete memories or
        index documents. Safe methods pass through.
        """
        host = request.headers.get("host")
        own_origin = f"{request.url.scheme}://{host}" if host else None
        if request.method not in _SAFE_METHODS and not is_trusted_origin(
            request.headers.get("origin"), own_origin=own_origin
        ):
            return JSONResponse(
                status_code=403,
                content={"code": "untrusted_origin", "detail": "That request was blocked."},
            )
        result: Response = await call_next(request)
        return result

    @app.exception_handler(RequestValidationError)
    async def on_invalid_input(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """Return a typed error, never Pydantic's raw error list.

        FastAPI's default `detail` is a list of dicts; the frontend renders `detail` as
        a string, and an array there crashes React. A stable {code, field, detail}
        shape can be mapped to user-facing copy.
        """
        first = exc.errors()[0] if exc.errors() else {}
        location = [str(part) for part in first.get("loc", ()) if part not in ("body", "query")]
        # Where and what kind, never the value: an error carries what was sent, and a
        # note or a pasted document is someone's words.
        logger.info(
            "invalid request: %s",
            [(error.get("loc"), error.get("type")) for error in exc.errors()],
        )
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_input",
                "field": location[-1] if location else None,
                "detail": "That value isn't valid — check the highlighted field.",
            },
        )

    @app.exception_handler(Refusal)
    async def on_refusal(_request: Request, exc: Refusal) -> JSONResponse:
        """A route's refusal with its code, which FastAPI's own handler would drop."""
        return JSONResponse(
            status_code=exc.status_code, content={"code": exc.code, "detail": exc.detail}
        )

    @app.exception_handler(Exception)
    async def on_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        """Last resort: log where it failed, hand the client a code it can map.

        Without this the client gets a `text/plain` "Internal Server Error" body with
        no code, which the frontend cannot map to copy. Never the exception's message:
        an encoding error quotes the string it failed on, a search key among them.
        """
        log_failure(logger, "unhandled error", exc)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal",
                "detail": "Something went wrong on Veronica's side. Try again in a moment.",
            },
        )
