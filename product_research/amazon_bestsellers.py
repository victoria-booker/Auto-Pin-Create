"""
Amazon Best Sellers, scoped to a specific category — a genuine "trending on
Amazon" signal, unlike amazon_suggest (which reflects search-typing demand,
not actual sales rank).

Real caveats, confirmed live:
  - This is HTML scraping, not a JSON API. Amazon doesn't expose Best
    Sellers as structured data anywhere free.
  - The site-wide /gp/bestsellers page and category pages (/Best-Sellers/
    zgbs/<slug>) use DIFFERENT markup. This only targets category pages —
    confirmed their product titles live in each <img alt="..."> attribute;
    the site-wide page has no alt text on its images at all, so this
    won't work there.
  - Higher block risk than every other provider in this project. Amazon's
    terms prohibit automated access to these pages, and repeated requests
    are more likely to trigger a CAPTCHA than a JSON endpoint would. One
    request per category worked fine in testing; heavy or repeated use is
    a different risk profile — don't hammer this.
  - Brittle: Amazon changes this markup periodically without notice. An
    empty result means "check whether the alt-attribute pattern still
    matches," not necessarily "bad category name."

pip install requests
"""
import html
import re

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Friendly name -> Amazon's zgbs category slug. Scoped to categories that
# make sense for physical/POD product ideas (skips things like gift-cards,
# audible, coins, digital-text). Pass any other slug directly if you need
# one not listed here — top-level departments are named slugs (e.g.
# "toys-and-games"), but Books subcategories are numeric browse-node IDs
# nested under "books/" (e.g. childrens-coloring-books is "books/3374").
# Find either by browsing https://www.amazon.com/gp/bestsellers, opening
# the subcategory you want, and reading the slug out of its URL.
CATEGORIES = {
    "toys-games": "toys-and-games",
    "home-kitchen": "home-garden",
    "kitchen-dining": "kitchen",
    "arts-crafts": "arts-crafts",
    "pet-supplies": "pet-supplies",
    "sports-outdoors": "sporting-goods",
    "office-products": "office-products",
    "patio-lawn-garden": "lawn-garden",
    "books": "books",
    "childrens-books": "books/4",
    "childrens-coloring-books": "books/3374",
    "coloring-books-adults": "books/11357541011",
    "word-search": "books/4455",
    "crossword": "books/4416",
    "sudoku": "books/15756641",
    "logic-brain-teasers": "books/4436",
    "puzzles-general": "books/4439",
    "word-games": "books/4454",
    # "Novelty T-Shirts" (not the broader "Novelty Shirts", which also mixes in
    # button-downs/polos/tanks) — confirmed live: heavily graphic/slogan tees,
    # titles include "by Amazon Merch on Demand".
    "pod-tees-mens": "fashion/9056987011",
    "pod-tees-womens": "fashion/9056923011",
    "beauty-personal-care": "beauty",
    "baby": "baby-products",
    "clothing-shoes-jewelry": "fashion",
    "handmade": "handmade",
    "tools-home-improvement": "hi",
}

_TITLE_PATTERN = re.compile(r'<img alt="([^"]{5,200})"[^>]*class="[^"]*p13n-product-image')


def amazon_bestsellers(category: str, timeout: int = 10) -> list[str]:
    """category: a friendly key from CATEGORIES, or a raw zgbs slug."""
    slug = CATEGORIES.get(category, category)
    url = f"https://www.amazon.com/Best-Sellers/zgbs/{slug}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        titles = [html.unescape(t).strip() for t in _TITLE_PATTERN.findall(resp.text)]
        if not titles:
            print(f"[trending:amazon] '{category}' -> no results (blocked, bad category, or markup changed)")
        return titles
    except requests.RequestException as exc:
        print(f"[trending:amazon] '{category}' -> no results ({exc})")
        return []
