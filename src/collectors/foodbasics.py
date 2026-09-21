"""Collect and normalize Food Basics Canada grocery search listings.

Food Basics renders product data in server-side product tiles. Run the module
directly to collect the default grocery searches and write a CSV:

    python -m src.collectors.foodbasics
"""

import argparse
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from src.collectors.common import (
    DEFAULT_SEARCH_TERMS,
    QUALITY_OUTPUT_COLUMNS,
    normalize_listing_with_quality_checks as _normalize_listing,
    save_listings as _save_listings,
)

from src.collectors.metro_tiles import (
    extract_package_details,
    _extract_product_records,
    parse_product_records as _parse_product_records,
)


FOODBASICS_BASE_URL = "https://www.foodbasics.ca"
FOODBASICS_SEARCH_URL = f"{FOODBASICS_BASE_URL}/search"
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "foodbasics_prices_normalized.csv"
)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
}
FOODBASICS_OUTPUT_COLUMNS = (
    *QUALITY_OUTPUT_COLUMNS[:16],
    "multi_buy_quantity",
    "multi_buy_unit_price",
    "single_item_price",
    *QUALITY_OUTPUT_COLUMNS[16:],
)


def fetch_search_results(search_term, session=None, timeout=30):
    """Fetch one Food Basics Canada search-result page."""
    client = session or requests.Session()
    response = client.get(
        FOODBASICS_SEARCH_URL,
        params={"filter": search_term},
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )

    if response.status_code in {401, 403}:
        raise RuntimeError(
            "Food Basics Canada blocked the search request with HTTP "
            f"{response.status_code}."
        )

    response.raise_for_status()

    if "access denied" in response.text.lower():
        raise RuntimeError(
            "Food Basics Canada returned an access-denied page."
        )

    return response.text


def parse_product_records(records, search_term, observed_date=None):
    """Convert explicit Food Basics product-tile fields to raw listings."""
    return _parse_product_records(
        records,
        search_term,
        observed_date,
        store="Food Basics Canada",
        base_url=FOODBASICS_BASE_URL,
    )


def normalize_listing(listing):
    """Normalize one Food Basics row with package-aware form context."""
    normalized = _normalize_listing(listing)

    if (
        normalized["comparison_group"] == "fresh_apples"
        and normalized["product_form"] in {"loose", "packaged"}
        and listing.get("is_variable_weight") is False
        and listing.get("package_size") is not None
        and listing.get("package_unit") in {"lb", "lbs"}
    ):
        normalized["product_form"] = "bagged"

    return normalized


def normalize_listings(listings):
    return [normalize_listing(listing) for listing in listings]


def parse_search_results(html, search_term, observed_date=None):
    """Parse Food Basics server-rendered product tiles."""
    soup = BeautifulSoup(html, "html.parser")
    return parse_product_records(
        _extract_product_records(soup),
        search_term=search_term,
        observed_date=observed_date,
    )


def collect_foodbasics_listings(
    search_terms=DEFAULT_SEARCH_TERMS,
    max_results_per_term=12,
    observed_date=None,
    delay_seconds=1.0,
    fetcher=None,
):
    """Fetch, parse, classify, and normalize the configured searches."""
    if max_results_per_term is not None and max_results_per_term <= 0:
        raise ValueError("max_results_per_term must be greater than zero.")

    search_terms = tuple(search_terms)
    observed_date = observed_date or date.today().isoformat()

    if fetcher is None:
        session = requests.Session()

        def fetcher(search_term):
            return fetch_search_results(search_term, session=session)

    listings = []

    for index, search_term in enumerate(search_terms):
        html = fetcher(search_term)
        parsed = parse_search_results(
            html,
            search_term=search_term,
            observed_date=observed_date,
        )

        if not parsed:
            raise RuntimeError(
                f"No Food Basics product tiles were found for "
                f"{search_term!r}; the page may have been blocked or its "
                "markup may have changed."
            )

        if max_results_per_term is not None:
            parsed = parsed[:max_results_per_term]

        listings.extend(parsed)

        if delay_seconds and index < len(search_terms) - 1:
            time.sleep(delay_seconds)

    return normalize_listings(listings)


def save_listings(listings, output_path=DEFAULT_OUTPUT_PATH):
    return _save_listings(
        listings,
        output_path,
        output_columns=FOODBASICS_OUTPUT_COLUMNS,
    )


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Collect Food Basics Canada grocery search listings and write "
            "a classified, price-normalized CSV."
        )
    )
    parser.add_argument(
        "--search-term",
        action="append",
        dest="search_terms",
        help=(
            "Search term to collect. Repeat for multiple terms; defaults to "
            "the seven initial grocery searches."
        ),
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=12,
        help="Maximum listings retained per search term (default: 12).",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.0,
        help="Delay between search requests (default: 1 second).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"CSV output path (default: {DEFAULT_OUTPUT_PATH}).",
    )
    return parser


def main(argv=None):
    args = _build_argument_parser().parse_args(argv)
    search_terms = args.search_terms or DEFAULT_SEARCH_TERMS
    listings = collect_foodbasics_listings(
        search_terms=tuple(search_terms),
        max_results_per_term=args.max_results,
        delay_seconds=args.delay_seconds,
    )
    output_path = save_listings(listings, args.output)
    print(f"Saved {len(listings)} listings to {output_path}")


if __name__ == "__main__":
    main()
