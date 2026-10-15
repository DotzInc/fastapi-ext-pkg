from typing import Any, Callable, Iterator, List

import elasticapm
import pytest
from elasticapm.utils import starmatch_to_regex

from fastapi_extras.apm.processors import PROCESSORS

SENSITIVE_FIELDS = ["password", "*token*", "authorization", "cpf"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def field_names() -> List[Any]:
    return [starmatch_to_regex(name) for name in SENSITIVE_FIELDS]


@pytest.fixture
def make_client() -> Iterator[Callable[..., elasticapm.Client]]:
    clients: List[elasticapm.Client] = []

    def factory(**overrides: Any) -> elasticapm.Client:
        config = {
            "SERVICE_NAME": "fastapi-ext-pkg-tests",
            "SERVER_URL": "http://localhost:8200",
            "TRANSPORT_CLASS": "tests.apm.fakes.CapturingTransport",
            "SANITIZE_FIELD_NAMES": SENSITIVE_FIELDS,
            "PROCESSORS": PROCESSORS,
            "INSTRUMENT": False,
            "CENTRAL_CONFIG": False,
            "METRICS_INTERVAL": "0ms",
            "CLOUD_PROVIDER": False,
            "DISABLE_LOG_RECORD_FACTORY": True,
            "SPAN_COMPRESSION_ENABLED": False,
            "CAPTURE_BODY": "all",
        }
        config.update(overrides)
        client = elasticapm.Client(config)
        clients.append(client)
        return client

    yield factory

    for client in clients:
        client.close()


@pytest.fixture
def apm_client(make_client: Callable[..., elasticapm.Client]) -> elasticapm.Client:
    return make_client()
