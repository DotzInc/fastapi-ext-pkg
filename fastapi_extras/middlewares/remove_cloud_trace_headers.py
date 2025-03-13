from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RemoveCloudTraceHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Remove os headers indesejados da requisição
        headers = dict(request.headers)
        headers.pop("x-cloud-trace-context", None)
        headers.pop("traceparent", None)

        # Cria uma nova requisição sem os headers removidos
        request.scope["headers"] = [
            (key.encode(), value.encode()) for key, value in headers.items()
        ]

        # Processa a requisição normalmente
        response = await call_next(request)
        return response
