from unittest.mock import Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from fastapi_extras.middlewares import MiddlewareSpec, configure_middlewares
from fastapi_extras.middlewares.remove_cloud_trace_headers import RemoveCloudTraceHeadersMiddleware
from fastapi_extras.middlewares.remove_path import RemovePathMiddleware


class MarkerMiddleware:
    def __init__(self, app, label="marker"):
        self.app = app
        self.label = label

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("markers", []).append(self.label)
        await self.app(scope, receive, send)


def test_default_stack_preserves_path_headers_and_cors():
    app = FastAPI()

    @app.get("/hello")
    async def hello(request: Request):
        return {"headers": dict(request.headers), "markers": request.scope["markers"]}

    configure_middlewares(
        app,
        root_path="/service",
        cors={"allow_origins": ["https://example.com"]},
        extra_middlewares=(MiddlewareSpec("marker", MarkerMiddleware),),
    )
    assert [m.cls for m in app.user_middleware] == [
        CORSMiddleware,
        MarkerMiddleware,
        RemoveCloudTraceHeadersMiddleware,
        RemovePathMiddleware,
    ]
    with TestClient(app) as client:
        response = client.get(
            "/service/hello",
            headers={
                "origin": "https://example.com",
                "traceparent": "test",
                "x-cloud-trace-context": "test",
            },
        )
        assert response.status_code == 200
        assert "traceparent" not in response.json()["headers"]
        assert "x-cloud-trace-context" not in response.json()["headers"]
        assert response.json()["markers"] == ["marker"]
        assert response.headers["access-control-allow-origin"] == "https://example.com"
        preflight = client.options(
            "/service/hello",
            headers={
                "origin": "https://example.com",
                "access-control-request-method": "GET",
            },
        )
        assert preflight.status_code == 200


def test_explicit_order_override_and_disable():
    app = FastAPI()
    configure_middlewares(
        app,
        root_path="/service",
        remove_cloud_trace_headers=False,
        extra_middlewares=(MiddlewareSpec("marker", MarkerMiddleware),),
        overrides=(
            MiddlewareSpec("remove_path", RemovePathMiddleware, enabled=False),
            MiddlewareSpec("marker", MarkerMiddleware, {"label": "replacement"}),
        ),
        order=("marker", "remove_path"),
    )
    assert len(app.user_middleware) == 1
    assert app.user_middleware[0].kwargs == {"label": "replacement"}


@pytest.mark.parametrize("order", [("missing",), (), ("remove_path", "remove_path")])
def test_invalid_order_is_atomic(order):
    app = FastAPI()
    with pytest.raises(ValueError):
        configure_middlewares(
            app, root_path="/service", remove_cloud_trace_headers=False, order=order
        )
    assert app.user_middleware == []


def test_duplicate_setup_and_started_app_are_rejected():
    app = FastAPI()
    configure_middlewares(app)
    with pytest.raises(RuntimeError, match="already"):
        configure_middlewares(app)
    started = FastAPI()
    started.middleware_stack = started.build_middleware_stack()
    with pytest.raises(RuntimeError, match="startup"):
        configure_middlewares(started)


def test_disabled_apm_does_not_create_client(monkeypatch):
    factory = Mock()
    monkeypatch.setattr("elasticapm.contrib.starlette.make_apm_client", factory)
    app = FastAPI()
    configure_middlewares(app, apm_config={"SERVICE_NAME": "test"}, apm_enabled=False)
    factory.assert_not_called()
    assert [m.cls for m in app.user_middleware] == [RemoveCloudTraceHeadersMiddleware]


def test_apm_config_and_client_injection(monkeypatch):
    from elasticapm.contrib.starlette import ElasticAPM

    client = object()
    factory = Mock(return_value=client)
    monkeypatch.setattr("elasticapm.contrib.starlette.make_apm_client", factory)
    app = FastAPI()
    configure_middlewares(app, apm_config={"SERVICE_NAME": "test"})
    factory.assert_called_once_with({"SERVICE_NAME": "test"})
    assert app.user_middleware[0].cls is ElasticAPM
    assert app.user_middleware[0].kwargs == {"client": client}
    factory.reset_mock()
    configure_middlewares(FastAPI(), apm_client=client)
    factory.assert_not_called()


def test_custom_apm_middleware_is_injected_without_importing_an_implementation():
    client = object()
    app = FastAPI()
    configure_middlewares(app, apm_client=client, apm_middleware_class=MarkerMiddleware)
    assert app.user_middleware[0].cls is MarkerMiddleware
    assert app.user_middleware[0].kwargs == {"client": client}
