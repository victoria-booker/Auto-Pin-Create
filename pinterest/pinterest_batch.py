"""
Batch version — run several listings through the pipeline in one go,
without one failure taking down the rest.
"""
import time

from pinterest.pinterest_pipeline import create_pin_for_listing

SECONDS_BETWEEN_PINS = 2  # polite pacing, well under any rate limit


def create_pins_batch(listings):
    """
    listings: a list of dicts, each with the same keys create_pin_for_listing
    takes (product_title, theme, category, link, image_url, and
    optionally title/description).

    Returns a list of results in the same order, each either
    {"status": "ok", "pin_id": ...} or {"status": "failed", "error": ...}
    so one bad listing doesn't stop the rest from running.
    """
    results = []

    for i, listing in enumerate(listings):
        try:
            result = create_pin_for_listing(**listing)
            results.append({"status": "ok", "pin_id": result.get("id")})
            print(f"[{i + 1}/{len(listings)}] created pin {result.get('id')}")
        except Exception as e:
            results.append({"status": "failed", "error": str(e)})
            print(f"[{i + 1}/{len(listings)}] FAILED: {e}")

        if i < len(listings) - 1:
            time.sleep(SECONDS_BETWEEN_PINS)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    print(f"\nDone: {ok_count}/{len(listings)} succeeded")
    return results


if __name__ == "__main__":
    # quick manual test with two listings
    listings = [
        {
            "product_title": "Word Search Puzzles for Millennial Moms",
            "theme": "90s nostalgia gift for a millennial mom",
            "category": "90s-nostalgia",
            "link": "https://wittynostalgia.com",
            "image_url": "https://wittynostalgia.com/cdn/shop/files/witty-nostalgia-hero-banner.png",
        },
        {
            "product_title": "All I Do Is Try T-Shirt",
            "theme": "vintage trophy-style humor tee",
            "category": "products-tagged",
            "link": "https://wittynostalgia.com/products/all-i-do-is-try-t-shirt-trophy-collection-humorous-achievement-design",
            "image_url": "https://wittynostalgia.com/cdn/shop/files/10899704592908662885_2048.jpg",
        },
    ]
    create_pins_batch(listings)
