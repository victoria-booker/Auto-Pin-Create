import os

try:
    from pinterest.pinterest_config import (
        CLIENT_ID as _LOCAL_CLIENT_ID,
        CLIENT_SECRET as _LOCAL_CLIENT_SECRET,
        PINTEREST_ENV as _LOCAL_PINTEREST_ENV,
    )
except ModuleNotFoundError:
    _LOCAL_CLIENT_ID = ""
    _LOCAL_CLIENT_SECRET = ""
    _LOCAL_PINTEREST_ENV = "production"

CLIENT_ID = os.environ.get("PINTEREST_CLIENT_ID", _LOCAL_CLIENT_ID)
CLIENT_SECRET = os.environ.get("PINTEREST_CLIENT_SECRET", _LOCAL_CLIENT_SECRET)
PINTEREST_ENV = os.environ.get("PINTEREST_ENV", _LOCAL_PINTEREST_ENV).strip().lower()

if PINTEREST_ENV not in {"sandbox", "production"}:
    raise ValueError("PINTEREST_ENV must be 'sandbox' or 'production'")

PINTEREST_API_BASE = (
    "https://api-sandbox.pinterest.com/v5"
    if PINTEREST_ENV == "sandbox"
    else "https://api.pinterest.com/v5"
)