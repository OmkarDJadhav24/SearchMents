import time
import uuid
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import structlog.contextvars

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request with latency, method, path, and status code."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        start = time.perf_counter()

        # Bind request-scoped context so all log lines in this request carry it
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        response.headers["X-Request-ID"] = request_id
        return response


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Extracts user_id from a decoded JWT (set by the auth dependency)
    and binds it to the structlog context so every log line in the
    request carries the tenant identifier.

    The actual JWT decoding happens in the FastAPI dependency
    (app/dependencies.py → get_current_user). This middleware only
    picks up what the dependency already attached to request.state.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Bind user_id if auth dependency populated request.state
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            structlog.contextvars.bind_contextvars(user_id=str(user_id))

        return response