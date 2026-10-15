import json
from typing import Any, Iterable, Mapping

import elasticapm

from fastapi_extras.apm.masking import get_sanitize_field_names, sanitize, truncate


def to_label_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def label_transaction(data: Mapping[str, Any], keys: Iterable[str]) -> None:
    labels = {key: data[key] for key in keys if data.get(key) is not None}
    if not labels:
        return
    safe_labels = truncate(sanitize(labels, get_sanitize_field_names()))
    elasticapm.label(**{key: to_label_value(value) for key, value in safe_labels.items()})
