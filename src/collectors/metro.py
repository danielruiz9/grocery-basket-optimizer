"""Collect Metro Canada search tiles, then classify and normalize their prices.

Direct requests may receive HTTP 403. Do not work around that protection:
save normally accessible browser-rendered search grids/pages locally instead.
For example, with chicken_breast.html, eggs.html, etc. in a capture directory:

    python -m src.collectors.metro --html-dir /path/to/captures \
        --observed-date 2026-09-20

The default keeps the first 12 results per search, including search noise.
Prices reflect the store/region selected in the captured browser session;
they are not a Canada-wide price guarantee. This module does not refresh the
integrated optimizer snapshot or change the app's supported stores.
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
    normalize_listing_with_quality_checks,
    save_listings as _save_listings,
)
from src.collectors.metro_tiles import (
    _extract_product_records,
    _extract_unit_price,
    parse_product_records as _parse_product_records,
)


METRO_BASE_URL = "https://www.metro.ca"
METRO_SEARCH_URL = f"{METRO_BASE_URL}/en/online-grocery/search"
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "metro_prices_normalized.csv"
)
METRO_OUTPUT_COLUMNS = (
    *QUALITY_OUTPUT_COLUMNS[:16],
    "multi_buy_quantity",
    "multi_buy_unit_price",
    "single_item_price",
    *QUALITY_OUTPUT_COLUMNS[16:],
    "displayed_price_text",
    "regular_price_text",
    "regular_price_unit",
)


def fetch_search_results(search_term, session=None, timeout=30):
    """Make one ordinary request; fail clearly when access is blocked."""
    client = session or requests.Session()
    response = client.get(
        METRO_SEARCH_URL,
        params={"filter": search_term},
        timeout=timeout,
    )
    if response.status_code in {401, 403, 429}:
        raise RuntimeError(
            f"Metro Canada blocked the search request with HTTP "
            f"{response.status_code}. Use locally saved, normally accessible "
            "browser-rendered pages (--html-dir); do not bypass the block."
        )
    response.raise_for_status()
    if "access denied" in response.text.lower():
        raise RuntimeError("Metro Canada returned an access-denied page.")
    return response.text


def parse_product_records(records, search_term, observed_date=None):
    """Parse explicit tile fields, preserving Metro's price-basis metadata."""
    listings = []
    seen = set()
    for record in records:
        key = (
            record.get("product_code") or record.get("href")
            or record.get("title") or record.get("product_name")
        )
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        listing = _parse_product_records(
            [record], search_term, observed_date,
            store="Metro Canada", base_url=METRO_BASE_URL,
        )[0]
        listing["displayed_price_text"] = record.get("current_price_text")
        listing["regular_price_text"] = record.get("regular_price_text")
        _, listing["regular_price_unit"] = _extract_unit_price(
            record.get("regular_price_text")
        )
        # Keep explicitly exposed promotional unit prices in the audit data.
        # normalize_listing prevents conditional-only prices entering comparisons.
        if listing["sale_status"] == "multi-buy":
            listing["listed_unit_price"], listing["listed_unit"] = (
                _extract_unit_price(record.get("secondary_price_text"))
            )
        if listing["is_variable_weight"]:
            # "3 to 4 units per tray" and deli serving examples are not
            # fixed packages. Keep the label, never infer a package weight.
            listing["package_size"] = None
            listing["package_unit"] = None
        listings.append(listing)
    return listings


def parse_search_results(html, search_term, observed_date=None):
    """Read the search grid only, not hidden recommendation/menu tiles."""
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.select_one(".searchOnlineResults")
    if grid is None:
        return []
    return parse_product_records(
        _extract_product_records(grid), search_term, observed_date
    )


def normalize_listing(listing):
    """Apply the shared classifier, pricing and comparison-unit checks."""
    normalized = normalize_listing_with_quality_checks(listing)
    if (
        listing.get("sale_status") == "multi-buy"
        and listing.get("single_item_price") is None
    ):
        normalized["price_per_standard_unit"] = None
        normalized["normalization_error"] = (
            "Multi-buy listing has no explicit single-item price; "
            "unconditional price cannot be normalized."
        )
    if (
        normalized["comparison_group"] == "fresh_apples"
        and normalized["product_form"] == "loose"
        and listing.get("is_variable_weight") is False
        and listing.get("package_size") is not None
        and listing.get("package_unit") in {"lb", "lbs"}
    ):
        normalized["product_form"] = "bagged"
    return normalized


def normalize_listings(listings):
    return [normalize_listing(listing) for listing in listings]


def collect_metro_listings(
    search_terms=DEFAULT_SEARCH_TERMS,
    max_results_per_term=12,
    observed_date=None,
    delay_seconds=1.0,
    fetcher=None,
):
    """Collect one page per term, or inject locally captured HTML as fetcher."""
    if max_results_per_term is not None and max_results_per_term <= 0:
        raise ValueError("max_results_per_term must be greater than zero.")
    terms = tuple(search_terms)
    observed_date = observed_date or date.today().isoformat()
    if fetcher is None:
        session = requests.Session()

        def fetcher(term):
            return fetch_search_results(term, session=session)

    rows = []
    for index, term in enumerate(terms):
        parsed = parse_search_results(fetcher(term), term, observed_date)
        if not parsed:
            raise RuntimeError(
                f"No Metro search product tiles were found for {term!r}; "
                "the page may be blocked, not yet rendered, or changed. "
                "No output has been saved."
            )
        rows.extend(parsed[:max_results_per_term])
        if delay_seconds and index < len(terms) - 1:
            time.sleep(delay_seconds)
    return normalize_listings(rows)


def save_listings(listings, output_path=DEFAULT_OUTPUT_PATH):
    return _save_listings(listings, output_path, METRO_OUTPUT_COLUMNS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-term", action="append", dest="search_terms")
    parser.add_argument("--max-results", type=int, default=12)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--html-dir", type=Path,
        help="Saved rendered pages named chicken_breast.html, eggs.html, etc.",
    )
    parser.add_argument("--observed-date", type=date.fromisoformat)
    args = parser.parse_args(argv)
    if args.html_dir and args.observed_date is None:
        parser.error("--observed-date is required for saved HTML captures.")
    fetcher = None
    if args.html_dir:
        def fetcher(term):
            filename = term.replace(" ", "_") + ".html"
            if Path(filename).name != filename:
                raise ValueError("Search term must not contain path separators.")
            return (args.html_dir / filename).read_text(encoding="utf-8")
    listings = collect_metro_listings(
        search_terms=args.search_terms or DEFAULT_SEARCH_TERMS,
        max_results_per_term=args.max_results,
        observed_date=args.observed_date,
        delay_seconds=args.delay_seconds,
        fetcher=fetcher,
    )
    output = save_listings(listings, args.output)
    print(f"Saved {len(listings)} Metro listings to {output}")


if __name__ == "__main__":
    main()
