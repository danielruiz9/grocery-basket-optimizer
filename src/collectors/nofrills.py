"""Collect and normalize No Frills Canada grocery search listings.

The parser reads product records embedded in the rendered search page's
``__NEXT_DATA__`` payload. Run the module directly to collect the default
grocery searches and write a CSV:

    python -m src.collectors.nofrills
"""

import argparse
import json
import math
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from src.classification import expected_standardized_unit
from src.collectors.common import (
    DEFAULT_SEARCH_TERMS,
    OUTPUT_COLUMNS,
    normalize_listing as _normalize_listing,
    save_listings as _save_listings,
)
from src.pricing import calculate_price_per_standard_unit


NOFRILLS_BASE_URL = "https://www.nofrills.ca"
NOFRILLS_SEARCH_URL = f"{NOFRILLS_BASE_URL}/en/search"
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "nofrills_prices_normalized.csv"
)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
}
NOFRILLS_OUTPUT_COLUMNS = (
    *OUTPUT_COLUMNS[:-1],
    "data_quality_warning",
    OUTPUT_COLUMNS[-1],
)
IMPLAUSIBLE_UNIT_PRICE_FACTOR = 5.0

_NUMBER_PATTERN = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
_MULTIPACK_PATTERN = re.compile(
    r"(?P<count>\d+)\s*[x\u00d7]\s*"
    r"(?P<size>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|g|ml|l|lbs?)\b",
    re.IGNORECASE,
)
_PACKAGE_PATTERN = re.compile(
    r"(?P<size>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|g|ml|l|lbs?|ea|each|count|ct)\b",
    re.IGNORECASE,
)
_UNIT_PRICE_PATTERN = re.compile(
    rf"\$\s*(?P<price>{_NUMBER_PATTERN})\s*/\s*"
    r"(?P<amount>\d+(?:\.\d+)?)?\s*"
    r"(?P<unit>kg|g|ml|l|lbs?|ea|each)\b",
    re.IGNORECASE,
)


def fetch_search_results(search_term, session=None, timeout=30):
    """Fetch one No Frills Canada search-result page."""
    client = session or requests.Session()
    response = client.get(
        NOFRILLS_SEARCH_URL,
        params={"search-bar": search_term},
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )

    if response.status_code in {401, 403}:
        raise RuntimeError(
            "No Frills Canada blocked the search request with HTTP "
            f"{response.status_code}."
        )

    response.raise_for_status()

    if "access denied" in response.text.lower():
        raise RuntimeError(
            "No Frills Canada returned an access-denied page."
        )

    return response.text


def _parse_money(value):
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        return float(value)

    match = re.search(rf"\$?\s*(?P<amount>{_NUMBER_PATTERN})", value)

    if not match:
        return None

    return float(match.group("amount").replace(",", ""))


def _canonical_package_unit(unit):
    normalized = unit.lower().strip()

    if normalized in {"ea", "each", "count", "ct"}:
        return "count"

    if normalized == "lbs":
        return "lb"

    return normalized


def extract_package_details(package_sizing):
    """Parse the package portion of a No Frills package-sizing label."""
    if not package_sizing:
        return {
            "package_size_text": None,
            "package_size": None,
            "package_unit": None,
        }

    package_text = package_sizing.split(",", 1)[0].strip()

    if not package_text or package_text.startswith("$"):
        return {
            "package_size_text": None,
            "package_size": None,
            "package_unit": None,
        }

    multipack_match = _MULTIPACK_PATTERN.fullmatch(package_text)

    if multipack_match:
        count = float(multipack_match.group("count"))
        size = float(multipack_match.group("size"))
        return {
            "package_size_text": package_text,
            "package_size": count * size,
            "package_unit": _canonical_package_unit(
                multipack_match.group("unit")
            ),
        }

    package_match = _PACKAGE_PATTERN.fullmatch(package_text)

    if package_match:
        return {
            "package_size_text": package_text,
            "package_size": float(package_match.group("size")),
            "package_unit": _canonical_package_unit(
                package_match.group("unit")
            ),
        }

    return {
        "package_size_text": package_text,
        "package_size": None,
        "package_unit": None,
    }


def _extract_unit_price(package_sizing):
    if not package_sizing:
        return None, None, None

    match = _UNIT_PRICE_PATTERN.search(package_sizing)

    if not match:
        return None, None, None

    listed_unit_price = _parse_money(match.group("price"))
    amount = match.group("amount") or "1"
    unit = _canonical_package_unit(match.group("unit"))

    if unit == "count":
        listed_unit = "1unit" if float(amount) == 1 else f"{amount}unit"
    elif float(amount) == 1:
        listed_unit = unit
    else:
        listed_unit = f"{amount}{unit}"

    unit_price_text = package_sizing[match.start():].strip()
    return unit_price_text, listed_unit_price, listed_unit


