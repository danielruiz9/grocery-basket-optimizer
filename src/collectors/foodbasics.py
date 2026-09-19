"""Collect and normalize Food Basics Canada grocery search listings.

Food Basics renders product data in server-side product tiles. Run the module
directly to collect the default grocery searches and write a CSV:

    python -m src.collectors.foodbasics
"""

import argparse
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from src.collectors.common import (
    DEFAULT_SEARCH_TERMS,
    QUALITY_OUTPUT_COLUMNS,
    normalize_listing_with_quality_checks as _normalize_listing,
    save_listings as _save_listings,
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

_NUMBER_PATTERN = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
_MULTIPACK_PATTERN = re.compile(
    r"(?P<count>\d+)\s*[x\u00d7]\s*"
    r"(?P<size>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|g|ml|l|lbs?|un|units?|ea|each|count|ct)\b\.?",
    re.IGNORECASE,
)
_PACKAGE_PATTERN = re.compile(
    r"(?P<size>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|g|ml|l|lbs?|un|units?|ea|each|count|ct)\b\.?",
    re.IGNORECASE,
)
_UNIT_PRICE_PATTERN = re.compile(
    rf"\$\s*(?P<price>{_NUMBER_PATTERN})\s*/\s*"
    r"(?P<amount>\d+(?:\.\d+)?)?\s*"
    r"(?P<unit>kg|g|ml|l|lbs?|un|units?|ea|each)\b\.?",
    re.IGNORECASE,
)
_MULTIBUY_PATTERN = re.compile(
    rf"^\s*(?P<count>\d+)\s*/\s*\$\s*"
    rf"(?P<total>{_NUMBER_PATTERN})",
    re.IGNORECASE,
)
_SINGLE_ITEM_PRICE_PATTERN = re.compile(
    rf"\bor\s*\$\s*(?P<price>{_NUMBER_PATTERN})\s*"
    r"(?:ea|each)\b\.?,?",
    re.IGNORECASE,
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
    normalized = unit.lower().strip().rstrip(".")

    if normalized in {
        "un",
        "unit",
        "units",
        "ea",
        "each",
        "count",
        "ct",
    }:
        return "count"

    if normalized == "lbs":
        return "lb"

    return normalized


def extract_package_details(package_text):
    """Parse package size text shown on a Food Basics product tile."""
    if not package_text:
        return {
            "package_size_text": None,
            "package_size": None,
            "package_unit": None,
        }

    package_text = str(package_text).strip()

    if not package_text:
        return {
            "package_size_text": None,
            "package_size": None,
            "package_unit": None,
        }

    multipack_match = _MULTIPACK_PATTERN.search(package_text)

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

    package_matches = list(_PACKAGE_PATTERN.finditer(package_text))

    if package_matches:
        package_match = package_matches[-1]
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


def _extract_unit_price(unit_price_text):
    if not unit_price_text:
        return None, None

    match = _UNIT_PRICE_PATTERN.search(unit_price_text)

    if not match:
        return None, None

    listed_unit_price = _parse_money(match.group("price"))
    amount = float(match.group("amount") or 1)
    unit = _canonical_package_unit(match.group("unit"))

    if unit == "count":
        listed_unit = "1unit" if amount == 1 else f"{amount:g}unit"
    elif amount == 1:
        listed_unit = unit
    else:
        listed_unit = f"{amount:g}{unit}"

    return listed_unit_price, listed_unit


def _extract_current_price(current_price_text, secondary_price_text=None):
    if not current_price_text:
        return None, None, None, None, None

    single_item_match = _SINGLE_ITEM_PRICE_PATTERN.search(
        secondary_price_text or ""
    )
    single_item_price = (
        _parse_money(single_item_match.group("price"))
        if single_item_match
        else None
    )
    multibuy_match = _MULTIBUY_PATTERN.search(current_price_text)

    if multibuy_match:
        count = int(multibuy_match.group("count"))
        total = _parse_money(multibuy_match.group("total"))
        multi_buy_unit_price = total / count if count else None
        return (
            single_item_price,
            "multi-buy",
            count or None,
            multi_buy_unit_price,
            single_item_price,
        )

    current_price = _parse_money(current_price_text)

    if single_item_price is not None:
        return (
            single_item_price,
            "multi-buy",
            None,
            current_price,
            single_item_price,
        )

    return current_price, None, None, None, None


def _parse_boolean(value):
    if isinstance(value, bool):
        return value

    if value is None:
        return None

    normalized = str(value).strip().lower()

    if normalized == "true":
        return True

    if normalized == "false":
        return False

    return None


def _canonical_product_url(href):
    if not href:
        return None

    absolute_url = urljoin(FOODBASICS_BASE_URL, href)
    parsed = urlsplit(absolute_url)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def _sale_status(multibuy_status, regular_price, badges):
    if multibuy_status:
        return multibuy_status

    if regular_price is not None:
        return "sale"

    if badges and "save" in badges.lower():
        return "sale"

    return None


def parse_product_records(records, search_term, observed_date=None):
    """Convert explicit Food Basics product-tile fields to raw listings."""
    observed_date = observed_date or date.today().isoformat()
    listings = []
    seen_products = set()

    for record in records:
        product_key = (
            record.get("product_code")
            or record.get("href")
            or record.get("title")
            or record.get("product_name")
        )

        if product_key and product_key in seen_products:
            continue

        title = record.get("title") or record.get("product_name")
        package = extract_package_details(record.get("unit_details"))
        unit_price_text = record.get("secondary_price_text")
        listed_unit_price, listed_unit = _extract_unit_price(
            unit_price_text
        )
        (
            displayed_price,
            multibuy_status,
            multi_buy_quantity,
            multi_buy_unit_price,
            single_item_price,
        ) = _extract_current_price(
            record.get("current_price_text"),
            unit_price_text,
        )
        if multibuy_status and single_item_price is None:
            listed_unit_price = None
            listed_unit = None
        regular_price = _parse_money(record.get("regular_price_text"))
        sale_status = _sale_status(
            multibuy_status,
            regular_price,
            record.get("badges"),
        )

        listings.append(
            {
                "date_observed": str(observed_date),
                "search_term": search_term,
                "store": "Food Basics Canada",
                "product_title": title,
                "product_url": _canonical_product_url(record.get("href")),
                **package,
                "displayed_price": displayed_price,
                "unit_price_text": unit_price_text,
                "listed_unit_price": listed_unit_price,
                "listed_unit": listed_unit,
                "is_variable_weight": _parse_boolean(
                    record.get("is_weighted")
                ),
                "sale_status": sale_status,
                "sale_price": (
                    multi_buy_unit_price
                    if multibuy_status
                    else displayed_price
                    if sale_status is not None
                    else None
                ),
                "regular_price": regular_price,
                "multi_buy_quantity": multi_buy_quantity,
                "multi_buy_unit_price": multi_buy_unit_price,
                "single_item_price": single_item_price,
            }
        )

        if product_key:
            seen_products.add(product_key)

    return listings


def _node_text(node):
    return node.get_text(" ", strip=True) if node is not None else None


def _extract_product_records(soup):
    records = []

    for card in soup.select(
        ".default-product-tile[data-product-code]"
    ):
        link = card.select_one("a.product-details-link[href]")
        records.append(
            {
                "product_code": card.get("data-product-code"),
                "product_name": card.get("data-product-name"),
                "product_category": card.get("data-product-category"),
                "is_weighted": card.get("data-is-weighted"),
                "title": _node_text(card.select_one(".head__title")),
                "unit_details": _node_text(
                    card.select_one(".head__unit-details")
                ),
                "current_price_text": _node_text(
                    card.select_one(".pricing__sale-price")
                ),
                "regular_price_text": _node_text(
                    card.select_one(".pricing__before-price")
                ),
                "secondary_price_text": _node_text(
                    card.select_one(".pricing__secondary-price")
                ),
                "badges": _node_text(
                    card.select_one(".visual__stickers")
                ),
                "href": link.get("href") if link else None,
            }
        )

    return records


def normalize_listing(listing):
    """Normalize one Food Basics row with package-aware form context."""
    normalized = _normalize_listing(listing)

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
