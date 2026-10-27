from unittest.mock import Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from fastapi_extras.middlewares import MiddlewareSpec, configure_middlewares, garbage_collector
from fastapi_extras.middlewares.garbage_collector import GarbageCollectorMiddleware
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


def test_garbage_collector_is_disabled_by_default():
    app = FastAPI()
    configure_middlewares(app, cors={"allow_origins": ["*"]})
    assert GarbageCollectorMiddleware not in [m.cls for m in app.user_middleware]


def test_garbage_collector_is_the_outermost_middleware(monkeypatch):
    fake_gc = Mock()
    monkeypatch.setattr(garbage_collector, "gc", fake_gc)
    app = FastAPI()

    @app.get("/hello")
    async def hello():
        return {"ok": True}

    configure_middlewares(
        app,
        root_path="/service",
        cors={"allow_origins": ["https://example.com"]},
        garbage_collector=True,
        extra_middlewares=(MiddlewareSpec("marker", MarkerMiddleware),),
    )
    assert [m.cls for m in app.user_middleware] == [
        GarbageCollectorMiddleware,
        CORSMiddleware,
        MarkerMiddleware,
        RemoveCloudTraceHeadersMiddleware,
        RemovePathMiddleware,
    ]
    with TestClient(app) as client:
        response = client.get("/service/hello", headers={"origin": "https://example.com"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://example.com"
    fake_gc.freeze.assert_called_once()


def test_garbage_collector_options_can_be_overridden():
    app = FastAPI()
    configure_middlewares(
        app,
        remove_cloud_trace_headers=False,
        garbage_collector=True,
        overrides=(
            MiddlewareSpec(
                "garbage_collector", GarbageCollectorMiddleware, {"every_requests": 100}
            ),
        ),
    )
    assert app.user_middleware[0].kwargs == {"every_requests": 100}


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


@pytest.mark.parametrize(
    "invalid_options, message",
    [
        (
            {"apm_config": {"SERVICE_NAME": "test"}, "apm_client": object()},
            "Pass either apm_config or apm_client",
        ),
        (
            {"extra_middlewares": (MiddlewareSpec("cors", MarkerMiddleware),)},
            "Duplicate middleware name: cors",
        ),
        (
            {"extra_middlewares": (MiddlewareSpec("garbage_collector", MarkerMiddleware),)},
            "Duplicate middleware name: garbage_collector",
        ),
        (
            {
                "extra_middlewares": (
                    MiddlewareSpec("marker", MarkerMiddleware),
                    MiddlewareSpec("marker", MarkerMiddleware),
                )
            },
            "Duplicate middleware name: marker",
        ),
        (
            {"overrides": (MiddlewareSpec("missing", MarkerMiddleware),)},
            "Unknown middleware override: missing",
        ),
    ],
)
def test_invalid_configuration_preserves_existing_stack_and_allows_retry(
    invalid_options, message, monkeypatch
):
    factory = Mock()
    monkeypatch.setattr("elasticapm.contrib.starlette.make_apm_client", factory)
    app = FastAPI()
    app.add_middleware(MarkerMiddleware, label="existing")
    existing = list(app.user_middleware)

    with pytest.raises(ValueError, match=message):
        configure_middlewares(app, **invalid_options)

    assert app.user_middleware == existing
    assert not getattr(app.state, "_fastapi_extras_middlewares_configured", False)
    factory.assert_not_called()

    configure_middlewares(app, root_path="/service")
    assert app.user_middleware[-1] is existing[0]
