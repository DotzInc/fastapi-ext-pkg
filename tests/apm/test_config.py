from typing import Any, Dict

import fastapi
import pytest
from elasticapm.conf.constants import MASK
from elasticapm.utils.module_import import import_string
from fastapi import FastAPI

from fastapi_extras.apm import build_apm_config
from fastapi_extras.apm.config import has_credentials, parse_field_names, unique
from fastapi_extras.apm.constants import DEFAULT_SANITIZE_FIELD_NAMES, RESPONSE_BODY_CONTEXT_KEY
from fastapi_extras.apm.middleware import ElasticAPMMiddleware
from fastapi_extras.apm.processors import AGENT_PROCESSORS, ALL_PROCESSORS, PROCESSORS

AGENT_DEFAULT_FIELD_NAMES = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "*key",
    "*token*",
    "*session*",
    "*credit*",
    "*card*",
    "*auth*",
    "*principal*",
    "set-cookie",
}


def test_builds_the_shared_settings_shape():
    config = build_apm_config(
        "svc", server_url="http://apm:8200", secret_token="token", environment="uat"
    )

    assert config == {
        "ENABLED": True,
        "SERVICE_NAME": "svc",
        "SECRET_TOKEN": "token",
        "SERVER_URL": "http://apm:8200",
        "ENVIRONMENT": "uat",
        "LOG_LEVEL": "INFO",
        "CLOUD_PROVIDER": "gcp",
        "FRAMEWORK_NAME": "fastapi",
        "FRAMEWORK_VERSION": fastapi.__version__,
        "CAPTURE_HEADERS": True,
        "CAPTURE_BODY": "off",
        "SANITIZE_FIELD_NAMES": list(DEFAULT_SANITIZE_FIELD_NAMES),
        "PROCESSORS": ALL_PROCESSORS,
    }


@pytest.mark.parametrize(
    ("server_url", "secret_token", "expected"),
    [
        ("http://apm:8200", "token", True),
        ("http://apm:8200", "  ", False),
        ("", "token", False),
        (None, None, False),
    ],
)
def test_enabled_follows_credentials_by_default(server_url, secret_token, expected):
    config = build_apm_config("svc", server_url=server_url, secret_token=secret_token)

    assert config["ENABLED"] is expected
    assert config["SECRET_TOKEN"] == (secret_token or "")
    assert config["SERVER_URL"] == (server_url or "")


@pytest.mark.parametrize("enabled", [True, False])
def test_explicit_enabled_wins_over_credentials(enabled):
    config = build_apm_config(
        "svc", server_url="http://apm:8200", secret_token="token", enabled=enabled
    )

    assert config["ENABLED"] is enabled


def test_has_credentials_requires_both_values():
    assert has_credentials("http://apm", "token")
    assert not has_credentials("http://apm", None)
    assert not has_credentials(None, "token")


def test_default_field_names_keep_agent_defaults_and_add_personal_data():
    names = set(DEFAULT_SANITIZE_FIELD_NAMES)

    assert AGENT_DEFAULT_FIELD_NAMES <= names
    assert {"*cpf*", "*email*", "*phone*", "*mobile*", "*document*", "*birth*", "pin"} <= names
    assert len(names) == len(DEFAULT_SANITIZE_FIELD_NAMES)


def test_field_names_accept_env_style_strings():
    config = build_apm_config("svc", sanitize_field_names=" cpf, ,Email,cpf ,")

    assert config["SANITIZE_FIELD_NAMES"] == ["cpf", "Email"]


def test_explicit_field_names_replace_the_defaults():
    config = build_apm_config("svc", sanitize_field_names=["cpf", "token"])

    assert config["SANITIZE_FIELD_NAMES"] == ["cpf", "token"]


def test_extra_field_names_are_appended_without_duplicates():
    config = build_apm_config("svc", extra_sanitize_field_names="zipcode,pin, zipcode")

    names = config["SANITIZE_FIELD_NAMES"]
    assert names[: len(DEFAULT_SANITIZE_FIELD_NAMES)] == list(DEFAULT_SANITIZE_FIELD_NAMES)
    assert names[-1] == "zipcode"
    assert names.count("pin") == 1
    assert names.count("zipcode") == 1


def test_extra_processors_run_after_the_shared_ones():
    config = build_apm_config("svc", extra_processors=["my.app.processor"])

    assert config["PROCESSORS"] == (*ALL_PROCESSORS, "my.app.processor")


def test_overrides_win_and_accept_lowercase_keys():
    config = build_apm_config(
        "svc", capture_body="all", service_version="1.2.3", CAPTURE_HEADERS=False
    )

    assert config["CAPTURE_BODY"] == "all"
    assert config["SERVICE_VERSION"] == "1.2.3"
    assert config["CAPTURE_HEADERS"] is False


def test_helpers_normalize_inputs():
    assert parse_field_names("a, b,,") == ["a", "b"]
    assert parse_field_names(("a", " ", "b ")) == ["a", "b"]
    assert unique(["b", "a", "b"]) == ["b", "a"]


def test_all_processors_compose_shared_and_agent_processors():
    assert ALL_PROCESSORS == (*PROCESSORS, *AGENT_PROCESSORS)
    assert len(set(ALL_PROCESSORS)) == len(ALL_PROCESSORS)
    for path in ALL_PROCESSORS:
        assert callable(import_string(path))


def test_config_drives_a_real_agent_client(make_client):
    config = build_apm_config(
        "svc", server_url="http://localhost:8200", secret_token="t", capture_body="all"
    )
    client = make_client(**config)

    names = [pattern.pattern for pattern in client.config.sanitize_field_names]
    assert "(?:.*cpf.*)\\Z" in names
    assert "(?:password)\\Z" in names
    assert tuple(client.config.processors) == ALL_PROCESSORS
    loaded = {processor.__name__ for processor in client.load_processors()}
    assert {path.rsplit(".", 1)[-1] for path in ALL_PROCESSORS} <= loaded


@pytest.mark.anyio
async def test_config_masks_personal_data_through_the_middleware(make_client):
    service = FastAPI()

    @service.post("/people")
    async def people(_payload: Dict[str, Any]):
        return {"owner": {"cpf": "123", "email": "a@b.c", "mobile_number": "11", "name": "Ana"}}

    client = make_client(
        **build_apm_config(
            "svc", server_url="http://localhost:8200", enabled=True, capture_body="all"
        )
    )
    middleware = ElasticAPMMiddleware(service, client=client)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b'{"cpf": "123", "pin": "9"}', "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/people",
        "raw_path": b"/people",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"testserver")],
        "client": ("127.0.0.1", 5000),
        "server": ("testserver", 80),
        "app": service,
    }
    await middleware(scope, receive, send)

    client.end_transaction()
    [transaction] = client._transport.events_of("transaction")
    assert transaction["context"]["custom"][RESPONSE_BODY_CONTEXT_KEY] == {
        "owner": {"cpf": MASK, "email": MASK, "mobile_number": MASK, "name": "Ana"}
    }
    assert '"cpf": "[REDACTED]"' in transaction["context"]["request"]["body"]
    assert '"pin": "[REDACTED]"' in transaction["context"]["request"]["body"]
