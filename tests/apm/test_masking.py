import elasticapm
import pytest
from elasticapm.conf.constants import BASE_SANITIZE_FIELD_NAMES, MASK
from pydantic import BaseModel
from pytest import MonkeyPatch

from fastapi_extras.apm.constants import MAX_FIELD_LENGTH, TRUNCATED_SUFFIX
from fastapi_extras.apm.masking import (
    get_sanitize_field_names,
    is_sensitive_key,
    mask_sensitive_data,
    normalize_span_value,
    parse_json,
    sanitize,
    truncate,
)
from tests.apm.fakes import Unprintable


class Credentials(BaseModel):
    username: str
    password: str


def test_field_names_fall_back_to_agent_defaults_without_client(monkeypatch: MonkeyPatch):
    monkeypatch.setattr(elasticapm, "get_client", lambda: None)

    assert get_sanitize_field_names() is BASE_SANITIZE_FIELD_NAMES


def test_field_names_come_from_client_config(apm_client):
    assert get_sanitize_field_names() is apm_client.config.sanitize_field_names


def test_is_sensitive_key_supports_wildcards_case_and_spaces(field_names):
    assert is_sensitive_key("id_token", field_names)
    assert is_sensitive_key(" Authorization ", field_names)
    assert is_sensitive_key("CPF", field_names)
    assert not is_sensitive_key("name", field_names)
    assert not is_sensitive_key(10, field_names)


def test_sanitize_masks_nested_structures(field_names):
    data = {
        "user": {"name": "Ana", "cpf": {"number": "123"}},
        "items": [{"access_token": "abc"}, ("x", {"password": "p"})],
        "count": 2,
    }

    assert sanitize(data, field_names) == {
        "user": {"name": "Ana", "cpf": MASK},
        "items": [{"access_token": MASK}, ["x", {"password": MASK}]],
        "count": 2,
    }


def test_truncate_limits_long_values_and_keeps_scalars():
    long_text = "a" * (MAX_FIELD_LENGTH + 1)

    result = truncate({"text": long_text, "items": [1, 2.5, True, None], "obj": ("b",)})

    assert result["text"] == "a" * MAX_FIELD_LENGTH + TRUNCATED_SUFFIX
    assert result["items"] == [1, 2.5, True, None]
    assert result["obj"] == ["b"]
    assert truncate(Credentials(username="u", password="p"), 5) == "usern" + TRUNCATED_SUFFIX


def test_normalize_span_value_handles_models_and_bytes():
    assert normalize_span_value(Credentials(username="u", password="p")) == {
        "username": "u",
        "password": "p",
    }
    assert normalize_span_value(b"hello") == "hello"
    assert normalize_span_value(bytearray(b"\xff")) == "/w=="
    assert normalize_span_value(42) == 42


def test_parse_json_returns_original_text_when_invalid():
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json("not json") == "not json"


def test_mask_sensitive_data_parses_json_payloads(field_names):
    body = b'{"access_token": "abc", "user": {"name": "Ana"}}'

    assert mask_sensitive_data(body, field_names) == {
        "access_token": MASK,
        "user": {"name": "Ana"},
    }
    assert mask_sensitive_data('[{"cpf": "1"}]', field_names) == [{"cpf": MASK}]
    assert mask_sensitive_data("plain text", field_names) == "plain text"


@pytest.mark.usefixtures("apm_client")
def test_mask_sensitive_data_uses_client_field_names():
    assert mask_sensitive_data({"cpf": "123"}) == {"cpf": MASK}


def test_mask_sensitive_data_hides_values_it_cannot_process(field_names):
    assert mask_sensitive_data({"value": Unprintable()}, field_names) == MASK
