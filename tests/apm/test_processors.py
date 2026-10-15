import json

import pytest
from elasticapm.conf.constants import ERROR, MASK, SPAN, TRANSACTION
from elasticapm.utils.module_import import import_string

from fastapi_extras.apm.processors import (
    PROCESSORS,
    load_json_structure,
    sanitize_custom_context,
    sanitize_http_request_json_body,
    sanitize_labels,
)


@pytest.fixture
def client(apm_client):
    return apm_client


def test_processors_are_importable_by_dotted_path():
    processors = [import_string(path) for path in PROCESSORS]

    assert processors == [sanitize_http_request_json_body, sanitize_custom_context, sanitize_labels]
    assert sanitize_http_request_json_body.event_types == {ERROR, TRANSACTION}
    assert sanitize_custom_context.event_types == {ERROR, TRANSACTION}
    assert sanitize_labels.event_types == {SPAN, TRANSACTION}


def test_load_json_structure_accepts_only_objects_and_arrays():
    assert load_json_structure({"a": 1}) == {"a": 1}
    assert load_json_structure(b"[1]") == [1]
    assert load_json_structure("12") is None
    assert load_json_structure("{broken") is None
    assert load_json_structure(12) is None


@pytest.mark.parametrize(
    "body",
    ['{"password": "p", "user": {"cpf": "1"}}', {"password": "p", "user": {"cpf": "1"}}],
)
def test_sanitize_http_request_json_body_masks_json(client, body):
    event = {"context": {"request": {"body": body}}}

    result = sanitize_http_request_json_body(client, event)

    assert json.loads(result["context"]["request"]["body"]) == {
        "password": MASK,
        "user": {"cpf": MASK},
    }


@pytest.mark.parametrize(
    "event",
    [{}, {"context": None}, {"context": {"request": {"body": "a=1&b=2"}}}],
)
def test_sanitize_http_request_json_body_ignores_other_bodies(client, event):
    assert sanitize_http_request_json_body(client, event) == event


def test_sanitize_custom_context_masks_response_body(client):
    event = {"context": {"custom": {"response_body": {"access_token": "a", "ok": True}}}}

    result = sanitize_custom_context(client, event)

    assert result["context"]["custom"] == {"response_body": {"access_token": MASK, "ok": True}}
    assert sanitize_custom_context(client, {"context": None}) == {"context": None}


def test_sanitize_labels_masks_keys_and_json_values(client):
    event = {
        "context": {
            "tags": {
                "authorization": "Bearer x",
                "response_body": '{"id_token": "t", "name": "Ana"}',
                "message": "plain",
                "status_code": 200,
            }
        }
    }

    tags = sanitize_labels(client, event)["context"]["tags"]

    assert tags["authorization"] == MASK
    assert json.loads(tags["response_body"]) == {"id_token": MASK, "name": "Ana"}
    assert tags["message"] == "plain"
    assert tags["status_code"] == 200
    assert sanitize_labels(client, {}) == {}
