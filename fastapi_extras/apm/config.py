from typing import Any, Dict, Iterable, List, Optional, Union

import fastapi

from fastapi_extras.apm.constants import (
    DEFAULT_CAPTURE_BODY,
    DEFAULT_CLOUD_PROVIDER,
    DEFAULT_LOG_LEVEL,
    DEFAULT_SANITIZE_FIELD_NAMES,
)
from fastapi_extras.apm.processors import ALL_PROCESSORS

FieldNamesInput = Union[str, Iterable[str]]


def parse_field_names(value: FieldNamesInput) -> List[str]:
    items = value.split(",") if isinstance(value, str) else value
    return [item.strip() for item in items if item.strip()]


def unique(items: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(items))


def has_credentials(server_url: Optional[str], secret_token: Optional[str]) -> bool:
    return bool((server_url or "").strip() and (secret_token or "").strip())


def build_apm_config(
    service_name: str,
    *,
    server_url: Optional[str] = "",
    secret_token: Optional[str] = "",
    environment: str = "",
    enabled: Optional[bool] = None,
    log_level: str = DEFAULT_LOG_LEVEL,
    capture_body: str = DEFAULT_CAPTURE_BODY,
    sanitize_field_names: Optional[FieldNamesInput] = None,
    extra_sanitize_field_names: FieldNamesInput = (),
    extra_processors: Iterable[str] = (),
    **overrides: Any,
) -> Dict[str, Any]:
    base_field_names = (
        DEFAULT_SANITIZE_FIELD_NAMES
        if sanitize_field_names is None
        else parse_field_names(sanitize_field_names)
    )
    config: Dict[str, Any] = {
        "ENABLED": has_credentials(server_url, secret_token) if enabled is None else enabled,
        "SERVICE_NAME": service_name,
        "SECRET_TOKEN": secret_token or "",
        "SERVER_URL": server_url or "",
        "ENVIRONMENT": environment,
        "LOG_LEVEL": log_level,
        "CLOUD_PROVIDER": DEFAULT_CLOUD_PROVIDER,
        "FRAMEWORK_NAME": "fastapi",
        "FRAMEWORK_VERSION": fastapi.__version__,
        "CAPTURE_HEADERS": True,
        "CAPTURE_BODY": capture_body,
        "SANITIZE_FIELD_NAMES": unique(
            [*base_field_names, *parse_field_names(extra_sanitize_field_names)]
        ),
        "PROCESSORS": (*ALL_PROCESSORS, *extra_processors),
    }
    config.update({key.upper(): value for key, value in overrides.items()})
    return config
