from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RemoveCloudTraceHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        headers = dict(request.headers)
        headers.pop("x-cloud-trace-context", None)
        headers.pop("traceparent", None)

        request.scope["headers"] = [
            (key.encode(), value.encode()) for key, value in headers.items()
        ]

        response = await call_next(request)
        return response
