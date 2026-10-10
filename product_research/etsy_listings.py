"""
What's actually selling on Etsy right now — across POD physical products
AND digital products — via the Apify "Etsy Listings Scraper" actor
(yumitori/etsy-listings-scraper), not Etsy directly. Etsy's own site is
blocked outright by DataDome (confirmed live: every page we tried 403'd,
not just the suggest endpoint) — this actor gets past that with its own
proxies, same category of workaround as the keyword tool, just returning
real listing/sales signal instead of autosuggest text.

Not digital-only: the actor supports a digitalOnly filter, but that
excludes every physical product outright — no rugs, hats, mugs, shirts.
Since POD physical goods are most of what you sell, this leaves the filter
off by default (pass digital_only=True per-call if you want strictly
digital results for a given seed).

There's no "browse trending" call — Etsy search is keyword-only, so this
still needs seed terms. SEEDS below spans your actual POD + digital lines;
edit it to match what you actually want signal on.

Ranking logic: a listing is "trending" here if it's actually moving —
isBestseller/isStarSeller badges, a nonzero inCartCount (people have it in
their cart right now), and a recent lastSaleDate. This is deliberately not
just "sorted by whatever Etsy search returns first" — that's relevance/ads
ranking, not demand.

Needs an Apify API token: sign up at https://apify.com, get your token
from https://console.apify.com/account/integrations, then set
APIFY_API_TOKEN in your environment (or local research_config.py).

pip install apify-client
"""
from datetime import datetime, timedelta, timezone

from apify_client import ApifyClient

from product_research.research_runtime_config import APIFY_API_TOKEN

ACTOR_ID = "yumitori/etsy-listings-scraper"

# Broad sweep across POD physical products (rugs, hats, and anything else
# printable-on-demand) plus digital products generally — not narrowed to
# your current specific lines, so it surfaces adjacent niches too.
SEEDS = [
    "vintage shirt",
    "song lyric shirt",
    "funny mug",
    "trucker hat",
    "custom rug",
    "tumbler cup",
    "tote bag",
    "svg file",
    "printable planner",
    "digital download",
    "clipart bundle",
    "printable word search",
    "digital sticker",
]

RECENT_SALE_WINDOW_DAYS = 14


def _is_recent_sale(last_sale_date: str | None) -> bool:
    if not last_sale_date:
        return False
    try:
        sale_dt = datetime.fromisoformat(last_sale_date.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - sale_dt) <= timedelta(days=RECENT_SALE_WINDOW_DAYS)


def trend_score(listing: dict) -> int:
    score = 0
    if listing.get("isBestseller"):
        score += 3
    if listing.get("isStarSeller"):
        score += 1
    if listing.get("inCartCount", 0) > 0:
        score += 2
    if _is_recent_sale(listing.get("lastSaleDate")):
        score += 2
    return score


def etsy_trending_listings(
    seed: str, max_items: int = 50, digital_only: bool = False, timeout_secs: int = 120
) -> list[dict]:
    if not APIFY_API_TOKEN:
        print(f"[trending:etsy] '{seed}' -> no results (APIFY_API_TOKEN is not configured)")
        return []

    client = ApifyClient(APIFY_API_TOKEN)
    try:
        run = client.actor(ACTOR_ID).call(
            run_input={"keywords": [seed], "maxItems": max_items, "digitalOnly": digital_only},
            timeout_secs=timeout_secs,
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except Exception as exc:  # Apify client raises its own broad exception types
        print(f"[trending:etsy] '{seed}' -> no results ({exc})")
        return []

    items.sort(key=trend_score, reverse=True)
    return items


def format_listing(listing: dict) -> str:
    title = listing.get("title", "?")
    signals = []
    if listing.get("isBestseller"):
        signals.append("bestseller")
    if listing.get("isStarSeller"):
        signals.append("star seller")
    cart = listing.get("inCartCount", 0)
    if cart:
        signals.append(f"{cart} in carts")
    if _is_recent_sale(listing.get("lastSaleDate")):
        signals.append("sold recently")
    tag = f" [{', '.join(signals)}]" if signals else ""
    return f"{title}{tag}"
