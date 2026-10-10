"""
Cross-platform product-idea generator.

Queries each platform's autocomplete/suggest endpoint for one or more seed
keywords, aggregates the results, and ranks phrases by how many different
platforms suggested them (a phrase surfacing on Amazon AND YouTube AND
Etsy is a much stronger product signal than one only Amazon suggests).

Usage:
    python idea_generator.py "funny mug" "planner printable" --deep
    python idea_generator.py "dog bandana" --platforms amazon,youtube --output ideas.csv

Platforms: amazon, youtube, etsy, pinterest, tiktok, google_trends (see
suggest_providers.py for reliability notes — amazon and youtube are the
only ones that work with zero setup; google_trends works but is rate-limited
and flaky by nature, not a bug).

Output: a CSV (open it in Excel/Numbers/Sheets — raw CSV reads as comma
soup in a plain text editor) plus a grouped .md report next to it, ordered
by seed keyword, that's meant to be read directly in an editor or terminal.
"""
import argparse
import csv
import os
import string
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from product_research.amazon_bestsellers import CATEGORIES as AMAZON_CATEGORIES, amazon_bestsellers
from product_research.etsy_listings import SEEDS as ETSY_DEFAULT_SEEDS, etsy_trending_listings, format_listing, trend_score
from product_research.etsy_snapshot import diff_seed, format_age, load_snapshot, save_snapshot, to_minimal
from product_research.google_trends import google_trending_now
from product_research.suggest_providers import PROVIDERS

_ETSY_TRENDING_DEFAULT = "__default__"

ALPHABET = string.ascii_lowercase


def expand_queries(seed: str, deep: bool) -> list[str]:
    queries = [seed]
    if deep:
        queries += [f"{seed} {letter}" for letter in ALPHABET]
    return queries


def normalize(phrase: str) -> str:
    phrase = phrase.lower().replace("'", "").replace("-", " ")
    return " ".join(phrase.split())


def collect_ideas(seeds: list[str], platforms: list[str], deep: bool, sleep: float) -> dict:
    ideas: dict[str, dict] = defaultdict(lambda: {"display": "", "platforms": set(), "seeds": set()})

    for seed in seeds:
        for query in expand_queries(seed, deep):
            for platform in platforms:
                provider = PROVIDERS[platform]
                for suggestion in provider(query):
                    if not suggestion:
                        continue
                    key = normalize(suggestion)
                    entry = ideas[key]
                    entry["display"] = entry["display"] or suggestion.strip()
                    entry["platforms"].add(platform)
                    entry["seeds"].add(seed)
                if sleep:
                    time.sleep(sleep)

    return ideas


def group_by_seed(ideas: dict, seed_order: list[str]) -> dict[str, list[tuple]]:
    """Bucket ideas under the first seed (in input order) that produced them,
    each bucket sorted by platform count (desc) then alphabetically."""
    order_index = {seed: i for i, seed in enumerate(seed_order)}
    groups: dict[str, list[tuple]] = defaultdict(list)

    for entry in ideas.values():
        primary_seed = min(entry["seeds"], key=lambda s: order_index[s])
        row = (entry["display"], sorted(entry["platforms"]), sorted(entry["seeds"]))
        groups[primary_seed].append(row)

    for rows in groups.values():
        rows.sort(key=lambda row: (-len(row[1]), row[0]))

    return groups


def write_csv(groups: dict[str, list[tuple]], seed_order: list[str], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["idea", "platform_count", "platforms", "seed_keywords"])
        for seed in seed_order:
            for display, platforms, seeds in groups.get(seed, []):
                writer.writerow([display, len(platforms), ", ".join(platforms), ", ".join(seeds)])


def write_report(groups: dict[str, list[tuple]], seed_order: list[str], path: str) -> None:
    lines = ["# Product Ideas\n"]
    for seed in seed_order:
        rows = groups.get(seed, [])
        if not rows:
            continue
        lines.append(f"\n## {seed}  ({len(rows)} ideas)\n")
        for display, platforms, _ in rows:
            lines.append(f"- **[{len(platforms)}]** {display} — _{', '.join(platforms)}_")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_trending_report(trending: list[tuple[str, str]], geo: str, path: str) -> None:
    lines = [f"# Google Trending Now ({geo})\n"]
    if trending:
        for title, traffic in trending:
            lines.append(f"- {title} — _{traffic} searches_")
    else:
        lines.append("- (no results)")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def write_amazon_trending_report(by_category: dict[str, list[str]], path: str) -> None:
    lines = ["# Amazon Best Sellers\n"]
    for category, titles in by_category.items():
        lines.append(f"\n## {category}  ({len(titles)})\n")
        if titles:
            for title in titles:
                lines.append(f"- {title}")
        else:
            lines.append("- (no results — blocked, bad category, or markup changed)")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def print_amazon_trending(by_category: dict[str, list[str]], top: int) -> None:
    for category, titles in by_category.items():
        print(f"\nAmazon Best Sellers: {category}  ({len(titles)})")
        if not titles:
            print("  (no results)")
        for title in titles[:top]:
            print(f"  {title}")


