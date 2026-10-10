import os

try:
    from research_config import APIFY_API_TOKEN as _LOCAL_APIFY_API_TOKEN
except ModuleNotFoundError:
    _LOCAL_APIFY_API_TOKEN = ""

APIFY_API_TOKEN = os.environ.get("APIFY_API_TOKEN", _LOCAL_APIFY_API_TOKEN)