"""Shared API-key auth for api.py's routers.

Each router is created with dependencies=[Depends(require_api_key)] so every
endpoint on it is checked without repeating the check in each function body.
"""
import os

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

API_KEY = os.environ["PIN_API_KEY"]  # your own key, not Pinterest's
_api_key_header = APIKeyHeader(name="X-API-Key")


def require_api_key(x_api_key: str = Depends(_api_key_header)) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
