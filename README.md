# FastAPI Extension Package

![Tests](https://github.com/DotzInc/fastapi-ext-pkg/actions/workflows/tests.yml/badge.svg?event=push)
![PyPI - Version](https://img.shields.io/pypi/v/fastapi-ext-pkg)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/fastapi-ext-pkg)

A well-tailored utility set for FastAPI.

## Requirements

* Python 3.8+

## Installation

To install FastAPI Extension Package, use pip:

```sh
pip install fastapi-ext-pkg
```

## Shared middleware configuration

`configure_middlewares` registers middleware on an existing FastAPI application
before startup. Configure service values centrally while preserving routers and
lifespan:

```python
from fastapi_extras.middlewares import configure_middlewares

configure_middlewares(
    app,
    root_path=settings.ROOT_PATH,
    apm_config=settings.ELASTIC_APM,
    cors={"allow_origins": settings.ALLOW_ORIGINS},
)
```

The default registration order is path removal, trace-header removal, APM,
`extra_middlewares`, then CORS. Incoming requests traverse it in reverse. Use
`order` to preserve a service-specific order; include every configured name
exactly once. `MiddlewareSpec` defines custom middleware and named `overrides`
for replacement or disabling (`enabled=False`).

Use `root_path=None`, `remove_cloud_trace_headers=False`, `apm_enabled=False`,
or `cors=None` to omit middleware. Pass either `apm_config` or `apm_client`.
APM uses the upstream `elasticapm.contrib.starlette.ElasticAPM` by default and
requires the optional `[apm]` dependency. This branch contains no custom APM
implementation. To register another implementation supplied by the caller, pass
`apm_middleware_class`; `apm_options` supplies its constructor options.