def _canonical_product_url(href):
    if not href:
        return None

    absolute_url = urljoin(NOFRILLS_BASE_URL, href)
    parsed = urlsplit(absolute_url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def _is_variable_weight(tile):
    product_id = str(tile.get("productId") or "").upper()
    uom = str(tile.get("uom") or "").upper()
    pricing_type = str(
        (tile.get("pricingUnits") or {}).get("type") or ""
    ).upper()
    display_price = str(
        (tile.get("pricing") or {}).get("displayPrice") or ""
    ).lower()

    return (
        product_id.endswith("_KG")
        or uom == "KG"
        or "PRICED_BY_WEIGHT" in pricing_type
        or display_price.startswith("about ")
    )


def _sale_status(tile, regular_price):
    deal = tile.get("deal") or {}
    status = deal.get("name") or deal.get("type")

    if status:
        return str(status)

    if regular_price is not None:
        return "sale"

    return None


def parse_product_tiles(tiles, search_term, observed_date=None):
    """Parse No Frills product-tile records into raw listings."""
    observed_date = observed_date or date.today().isoformat()
    listings = []
    seen_products = set()

    for tile in tiles:
        product_key = (
            tile.get("productId")
            or tile.get("link")
            or tile.get("title")
        )

        if product_key and product_key in seen_products:
            continue

        package_sizing = tile.get("packageSizing")
        package = extract_package_details(package_sizing)
        unit_price_text, listed_unit_price, listed_unit = (
            _extract_unit_price(package_sizing)
        )
        pricing = tile.get("pricing") or {}
        displayed_price = _parse_money(pricing.get("price"))
        regular_price = _parse_money(pricing.get("wasPrice"))
        sale_status = _sale_status(tile, regular_price)

        listings.append(
            {
                "date_observed": str(observed_date),
                "search_term": search_term,
                "store": "No Frills Canada",
                "product_title": tile.get("title"),
                "product_url": _canonical_product_url(tile.get("link")),
                **package,
                "displayed_price": displayed_price,
                "unit_price_text": unit_price_text,
                "listed_unit_price": listed_unit_price,
                "listed_unit": listed_unit,
                "is_variable_weight": _is_variable_weight(tile),
                "sale_status": sale_status,
                "sale_price": (
                    displayed_price if sale_status is not None else None
                ),
                "regular_price": regular_price,
            }
        )

        if product_key:
            seen_products.add(product_key)

    return listings


def _extract_product_tiles(page_data):
    try:
        components = page_data["props"]["pageProps"][
            "initialSearchData"
        ]["layout"]["sections"]["mainContentCollection"][
            "components"
        ]
    except (KeyError, TypeError):
        return []

    tiles = []
    for component in components:
        product_tiles = (component.get("data") or {}).get("productTiles")

        if isinstance(product_tiles, list):
            tiles.extend(product_tiles)

    return tiles


def parse_search_results(html, search_term, observed_date=None):
    """Parse the embedded No Frills search data into raw listings."""
    soup = BeautifulSoup(html, "html.parser")
    data_node = soup.select_one("script#__NEXT_DATA__")

    if data_node is None or not data_node.string:
        return []

    try:
        page_data = json.loads(data_node.string)
    except json.JSONDecodeError:
        return []

    return parse_product_tiles(
        _extract_product_tiles(page_data),
        search_term=search_term,
        observed_date=observed_date,
    )


def _listed_price_warning(listing, normalized):
    if listing.get("is_variable_weight"):
        return None

    required_values = (
        listing.get("displayed_price"),
        listing.get("package_size"),
        listing.get("package_unit"),
        listing.get("listed_unit_price"),
        listing.get("listed_unit"),
        normalized.get("standardized_unit"),
    )

    if any(value is None or value == "" for value in required_values):
        return None

    try:
        package_price = calculate_price_per_standard_unit(
            price=listing["displayed_price"],
            package_size=listing["package_size"],
            package_unit=listing["package_unit"],
            standardized_unit=normalized["standardized_unit"],
        )
        listed_price = calculate_price_per_standard_unit(
            price=None,
            package_size=None,
            package_unit=None,
            standardized_unit=normalized["standardized_unit"],
            is_variable_weight=True,
            listed_unit_price=listing["listed_unit_price"],
            listed_unit=listing["listed_unit"],
        )
    except (TypeError, ValueError):
        return None

    if not math.isfinite(package_price) or not math.isfinite(listed_price):
        return None

    smaller_price = min(package_price, listed_price)
    larger_price = max(package_price, listed_price)

    if smaller_price == 0:
        factor = math.inf if larger_price > 0 else 1.0
    else:
        factor = larger_price / smaller_price

    if factor < IMPLAUSIBLE_UNIT_PRICE_FACTOR:
        return None

    factor_text = "infinite" if math.isinf(factor) else f"{factor:.2f}x"
    return (
        "Listed unit price differs from the package-derived price by "
        f"{factor_text}; package-derived normalization was retained."
    )


def normalize_listing(listing):
    """Normalize one row and apply No Frills data-quality checks."""
    normalized = _normalize_listing(listing)
    normalized["data_quality_warning"] = _listed_price_warning(
        listing,
        normalized,
    )

    expected_unit = expected_standardized_unit(
        normalized["comparison_group"]
    )
    actual_unit = normalized.get("standardized_unit")

    if (
        normalized["normalization_error"] is None
        and expected_unit is not None
        and actual_unit != expected_unit
    ):
        normalized["price_per_standard_unit"] = None
        normalized["normalization_error"] = (
            f"Incompatible comparison unit for "
            f"{normalized['comparison_group']}: expected {expected_unit}, "
            f"found {actual_unit}."
        )

    return normalized


def normalize_listings(listings):
    return [normalize_listing(listing) for listing in listings]


def collect_nofrills_listings(
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
                f"No No Frills product records were found for "
                f"{search_term!r}; the page may have been blocked or its "
                "embedded data may have changed."
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
        output_columns=NOFRILLS_OUTPUT_COLUMNS,
    )


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Collect No Frills Canada grocery search listings and write a "
            "classified, price-normalized CSV."
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
    listings = collect_nofrills_listings(
        search_terms=tuple(search_terms),
        max_results_per_term=args.max_results,
        delay_seconds=args.delay_seconds,
    )
    output_path = save_listings(listings, args.output)
    print(f"Saved {len(listings)} listings to {output_path}")


if __name__ == "__main__":
    main()
