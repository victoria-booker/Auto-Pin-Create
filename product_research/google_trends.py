"""
Google Trends "rising" related queries — an actual trend/spike signal,
unlike the autocomplete providers (which reflect steady-state search demand,
not what's currently climbing).

Deliberately RISING ONLY, not "top": pytrends also exposes a "top" table
(currently popular queries), but that's redundant with what Amazon/YouTube
autocomplete already tell you and would dilute the one signal this provider
is here for. "Rising" queries are sorted by growth magnitude (highest
first) so the biggest spikes lead; a query Google reports as a breakout
(new term, >5000% growth, no baseline to compute a percentage from) sorts
first of all.

There is no official Google Trends API. This uses pytrends, which scrapes
trends.google.com's internal endpoints. Confirmed live: it works, but Google
rate-limits it hard and unpredictably — 2 of 3 back-to-back requests (1s
apart) failed with 429 in testing. Retries with backoff below, but expect
gaps; this is the flakiest provider in the set, not a dependable feed.

pip install pytrends

---

google_trending_now() is a separate, seed-independent feed: Google's
general "what's trending right now" list (sports, celebrities, news —
NOT filtered to shopping/product-relevant topics). pytrends' own methods
for this (today_searches / trending_searches / realtime_trending_searches)
are dead — confirmed live, Google returns 404 on all three; those backend
endpoints were retired. This instead hits the RSS feed that Google's own
current Trending page uses (trends.google.com/trending/rss) directly via
requests — simpler than pytrends, and no rate-limiting observed over
several back-to-back calls in testing (unlike the seed-based endpoint
above).
"""
import time
from xml.etree import ElementTree

import requests
from pytrends.exceptions import TooManyRequestsError
from pytrends.request import TrendReq

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 3  # doubles each retry: 3s, 6s

_client = None


def _get_client() -> TrendReq:
    global _client
    if _client is None:
        _client = TrendReq(hl="en-US", tz=360, timeout=(5, 10))
    return _client


def _growth_sort_key(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")  # "Breakout" — beyond what a percentage can express


def google_trends_suggest(query: str, timeout: int = 10, geo: str = "US") -> list[str]:
    pytrends = _get_client()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            pytrends.build_payload([query], timeframe="today 3-m", geo=geo)
            rising = (pytrends.related_queries().get(query) or {}).get("rising")
            if rising is None or rising.empty:
                return []
            rising = rising.sort_values(by="value", key=lambda col: col.map(_growth_sort_key), ascending=False)
            return rising["query"].tolist()
        except TooManyRequestsError:
            if attempt == MAX_ATTEMPTS:
                print(f"[suggest:google_trends] '{query}' -> no results (rate-limited by Google after {MAX_ATTEMPTS} attempts)")
                return []
            time.sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))
        except Exception as exc:
            print(f"[suggest:google_trends] '{query}' -> no results ({exc})")
            return []

    return []


RSS_NAMESPACE = {"ht": "https://trends.google.com/trending/rss"}


def google_trending_now(geo: str = "US", timeout: int = 10) -> list[tuple[str, str]]:
    """General trending searches for a region, no seed keyword. Returns
    (title, approx_traffic) pairs, e.g. ("baker mayfield", "5000+")."""
    url = "https://trends.google.com/trending/rss"
    try:
        resp = requests.get(url, params={"geo": geo}, timeout=timeout)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        results = []
        for item in root.findall(".//item"):
            title = item.findtext("title")
            if not title:
                continue
            traffic = item.findtext("ht:approx_traffic", default="", namespaces=RSS_NAMESPACE)
            results.append((title, traffic))
        return results
    except (requests.RequestException, ElementTree.ParseError) as exc:
        print(f"[trending:google] no results for geo={geo} ({exc})")
        return []
