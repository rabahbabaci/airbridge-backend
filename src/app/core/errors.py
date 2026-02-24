from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError


class AppError(Exception):
    """Base application error with a structured JSON body."""

    def __init__(self, code: str, message: str, details: Any = None, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.details = details
        self.status_code = status_code
        super().__init__(message)


class InvalidInputError(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(code="INVALID_INPUT", message=message, details=details, status_code=422)


class UnsupportedModeError(AppError):
    def __init__(self, mode: str) -> None:
        super().__init__(
            code="UNSUPPORTED_MODE",
            message=f"input_mode '{mode}' is not supported. Use 'flight_number' or 'route_search'.",
            status_code=422,
        )


def _error_body(code: str, message: str, details: Any = None) -> dict:
    body: dict = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return body


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message, exc.details),
    )


def _sanitize_errors(errors: list[dict]) -> list[dict]:
    """Convert non-JSON-serializable values (e.g. exceptions in ctx) to strings."""
    sanitized = []
    for err in errors:
        entry = {k: v for k, v in err.items() if k != "ctx"}
        if "ctx" in err:
            entry["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        sanitized.append(entry)
    return sanitized


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    raw_errors = exc.errors() if hasattr(exc, "errors") else []
    return JSONResponse(
        status_code=422,
        content=_error_body(
            code="INVALID_INPUT",
            message="Request validation failed.",
            details=_sanitize_errors(raw_errors),
        ),
    )