def _diff_summary_lines(diff: dict) -> list[str]:
    lines = [f"  {len(diff['new'])} new, {len(diff['dropped'])} dropped, {diff['unchanged_count']} unchanged"]
    for listing in diff["new"][:10]:
        lines.append(f"  + NEW: {listing.get('title')}")
    for change in diff["changed"][:5]:
        cart = change["cart_delta"]
        cart_str = f"{'+' if cart >= 0 else ''}{cart} carts" if cart else ""
        score_str = f"score {'+' if change['score_delta'] >= 0 else ''}{change['score_delta']}"
        detail = ", ".join(s for s in (cart_str, score_str) if s)
        lines.append(f"  ~ moved: {change['title']} ({detail})")
    return lines


def write_etsy_trending_report(by_seed: dict[str, list[dict]], diffs: dict[str, dict] | None, snapshot_age: str, path: str) -> None:
    lines = ["# Etsy Trending (POD + Digital)\n", f"\n_Comparing against snapshot from: {snapshot_age}_\n"]
    for seed, listings in by_seed.items():
        lines.append(f"\n## {seed}  ({len(listings)})\n")
        if diffs and seed in diffs:
            lines.extend(_diff_summary_lines(diffs[seed]))
            lines.append("")
        if listings:
            for listing in listings:
                lines.append(f"- {format_listing(listing)}")
        else:
            lines.append("- (no results — check APIFY_API_TOKEN configuration)")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def print_etsy_trending(by_seed: dict[str, list[dict]], diffs: dict[str, dict] | None, top: int) -> None:
    for seed, listings in by_seed.items():
        print(f"\nEtsy trending: {seed}  ({len(listings)})")
        if diffs and seed in diffs:
            for line in _diff_summary_lines(diffs[seed]):
                print(line)
        if not listings:
            print("  (no results)")
        for listing in listings[:top]:
            print(f"  {format_listing(listing)}")


def print_trending(trending: list[tuple[str, str]]) -> None:
    print("\nTrending right now (Google, general — not tied to any seed):")
    if not trending:
        print("  (no results)")
    for title, traffic in trending:
        print(f"  {title}  ({traffic} searches)")


