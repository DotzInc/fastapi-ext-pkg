import base64
import json
import logging
from typing import Any, Optional, Pattern, Sequence

import elasticapm
from elasticapm.conf.constants import BASE_SANITIZE_FIELD_NAMES, MASK
from pydantic import BaseModel

from fastapi_extras.apm.constants import MAX_FIELD_LENGTH, TRUNCATED_SUFFIX

logger = logging.getLogger(__name__)

FieldNames = Sequence[Pattern]


def get_sanitize_field_names() -> FieldNames:
    client = elasticapm.get_client()
    if client is None:
        return BASE_SANITIZE_FIELD_NAMES
    return client.config.sanitize_field_names


def is_sensitive_key(key: Any, field_names: FieldNames) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.strip().lower()
    return any(pattern.match(normalized) for pattern in field_names)


def sanitize(data: Any, field_names: FieldNames) -> Any:
    if isinstance(data, dict):
        return {
            key: MASK if is_sensitive_key(key, field_names) else sanitize(value, field_names)
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [sanitize(item, field_names) for item in data]
    return data


def truncate(data: Any, max_length: int = MAX_FIELD_LENGTH) -> Any:
    if isinstance(data, dict):
        return {key: truncate(value, max_length) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [truncate(item, max_length) for item in data]
    if data is None or isinstance(data, (bool, int, float)):
        return data
    text = data if isinstance(data, str) else str(data)
    if len(text) > max_length:
        return text[:max_length] + TRUNCATED_SUFFIX
    return text


def normalize_span_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        dump = getattr(value, "model_dump", None) or value.dict
        return dump()
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return base64.b64encode(bytes(value)).decode("ascii")
    return value


def parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError:
        return value


def mask_sensitive_data(
    data: Any,
    field_names: Optional[FieldNames] = None,
    max_length: int = MAX_FIELD_LENGTH,
) -> Any:
    names = get_sanitize_field_names() if field_names is None else field_names
    try:
        value = normalize_span_value(data)
        if isinstance(value, str):
            value = parse_json(value)
        return truncate(sanitize(value, names), max_length)
    except Exception:
        logger.exception("Could not mask APM data")
        return MASK
