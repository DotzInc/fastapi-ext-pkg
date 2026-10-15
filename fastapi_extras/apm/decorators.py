import functools
import inspect
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Tuple, TypeVar
from urllib.parse import urlparse

import elasticapm
from elasticapm.conf.constants import MASK

from fastapi_extras.apm.constants import EXTERNAL, UNKNOWN, UNKNOWN_STATUS_CODE, UNREADABLE_RESPONSE
from fastapi_extras.apm.labels import to_label_value
from fastapi_extras.apm.masking import (
    FieldNames,
    get_sanitize_field_names,
    is_sensitive_key,
    mask_sensitive_data,
    normalize_span_value,
)

ReturnType = TypeVar("ReturnType")

ERROR_BODY_ATTRIBUTES = ("errors", "content", "detail")


def get_span_name(method: str, url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return f"{method.upper()} {path}"


def is_outgoing_request(value: Any) -> bool:
    return isinstance(getattr(value, "method", None), str) and isinstance(
        getattr(value, "url", None), str
    )


def find_outgoing_request(args: Tuple[Any, ...], kwargs: Mapping[str, Any]) -> Any:
    candidate = kwargs.get("request")
    if is_outgoing_request(candidate):
        return candidate
    return next((arg for arg in args if is_outgoing_request(arg)), None)


async def read_response(response: Any) -> Tuple[Optional[int], Any]:
    status_code = getattr(response, "status", None)
    if not isinstance(status_code, int):
        status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int):
        return None, None
    try:
        text = response.text
        body = text() if callable(text) else text
        if inspect.isawaitable(body):
            body = await body
    except Exception:
        body = UNREADABLE_RESPONSE
    return status_code, body


def read_error(error: Exception) -> Tuple[int, Any]:
    status_code = getattr(error, "status_code", None)
    if not isinstance(status_code, int):
        return UNKNOWN_STATUS_CODE, repr(error)
    body = {}
    for attribute in ERROR_BODY_ATTRIBUTES:
        value = getattr(error, attribute, None)
        if value:
            body[attribute] = value
    return status_code, body


def build_span_labels(
    status_code: Optional[int],
    response_body: Any,
    payload: Any,
    extra_labels: Optional[Mapping[str, Any]] = None,
    field_names: Optional[FieldNames] = None,
) -> Dict[str, Any]:
    names = get_sanitize_field_names() if field_names is None else field_names
    labels: Dict[str, Any] = {
        "status_code": status_code,
        "response_body": normalize_span_value(response_body),
    }
    if payload:
        labels["payload"] = normalize_span_value(payload)
    if extra_labels:
        labels.update(extra_labels)
    return {
        key: MASK
        if is_sensitive_key(key, names)
        else to_label_value(mask_sensitive_data(value, names))
        for key, value in labels.items()
    }


def capture_http_request(
    func: Callable[..., Awaitable[ReturnType]],
) -> Callable[..., Awaitable[ReturnType]]:
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> ReturnType:
        request = find_outgoing_request(args, kwargs)
        method = getattr(request, "method", UNKNOWN)
        url = getattr(request, "url", UNKNOWN)
        payload = getattr(request, "payload", None)
        status_code: Optional[int] = None
        response_body: Any = None

        async with elasticapm.async_capture_span(
            get_span_name(method, url), span_type=EXTERNAL
        ) as span:
            try:
                response = await func(*args, **kwargs)
                status_code, response_body = await read_response(response)
                return response
            except Exception as error:
                status_code, response_body = read_error(error)
                raise
            finally:
                if span:
                    span.label(
                        **build_span_labels(
                            status_code, response_body, payload, kwargs.get("labels")
                        )
                    )

    return wrapper
