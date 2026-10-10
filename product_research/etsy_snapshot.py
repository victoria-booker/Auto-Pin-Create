"""
Week-over-week diffing for etsy_listings.py — turns "is this changing
enough to justify running it again" into a measured fact instead of a
guess. Each run saves a snapshot; the next run compares against it before
overwriting.

Fields kept per listing are the minimum needed to answer "is this the same
listing, and did its trend signal move": listingId (identity), title
(display), trend_score/inCartCount/isBestseller/isStarSeller (what moved).
"""
import json
import os
import time

SNAPSHOT_PATH = "etsy_trending_snapshot.json"


def to_minimal(listing: dict, score: int) -> dict:
    return {
        "listingId": listing.get("listingId"),
        "title": listing.get("title"),
        "trend_score": score,
        "inCartCount": listing.get("inCartCount", 0),
        "isBestseller": bool(listing.get("isBestseller")),
        "isStarSeller": bool(listing.get("isStarSeller")),
    }


def load_snapshot(path: str = SNAPSHOT_PATH) -> dict:
    if not os.path.exists(path):
        return {"fetched_at": None, "by_seed": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(by_seed_minimal: dict[str, list[dict]], path: str = SNAPSHOT_PATH) -> None:
    """by_seed_minimal: already-minimal records, e.g. from to_minimal() or a
    prior load_snapshot()['by_seed'] — same shape either way, so merging the
    two before saving is just plain dict/list combination."""
    data = {"fetched_at": time.time(), "by_seed": by_seed_minimal}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def diff_seed(old_listings: list[dict], new_listings: list[tuple[dict, int]]) -> dict:
    """old_listings: minimal dicts from a prior snapshot.
    new_listings: (raw listing dict, trend_score) pairs from this run."""
    old_by_id = {l["listingId"]: l for l in old_listings if l.get("listingId")}
    new_by_id = {listing.get("listingId"): (listing, score) for listing, score in new_listings if listing.get("listingId")}

    new_ids = set(new_by_id) - set(old_by_id)
    dropped_ids = set(old_by_id) - set(new_by_id)
    common_ids = set(new_by_id) & set(old_by_id)

    changed = []
    for lid in common_ids:
        listing, score = new_by_id[lid]
        old = old_by_id[lid]
        cart_delta = listing.get("inCartCount", 0) - old.get("inCartCount", 0)
        score_delta = score - old.get("trend_score", 0)
        if cart_delta != 0 or score_delta != 0:
            changed.append({"title": listing.get("title"), "cart_delta": cart_delta, "score_delta": score_delta})

    return {
        "new": [new_by_id[lid][0] for lid in new_ids],
        "dropped": [old_by_id[lid] for lid in dropped_ids],
        "changed": sorted(changed, key=lambda c: abs(c["score_delta"]) + abs(c["cart_delta"]), reverse=True),
        "unchanged_count": len(common_ids) - len(changed),
    }


def format_age(fetched_at: float | None) -> str:
    if fetched_at is None:
        return "no prior snapshot — this is the baseline run"
    days = (time.time() - fetched_at) / 86400
    if days < 1:
        return "less than a day ago"
    return f"{days:.1f} days ago"
