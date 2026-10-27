from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Type

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from fastapi_extras.middlewares.remove_cloud_trace_headers import RemoveCloudTraceHeadersMiddleware
from fastapi_extras.middlewares.remove_path import RemovePathMiddleware


@dataclass(frozen=True)
class MiddlewareSpec:
    """Named middleware with constructor options and an explicit enabled flag."""

    name: str
    middleware_class: Type[Any]
    options: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True


def configure_middlewares(
    app: FastAPI,
    *,
    root_path: Optional[str] = None,
    remove_cloud_trace_headers: bool = True,
    apm_config: Optional[Mapping[str, Any]] = None,
    apm_client: Any = None,
    apm_enabled: bool = True,
    apm_options: Optional[Mapping[str, Any]] = None,
    apm_middleware_class: Optional[Type[Any]] = None,
    cors: Optional[Mapping[str, Any]] = None,
    extra_middlewares: Sequence[MiddlewareSpec] = (),
    overrides: Sequence[MiddlewareSpec] = (),
    order: Optional[Sequence[str]] = None,
) -> None:
    """Register middleware before startup, in add_middleware order.

    Requests traverse this order in reverse. Extra middleware defaults to before
    CORS. Overrides replace a named entry, including its enabled flag. None
    disables optional path/APM/CORS entries. Existing middleware is preserved.
    """
    if app.middleware_stack is not None:
        raise RuntimeError("Cannot configure middleware after application startup")
    if getattr(app.state, "_fastapi_extras_middlewares_configured", False):
        raise RuntimeError("Middleware has already been configured")
    if apm_config is not None and apm_client is not None:
        raise ValueError("Pass either apm_config or apm_client")

    specs = {}
    if root_path is not None:
        specs["remove_path"] = MiddlewareSpec(
            "remove_path", RemovePathMiddleware, {"path": root_path}
        )
    if remove_cloud_trace_headers:
        specs["remove_cloud_trace_headers"] = MiddlewareSpec(
            "remove_cloud_trace_headers", RemoveCloudTraceHeadersMiddleware
        )
    if apm_enabled and (apm_config is not None or apm_client is not None):
        if apm_middleware_class is None:
            from elasticapm.contrib.starlette import ElasticAPM

            apm_middleware_class = ElasticAPM
        specs["apm"] = MiddlewareSpec("apm", apm_middleware_class, dict(apm_options or {}))
    for spec in extra_middlewares:
        if spec.name in specs or spec.name in {
            "remove_path",
            "remove_cloud_trace_headers",
            "apm",
            "cors",
        }:
            raise ValueError("Duplicate middleware name: " + spec.name)
        specs[spec.name] = spec
    if cors is not None:
        specs["cors"] = MiddlewareSpec("cors", CORSMiddleware, dict(cors))
    for spec in overrides:
        if spec.name not in specs:
            raise ValueError("Unknown middleware override: " + spec.name)
        specs[spec.name] = spec

    names = list(specs) if order is None else list(order)
    if len(names) != len(set(names)) or set(names) != set(specs):
        raise ValueError("order must contain every configured middleware name exactly once")

    # Validate the full plan before creating a client or modifying the app.
    if "apm" in specs and specs["apm"].enabled:
        if apm_client is None:
            from elasticapm.contrib.starlette import make_apm_client

            apm_client = make_apm_client(dict(apm_config))
        spec = specs["apm"]
        specs["apm"] = MiddlewareSpec(
            spec.name, spec.middleware_class, {**spec.options, "client": apm_client}
        )
    for name in names:
        spec = specs[name]
        if spec.enabled:
            app.add_middleware(spec.middleware_class, **dict(spec.options))
    app.state._fastapi_extras_middlewares_configured = True
