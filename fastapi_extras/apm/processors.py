import json
from typing import Any, Dict, Optional

from elasticapm.conf.constants import ERROR, MASK, SPAN, TRANSACTION
from elasticapm.processors import for_events

from fastapi_extras.apm.masking import FieldNames, is_sensitive_key, sanitize

PROCESSORS = (
    "fastapi_extras.apm.processors.sanitize_http_request_json_body",
    "fastapi_extras.apm.processors.sanitize_custom_context",
    "fastapi_extras.apm.processors.sanitize_labels",
)


def load_json_structure(value: Any) -> Optional[Any]:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, (str, bytes, bytearray)):
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def sanitize_label(key: str, value: Any, field_names: FieldNames) -> Any:
    if is_sensitive_key(key, field_names):
        return MASK
    if not isinstance(value, str):
        return value
    parsed = load_json_structure(value)
    if parsed is None:
        return value
    return json.dumps(sanitize(parsed, field_names), ensure_ascii=False)


@for_events(ERROR, TRANSACTION)
def sanitize_http_request_json_body(client: Any, event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        body = event["context"]["request"]["body"]
    except (KeyError, TypeError):
        return event
    parsed = load_json_structure(body)
    if parsed is None:
        return event
    event["context"]["request"]["body"] = json.dumps(
        sanitize(parsed, client.config.sanitize_field_names)
    )
    return event


@for_events(ERROR, TRANSACTION)
def sanitize_custom_context(client: Any, event: Dict[str, Any]) -> Dict[str, Any]:
    custom = (event.get("context") or {}).get("custom")
    if isinstance(custom, dict):
        event["context"]["custom"] = sanitize(custom, client.config.sanitize_field_names)
    return event


@for_events(SPAN, TRANSACTION)
def sanitize_labels(client: Any, event: Dict[str, Any]) -> Dict[str, Any]:
    tags = (event.get("context") or {}).get("tags")
    if not isinstance(tags, dict):
        return event
    field_names = client.config.sanitize_field_names
    event["context"]["tags"] = {
        key: sanitize_label(key, value, field_names) for key, value in tags.items()
    }
    return event
