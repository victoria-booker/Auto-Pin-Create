"""Product-idea research endpoint, wrapping product_research/idea_generator.py."""
from typing import Annotated, Literal

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api_auth import require_api_key

from product_research.amazon_bestsellers import CATEGORIES as AMAZON_CATEGORIES, amazon_bestsellers
from product_research.etsy_listings import _is_recent_sale, etsy_trending_listings, trend_score
from product_research.google_trends import google_trending_now
from product_research.idea_generator import collect_ideas, group_by_seed
from product_research.pinterest_trends import PinterestTrendsError, get_pinterest_trends
from product_research.research_runtime_config import APIFY_API_TOKEN
from product_research.suggest_providers import PROVIDERS

router = APIRouter(dependencies=[Depends(require_api_key)])

DEFAULT_PLATFORMS = ["amazon", "youtube"]  # the platforms that work with no extra setup


# ---------- models ----------

class GenerateIdeasRequest(BaseModel):
    seeds: list[Annotated[str, Field(min_length=2, max_length=80)]] = Field(
        ...,
        min_length=1,
        max_length=2,
        description="One or two seed keywords to research, e.g. ['funny mug', 'dog bandana'].",
    )
    platforms: list[str] | None = Field(
        None,
        description="Subset of amazon, youtube, etsy, pinterest, tiktok, google_trends. Defaults to "
                    "amazon + youtube (the only ones that work without extra setup right now). "
                    "google_trends gives real trend/spike signal but is rate-limited by Google and "
                    "retries slowly — avoid it here since GPT Action calls have a request timeout.",
    )
    deep: bool = Field(
        False,
        description="Also query each seed plus every letter a-z. Only one seed and up to two platforms are allowed in deep mode to avoid Action timeouts.",
    )


class IdeaItem(BaseModel):
    idea: str = Field(..., description="The suggested product idea / search phrase.")
    platform_count: int = Field(..., description="How many different platforms suggested this phrase.")
    platforms: list[str] = Field(..., description="Which platforms suggested it.")


class SeedGroup(BaseModel):
    seed: str = Field(..., description="The seed keyword these ideas were generated from.")
    ideas: list[IdeaItem] = Field(..., description="Ideas for this seed, ranked by platform_count then alphabetically.")


class GenerateIdeasResponse(BaseModel):
    groups: list[SeedGroup] = Field(..., description="One group per seed keyword, in the order they were requested.")


class TrendingItem(BaseModel):
    title: str = Field(..., description="The trending search term.")
    approx_traffic: str = Field(..., description="Google's rough search-volume bucket, e.g. '5000+'.")


class TrendingResponse(BaseModel):
    geo: str = Field(..., description="Region code the results are for.")
    items: list[TrendingItem] = Field(..., description="Trending searches, no seed keyword involved.")


class PinterestTrendItem(BaseModel):
    keyword: str
    pct_growth_wow: float | None = None
    pct_growth_mom: float | None = None
    pct_growth_yoy: float | None = None
    time_series: dict[str, float] | None = None


class PinterestTrendsResponse(BaseModel):
    region: str
    trend_type: str
    trends: list[PinterestTrendItem]


class AmazonBestsellersResponse(BaseModel):
    category: str
    titles: list[str]


class EtsyListingItem(BaseModel):
    listing_id: str | int | None = None
    title: str
    url: str | None = None
    price: str | float | None = None
    in_carts: int | None = None
    bestseller: bool
    star_seller: bool
    recently_sold: bool
    trend_score: int


class EtsyResearchResponse(BaseModel):
    seed: str
    listings: list[EtsyListingItem]


# ---------- endpoint ----------

@router.post(
    "/generate-ideas",
    operation_id="generateIdeas",
    summary="Generate product ideas from platform autocomplete/suggest signal",
    description="Queries each platform's autocomplete/suggest endpoint for the given seed keywords and "
                "ranks results by how many platforms suggested the same phrase.",
    response_model=GenerateIdeasResponse,
)
def generate_ideas_endpoint(req: GenerateIdeasRequest):
    platforms = req.platforms or DEFAULT_PLATFORMS
    if len(platforms) > 3:
        raise HTTPException(status_code=400, detail="Choose no more than three platforms per request.")
    if req.deep and (len(req.seeds) > 1 or len(platforms) > 2):
        raise HTTPException(status_code=400, detail="Deep research supports one seed and at most two platforms per request.")
    unknown = set(platforms) - set(PROVIDERS)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown platform(s): {', '.join(sorted(unknown))}. Choose from: {', '.join(PROVIDERS)}",
        )

    ideas = collect_ideas(req.seeds, platforms, req.deep, sleep=0.2)
    groups = group_by_seed(ideas, req.seeds)

    return GenerateIdeasResponse(
        groups=[
            SeedGroup(
                seed=seed,
                ideas=[
                    IdeaItem(idea=display, platform_count=len(plats), platforms=plats)
                    for display, plats, _ in groups.get(seed, [])
                ],
            )
            for seed in req.seeds
        ]
    )


