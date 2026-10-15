import elasticapm
from elasticapm.conf.constants import MASK
from pytest import MonkeyPatch

from fastapi_extras.apm.labels import label_transaction, to_label_value


def test_to_label_value_serializes_structures():
    assert to_label_value({"a": "ã"}) == '{"a": "ã"}'
    assert to_label_value([1, 2]) == "[1, 2]"
    assert to_label_value("text") == "text"
    assert to_label_value(10) == 10


def test_label_transaction_labels_only_present_keys(apm_client):
    apm_client.begin_transaction("request")

    label_transaction(
        {"sub": "user-1", "cpf": "123", "borrower_id": None, "scopes": ["a"]},
        ("sub", "cpf", "borrower_id", "scopes", "missing"),
    )

    apm_client.end_transaction("GET /", "HTTP 2xx")
    [transaction] = apm_client._transport.events_of("transaction")
    assert transaction["context"]["tags"] == {"sub": "user-1", "cpf": MASK, "scopes": '["a"]'}


def test_label_transaction_skips_when_nothing_to_label(monkeypatch: MonkeyPatch):
    calls = []
    monkeypatch.setattr(elasticapm, "label", lambda **labels: calls.append(labels))

    label_transaction({"sub": None}, ("sub",))

    assert calls == []
