"""
Platform autocomplete/suggest scrapers used for product-idea research.

Each provider takes a query string and returns a list[str] of suggested
phrases. None of these are official public APIs, so every provider fails
soft: on a timeout, non-200, or unexpected payload it returns [] and prints
a one-line warning instead of raising, so one flaky platform never breaks
the rest of the pipeline.

Reliability, as verified against the live endpoints:
  - amazon_suggest / youtube_suggest: stable, unauthenticated public JSON
    endpoints. No cookies or signing required.
  - etsy_suggest: Etsy itself is a dead end two ways over — the official
    Open API v3 app was declined on review, and the frontend
    suggestions_ajax.php scrape is blocked by DataDome bot-protection
    (confirmed live: 403 with a JS challenge page). Instead this reuses
    Google's and Bing's own autocomplete, scoped with an "etsy " prefix —
    free, no signup, no rate limiting observed. It's a proxy signal, not
    Etsy's own suggestion data, but confirmed live to return genuine
    Etsy-flavored buyer phrases (e.g. "etsy dog bandana custom",
    "etsy coloring books for adults").
    - pinterest_suggest: uses the official Pinterest Trends API (v5) via your
        existing Pinterest OAuth token — see pinterest_trends.py. Pinterest's
        Trends documentation describes availability for agencies, Enterprise
        clients, and partner platforms; Standard API access alone is not a
        documented guarantee of Trends access.
  - tiktok_suggest: TikTok's suggest endpoint requires signed request
    params (X-Bogus/msToken) that a stateless script cannot produce. Left
    in place as a hook — swap in a headless-browser session or a paid data
    API if reliable TikTok coverage matters.
  - google_trends_suggest: the only provider here that reflects actual
    trend/spike signal (rising related queries) rather than steady-state
    completion. No official API — uses pytrends against Google's internal
    endpoints, which rate-limits hard and unpredictably (confirmed live:
    2 of 3 back-to-back calls 429'd). Retries with backoff, but treat gaps
    as expected, not a bug. See google_trends.py.
"""
import json

import requests

from product_research.google_trends import google_trends_suggest
from product_research.pinterest_trends import pinterest_trend_suggest

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

DEFAULT_TIMEOUT = 6


def _warn(platform: str, query: str, reason: str) -> None:
    print(f"[suggest:{platform}] '{query}' -> no results ({reason})")


def amazon_suggest(query: str, marketplace: str = "ATVPDKIKX0DER", timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    url = "https://completion.amazon.com/api/2017/suggestions"
    params = {"mid": marketplace, "alias": "aps", "prefix": query}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return [s["value"] for s in data.get("suggestions", []) if s.get("value")]
    except (requests.RequestException, json.JSONDecodeError, KeyError) as exc:
        _warn("amazon", query, str(exc))
        return []


def youtube_suggest(query: str, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    url = "http://suggestqueries.google.com/complete/search"
    params = {"client": "firefox", "ds": "yt", "q": query}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return [s for s in data[1] if isinstance(s, str)]
    except (requests.RequestException, json.JSONDecodeError, IndexError, TypeError) as exc:
        _warn("youtube", query, str(exc))
        return []


def etsy_suggest(query: str, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    prefixed = f"etsy {query}"
    suggestions = []

    try:
        resp = requests.get(
            "http://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "q": prefixed},
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        suggestions.extend(s for s in data[1] if isinstance(s, str))
    except (requests.RequestException, json.JSONDecodeError, IndexError, TypeError) as exc:
        _warn("etsy", query, f"google suggest failed: {exc}")

    try:
        resp = requests.get(
            "https://api.bing.com/osjson.aspx",
            params={"query": prefixed},
            headers=HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        suggestions.extend(s for s in data[1] if isinstance(s, str))
    except (requests.RequestException, json.JSONDecodeError, IndexError, TypeError) as exc:
        _warn("etsy", query, f"bing suggest failed: {exc}")

    return suggestions


def pinterest_suggest(query: str, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    return pinterest_trend_suggest(query, timeout=timeout)


def tiktok_suggest(query: str, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    url = "https://www.tiktok.com/api/suggest/word/"
    params = {"keyword": query, "aid": "1988", "app_name": "tiktok_web"}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        words = data.get("sug_list") or []
        return [w.get("word") for w in words if isinstance(w, dict) and w.get("word")]
    except (requests.RequestException, json.JSONDecodeError, AttributeError) as exc:
        _warn("tiktok", query, f"requires signed request params: {exc}")
        return []


PROVIDERS = {
    "amazon": amazon_suggest,
    "youtube": youtube_suggest,
    "etsy": etsy_suggest,
    "pinterest": pinterest_suggest,
    "tiktok": tiktok_suggest,
    "google_trends": google_trends_suggest,
}
