"""A request a route turns down for a reason the person can act on: a code the app can
map to its own words, and plain words to show until it does.

An `HTTPException`, so without the handler (`http_guards.install`) it still answers
with its status and words; with it, the body is `{"code", "detail"}`, the shape the app
reads (`frontend/src/lib/api.ts`).
"""

from __future__ import annotations

from fastapi import HTTPException


class Refusal(HTTPException):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
