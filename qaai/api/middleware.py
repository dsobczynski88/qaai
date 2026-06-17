import logging
import time
import uuid
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from qaai.core.constants import MAX_REQUEST_BODY_SIZE

request_logger = logging.getLogger("qaai.api.requests")


async def log_requests(request: Request, call_next: Callable):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start_time = time.perf_counter()

    request_logger.info(
        "Request started: %s %s",
        request.method,
        request.url.path,
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "client": request.client.host if request.client else None,
        },
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed = time.perf_counter() - start_time
        request_logger.error(
            "Request failed: %s %s",
            request.method,
            request.url.path,
            extra={"request_id": request_id, "error": str(exc), "elapsed_seconds": elapsed},
            exc_info=True,
        )
        raise

    elapsed = time.perf_counter() - start_time
    request_logger.info(
        "Request completed: %s %s - %s",
        request.method,
        request.url.path,
        response.status_code,
        extra={
            "request_id": request_id,
            "status_code": response.status_code,
            "elapsed_seconds": elapsed,
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


async def limit_request_size(request: Request, call_next: Callable):
    """Reject requests with bodies larger than MAX_REQUEST_BODY_SIZE."""
    if request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_BODY_SIZE:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large (max 10MB)"},
            )
    return await call_next(request)
