import json

import pytest
from fastapi import FastAPI, Request, Response

from fastapi_utils import middlewares


@pytest.mark.anyio
async def test_middleware_remove_path():
    app = FastAPI()
    middleware = middlewares.RemovePathMiddleware(app, path="/test")

    async def call_next(request: Request) -> Response:
        return Response(content=json.dumps({"path": request.scope["path"]}))

    request = Request(scope={"type": "http", "method": "GET", "path": "/test/foo"})
    response = await middleware.dispatch(request, call_next)

    assert json.loads(response.body) == {"path": "/foo"}
