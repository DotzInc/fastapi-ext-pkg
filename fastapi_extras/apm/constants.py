EXTERNAL = "external"
UNKNOWN = "UNKNOWN"
UNKNOWN_STATUS_CODE = 999
MAX_FIELD_LENGTH = 500
MAX_RESPONSE_BODY_BYTES = 64 * 1024
TRUNCATED_SUFFIX = "… [TRUNCATED]"
UNREADABLE_RESPONSE = "<could not decode response>"
RESPONSE_BODY_TOO_LARGE = "<response body too large>"
RESPONSE_BODY_CONTEXT_KEY = "response_body"
JSON_MEDIA_TYPE = "application/json"
JSON_MEDIA_TYPE_SUFFIX = "+json"
IDENTITY_ENCODING = "identity"
DEFAULT_SANITIZE_FIELD_NAMES = (
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
    "pin",
    "otp",
    "*document*",
    "*cpf*",
    "*cnpj*",
    "*email*",
    "*phone*",
    "*mobile*",
    "*birth*",
    "mother_name",
    "rg",
    "*account_number*",
    "agency",
    "pix_key",
    "*cvv*",
    "*salary*",
    "*income*",
)
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_CAPTURE_BODY = "off"
DEFAULT_CLOUD_PROVIDER = "gcp"