def print_summary(groups: dict[str, list[tuple]], seed_order: list[str], top: int) -> None:
    for seed in seed_order:
        rows = groups.get(seed, [])
        if not rows:
            continue
        print(f"\n{seed}  ({len(rows)} ideas)")
        for display, platforms, _ in rows[:top]:
            print(f"  [{len(platforms)}] {display}  ({', '.join(platforms)})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate product ideas from platform autocomplete suggestions.")
    parser.add_argument("seeds", nargs="*", help="Seed keywords, e.g. \"funny mug\" \"dog bandana\". "
                                                   "Not required if you only pass --trending.")
    parser.add_argument(
        "--platforms",
        default="amazon,youtube,etsy,pinterest,tiktok",
        help="Comma-separated subset of: amazon,youtube,etsy,pinterest,tiktok,google_trends. "
             "google_trends isn't in the default set — it retries with multi-second backoff on "
             "rate limits, which adds up fast in --deep mode. Opt in explicitly.",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Also query each seed + each letter a-z for broader coverage (26x more requests per platform).",
    )
    parser.add_argument("--sleep", type=float, default=0.2, help="Delay in seconds between requests (politeness).")
    parser.add_argument("--output", default="product_ideas.csv", help="CSV output path.")
    parser.add_argument("--report", default="product_ideas.md", help="Grouped, human-readable report path.")
    parser.add_argument("--top", type=int, default=10, help="How many ideas per seed to print to the console.")
    parser.add_argument(
        "--trending",
        action="store_true",
        help="Also fetch Google's general trending searches (no seed keyword needed) — sports/celebrities/"
             "news, not shopping-specific. Works standalone with no seeds at all.",
    )
    parser.add_argument("--geo", default="US", help="Region code for --trending, e.g. US, GB, IN.")
    parser.add_argument(
        "--trending-output",
        default="trending.md",
        help="Where --trending results are written — a separate file from --report so it never "
             "overwrites your seed-based product_ideas.md.",
    )
    parser.add_argument(
        "--amazon-trending",
        default=None,
        help="Comma-separated Amazon Best Sellers categories, no seed keyword needed, e.g. "
             f"toys-games,pet-supplies. Known categories: {', '.join(sorted(AMAZON_CATEGORIES))}. "
             "A raw zgbs slug also works. HTML-scraped, not an API — brittle, and higher block risk "
             "than the other providers here. See amazon_bestsellers.py before relying on this.",
    )
    parser.add_argument(
        "--amazon-trending-output",
        default="amazon_trending.md",
        help="Where --amazon-trending results are written — its own file, separate from --report "
             "and --trending-output.",
    )
    parser.add_argument(
        "--etsy-trending",
        nargs="?",
        const=_ETSY_TRENDING_DEFAULT,
        default=None,
        help="What's actually selling on Etsy across POD physical + digital products (bestseller badges, "
             "live cart counts, recent sales) via a paid Apify actor — NOT free, see etsy_listings.py. "
             "Bare flag uses the built-in seed list; pass comma-separated seeds to override, e.g. "
             '--etsy-trending "trucker hat,svg file". Needs APIFY_API_TOKEN.',
    )
    parser.add_argument("--etsy-max-items", type=int, default=50, help="Max listings fetched per seed for --etsy-trending.")
    parser.add_argument(
        "--etsy-trending-output",
        default="etsy_trending.md",
        help="Where --etsy-trending results are written — its own file.",
    )
    args = parser.parse_args()

    if not args.seeds and not args.trending and not args.amazon_trending and not args.etsy_trending:
        sys.exit(
            "Provide at least one seed keyword, or pass --trending / --amazon-trending / "
            "--etsy-trending for seed-free trend data."
        )

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    unknown = set(platforms) - set(PROVIDERS)
    if unknown:
        sys.exit(f"Unknown platform(s): {', '.join(sorted(unknown))}. Choose from: {', '.join(PROVIDERS)}")

    if args.trending:
        trending = google_trending_now(geo=args.geo)
        print_trending(trending)
        write_trending_report(trending, args.geo, args.trending_output)
        print(f"\nTrending report: {args.trending_output}")

    if args.amazon_trending:
        categories = [c.strip() for c in args.amazon_trending.split(",") if c.strip()]
        by_category = {category: amazon_bestsellers(category) for category in categories}
        print_amazon_trending(by_category, args.top)
        write_amazon_trending_report(by_category, args.amazon_trending_output)
        print(f"\nAmazon Best Sellers report: {args.amazon_trending_output}")

    if args.etsy_trending:
        etsy_seeds = (
            ETSY_DEFAULT_SEEDS
            if args.etsy_trending == _ETSY_TRENDING_DEFAULT
            else [s.strip() for s in args.etsy_trending.split(",") if s.strip()]
        )
        by_seed = {seed: etsy_trending_listings(seed, max_items=args.etsy_max_items) for seed in etsy_seeds}

        snapshot = load_snapshot()
        diffs = {
            seed: diff_seed(snapshot["by_seed"].get(seed, []), [(listing, trend_score(listing)) for listing in listings])
            for seed, listings in by_seed.items()
            if seed in snapshot["by_seed"] and listings  # skip diffing a failed fetch as "everything dropped"
        }
        snapshot_age = format_age(snapshot["fetched_at"])

        print_etsy_trending(by_seed, diffs, args.top)
        write_etsy_trending_report(by_seed, diffs, snapshot_age, args.etsy_trending_output)
        # Merge into the prior snapshot rather than replacing it outright, so a
        # seed that fails this run keeps its history for next time instead of
        # losing it.
        successful = {seed: listings for seed, listings in by_seed.items() if listings}
        if successful:
            merged = dict(snapshot["by_seed"])
            merged.update({
                seed: [to_minimal(listing, trend_score(listing)) for listing in listings]
                for seed, listings in successful.items()
            })
            save_snapshot(merged)
        print(f"\nEtsy trending report: {args.etsy_trending_output}")

    if not args.seeds:
        return

    ideas = collect_ideas(args.seeds, platforms, args.deep, args.sleep)
    groups = group_by_seed(ideas, args.seeds)
    write_csv(groups, args.seeds, args.output)
    write_report(groups, args.seeds, args.report)

    total = sum(len(rows) for rows in groups.values())
    print(f"\nCollected {total} unique ideas from {len(platforms)} platform(s)")
    print(f"CSV (for spreadsheet apps): {args.output}")
    print(f"Report (for reading here):  {args.report}")
    print_summary(groups, args.seeds, args.top)


if __name__ == "__main__":
    main()
