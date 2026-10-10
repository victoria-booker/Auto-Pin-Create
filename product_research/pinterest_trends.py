"""
Pinterest Trends (official API v5) — replaces the blocked typeahead scrape.

Unlike the other providers, Pinterest's Trends API isn't seed-driven: it
returns the top growing/trending search keywords for a region, not
suggestions for a specific query. So pinterest_trend_suggest(query) fetches
(and caches) that regional list once, then filters it down to entries that
share a word with the query — same list-of-strings interface the other
providers use, so it drops straight into PROVIDERS.

Requires Pinterest to authorize this app for Trends API access. The official
Trends documentation describes availability for agencies, Enterprise clients,
and partner platforms; Standard API access alone is not documented as
sufficient. Confirm access by checking the granted token scopes and making a
live request. A denied request is surfaced by the API route as a permission
error, while optional idea-suggestion calls fail soft.

Schema caveat: Pinterest's docs describe each trend entry as having a
"keyword" field, but this hasn't been exercised against a live trends-scoped
token yet. _extract_keyword() below checks a few likely field names and
falls back gracefully — if it comes back empty once you have a real token,
print a raw entry (see the except block) and the field name is easy to fix.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from pinterest import pinterest_pins  # noqa: E402
from pinterest.runtime_config import PINTEREST_API_BASE  # noqa: E402

import requests  # noqa: E402

get_access_token = pinterest_pins.get_access_token

REGION = os.environ.get("PINTEREST_TRENDS_REGION", "US")
TREND_TYPE = os.environ.get("PINTEREST_TRENDS_TYPE", "growing")  # "growing" or "monthly"
CACHE_TTL_SECONDS = 3600

_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}


class PinterestTrendsError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        super().__init__(detail)


def _extract_keyword(entry: dict) -> str | None:
    for key in ("keyword", "term", "name", "query"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def get_pinterest_trends(
    region: str = REGION,
    trend_type: str = TREND_TYPE,
    limit: int = 50,
    timeout: int = 10,
) -> list[dict]:
    if trend_type not in {"growing", "monthly"}:
        raise ValueError("trend_type must be 'growing' or 'monthly'.")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50.")

    cache_key = (region.upper(), trend_type, limit)
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    url = f"{PINTEREST_API_BASE}/trends/keywords/{region.upper()}/top/{trend_type}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {get_access_token()}"},
        params={"limit": limit},
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        raise PinterestTrendsError(response.status_code, "Pinterest rejected this token for Trends access.")
    response.raise_for_status()

    payload = response.json()
    entries = payload.get("trends") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Pinterest Trends returned an unexpected response; expected a 'trends' list.")

    trends = [entry for entry in entries if isinstance(entry, dict) and _extract_keyword(entry)]
    if entries and not trends:
        raise ValueError("Pinterest Trends returned entries without a keyword field.")
    _cache[cache_key] = (time.time(), trends)
    return trends


def pinterest_trend_suggest(query: str, timeout: int = 6) -> list[str]:
    try:
        top_trends = get_pinterest_trends(REGION, TREND_TYPE, timeout=timeout)
    except (PinterestTrendsError, requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"[suggest:pinterest] trends fetch failed for {REGION}/{TREND_TYPE}: {exc}")
        return []
    query_words = set(query.lower().split())
    return [
        keyword
        for trend in top_trends
        if (keyword := _extract_keyword(trend)) and query_words & set(keyword.lower().split())
    ]