@router.get(
    "/trending-now",
    operation_id="getTrendingNow",
    summary="Get Google's general trending searches, no seed keyword needed",
    description="Google's current 'what's trending right now' feed for a region — sports, celebrities, "
                "news, etc. Not filtered to shopping/product topics; useful for spotting a cultural moment "
                "worth riffing a product on, not a substitute for generateIdeas.",
    response_model=TrendingResponse,
)
def get_trending_now_endpoint(geo: str = "US"):
    if len(geo) != 2 or not geo.isalpha():
        raise HTTPException(status_code=400, detail="geo must be a two-letter region code.")
    geo = geo.upper()
    items = google_trending_now(geo=geo)
    return TrendingResponse(
        geo=geo,
        items=[TrendingItem(title=title, approx_traffic=traffic) for title, traffic in items],
    )


@router.get(
    "/pinterest-trends",
    operation_id="getPinterestTrends",
    summary="Get Pinterest's current top trending keywords",
    description="Requires Pinterest to authorize this app for Trends access; Standard access alone is not guaranteed to include it.",
    response_model=PinterestTrendsResponse,
)
def get_pinterest_trends_endpoint(
    region: str = Query("US", min_length=2, max_length=2, pattern="^[A-Za-z]{2}$"),
    trend_type: Literal["growing", "monthly"] = "growing",
    limit: int = Query(50, ge=1, le=50),
):
    region = region.upper()
    try:
        trends = get_pinterest_trends(region=region, trend_type=trend_type, limit=limit)
    except PinterestTrendsError as exc:
        if exc.status_code in {401, 403}:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Pinterest denied Trends access for this token. Confirm Pinterest has enabled Trends "
                    "for this app and that the issued token includes trends:read; Standard "
                    "access alone is not documented as sufficient."
                ),
            ) from exc
        raise HTTPException(status_code=502, detail="Pinterest Trends API returned an error.") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Could not reach Pinterest Trends API.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return PinterestTrendsResponse(
        region=region,
        trend_type=trend_type,
        trends=[PinterestTrendItem.model_validate(trend) for trend in trends],
    )


@router.get(
    "/amazon-bestsellers",
    operation_id="getAmazonBestsellers",
    summary="Get Amazon bestseller product titles for a category",
    description="Fetches a public Amazon bestseller category page. Results may be empty if Amazon blocks the request or changes its markup.",
    response_model=AmazonBestsellersResponse,
)
def get_amazon_bestsellers_endpoint(
    category: str = Query(..., min_length=2, max_length=64),
    limit: int = Query(20, ge=1, le=50),
):
    if category not in AMAZON_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown Amazon category. Choose from: {', '.join(sorted(AMAZON_CATEGORIES))}",
        )
    return AmazonBestsellersResponse(
        category=category,
        titles=amazon_bestsellers(category)[:limit],
    )


@router.get(
    "/etsy-trending",
    operation_id="getEtsyTrending",
    summary="Research Etsy listings for a product keyword",
    description="Runs the configured Apify Etsy listings scraper and ranks the results by bestseller, cart, and recent-sale signals. Requires APIFY_API_TOKEN; may take up to 30 seconds.",
    response_model=EtsyResearchResponse,
)
def get_etsy_trending_endpoint(
    seed: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(10, ge=1, le=25),
    digital_only: bool = False,
):
    if not APIFY_API_TOKEN:
        raise HTTPException(status_code=503, detail="Set APIFY_API_TOKEN to enable Etsy listing research.")
    listings = etsy_trending_listings(
        seed,
        max_items=limit,
        digital_only=digital_only,
        timeout_secs=30,
    )
    return EtsyResearchResponse(
        seed=seed,
        listings=[
            EtsyListingItem(
                listing_id=listing.get("listingId"),
                title=listing.get("title") or "Untitled listing",
                url=listing.get("url") or listing.get("listingUrl"),
                price=listing.get("price"),
                in_carts=listing.get("inCartCount"),
                bestseller=bool(listing.get("isBestseller")),
                star_seller=bool(listing.get("isStarSeller")),
                recently_sold=_is_recent_sale(listing.get("lastSaleDate")),
                trend_score=trend_score(listing),
            )
            for listing in listings[:limit]
        ],
    )
