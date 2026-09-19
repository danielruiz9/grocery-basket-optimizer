"""Collect and normalize Walmart Canada grocery search listings.

The parser targets fields rendered in Walmart search-result product cards. Run
the module directly to collect the default grocery searches and write a CSV:

    python -m src.collectors.walmart
"""

import argparse
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from src.collectors.common import (
    DEFAULT_SEARCH_TERMS,
    OUTPUT_COLUMNS,
    normalize_listing,
    normalize_listings,
    save_listings as _save_listings,
)


WALMART_BASE_URL = "https://www.walmart.ca"
WALMART_SEARCH_URL = f"{WALMART_BASE_URL}/en/search"
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "walmart_prices_normalized.csv"
)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GroceryBasketOptimizer/0.1)",
    "Accept-Language": "en-CA,en;q=0.9",
}

_NUMBER_PATTERN = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
_RANGE_PATTERN = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*[-\u2013]\s*"
    r"(?P<high>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|g|ml|l|lbs?)\b",
    re.IGNORECASE,
)
_MULTIPACK_PATTERN = re.compile(
    r"(?P<count>\d+)\s*[x\u00d7]\s*"
    r"(?P<size>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|g|ml|l|lbs?)\b",
    re.IGNORECASE,
)
_PACKAGE_PATTERN = re.compile(
    r"(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|ml|l|lbs?)\b",
    re.IGNORECASE,
)
_COUNT_PATTERN = re.compile(
    r"(?P<size>\d+)\s*(?P<unit>count|ct|pack|pk|pieces?|pcs?)\b",
    re.IGNORECASE,
)
_EGG_COUNT_PATTERN = re.compile(
    r"(?P<size>\d+)\s*"
    r"(?:(?:extra\s+large|large|medium|small)\s+)?"
    r"(?:(?:white|brown)\s+)?eggs?\b",
    re.IGNORECASE,
)
_EGG_TRAILING_COUNT_PATTERN = re.compile(
    r"(?P<size>\d+)\s*eggs?\b",
    re.IGNORECASE,
)


def fetch_search_results(search_term, session=None, timeout=30):
    """Fetch one Walmart Canada search-result page."""
    client = session or requests.Session()
    response = client.get(
        WALMART_SEARCH_URL,
        params={"q": search_term},
        headers=DEFAULT_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()

    if urlsplit(response.url).path == "/blocked":
        raise RuntimeError(
            "Walmart Canada redirected the request to its blocked page."
        )

    return response.text


def _parse_money(value):
    if not value:
        return None

    return float(value.replace(",", ""))


def _canonical_package_unit(unit):
    normalized = unit.lower().strip()

    count_units = {
        "ct", "count", "pack", "pk", "piece", "pieces", "pc", "pcs"
    }

    if normalized in count_units:
        return "count"

    if normalized == "lbs":
        return "lb"

    return normalized


def extract_package_details(product_title):
    """Extract a displayed package expression without estimating ranges."""
    if not product_title:
        return {
            "package_size_text": None,
            "package_size": None,
            "package_unit": None,
        }

    multipack_match = _MULTIPACK_PATTERN.search(product_title)

    if multipack_match:
        count = float(multipack_match.group("count"))
        size = float(multipack_match.group("size"))
        return {
            "package_size_text": multipack_match.group(0),
            "package_size": count * size,
            "package_unit": _canonical_package_unit(
                multipack_match.group("unit")
            ),
        }

    range_matches = list(_RANGE_PATTERN.finditer(product_title))

    if range_matches:
        range_match = range_matches[-1]
        return {
            "package_size_text": range_match.group(0),
            "package_size": None,
            "package_unit": _canonical_package_unit(
                range_match.group("unit")
            ),
        }

    package_matches = list(_PACKAGE_PATTERN.finditer(product_title))

    if package_matches:
        package_match = package_matches[-1]
        return {
            "package_size_text": package_match.group(0),
            "package_size": float(package_match.group("size")),
            "package_unit": _canonical_package_unit(
                package_match.group("unit")
            ),
        }

    count_matches = list(_COUNT_PATTERN.finditer(product_title))

    if count_matches:
        count_match = count_matches[-1]
        return {
            "package_size_text": count_match.group(0),
            "package_size": float(count_match.group("size")),
            "package_unit": "count",
        }

    egg_count_match = (
        _EGG_COUNT_PATTERN.search(product_title)
        or _EGG_TRAILING_COUNT_PATTERN.search(product_title)
    )

    if egg_count_match:
        return {
            "package_size_text": egg_count_match.group(0),
            "package_size": float(egg_count_match.group("size")),
            "package_unit": "count",
        }

    return {
        "package_size_text": None,
        "package_size": None,
        "package_unit": None,
    }


def _extract_unit_price(unit_price_text):
    if not unit_price_text:
        return None, None

    match = re.search(
        rf"(?:\$\s*(?P<dollars>{_NUMBER_PATTERN})|"
        rf"(?P<cents>{_NUMBER_PATTERN})\s*\u00a2)\s*/\s*"
        rf"(?P<unit>[^\s]+(?:\s*[^\s]+)?)",
        unit_price_text,
        re.IGNORECASE,
    )

    if not match:
        return None, None

    listed_unit_price = (
        _parse_money(match.group("dollars"))
        if match.group("dollars") is not None
        else _parse_money(match.group("cents")) / 100
    )
    listed_unit = re.sub(r"\s+", "", match.group("unit").lower())
    listed_unit = {
        "ea": "1unit",
        "1ea": "1unit",
        "each": "1unit",
        "unit": "1unit",
    }.get(listed_unit, listed_unit)
    return listed_unit_price, listed_unit


def _canonical_product_url(href):
    if not href:
        return None

    absolute_url = urljoin(WALMART_BASE_URL, href)
    parsed = urlsplit(absolute_url)

    if parsed.path == "/wapcrs/track":
        destination = parse_qs(parsed.query).get("rd", [None])[0]

        if destination:
            absolute_url = urljoin(WALMART_BASE_URL, destination)
            parsed = urlsplit(absolute_url)

    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def _extract_displayed_prices(price_text):
    if not price_text:
        return None, None

    def price_after(label_pattern):
        match = re.search(
            rf"{label_pattern}\s*"
            rf"(?:\$\s*(?P<dollars>{_NUMBER_PATTERN})|"
            rf"(?P<cents>{_NUMBER_PATTERN})\s*\u00a2)",
            price_text,
            re.IGNORECASE,
        )

        if not match:
            return None

        if match.group("dollars") is not None:
            return _parse_money(match.group("dollars"))

        return _parse_money(match.group("cents")) / 100

    current_price = price_after(r"current\s+price(?:\s+Now)?")
    regular_price = price_after(r"\bWas")
    return current_price, regular_price


def _extract_sale_status(card, regular_price):
    sale_terms = ("rollback", "clearance", "reduced price", "sale")

    for badge in card.select('[data-testid="tag-leading-badge"]'):
        badge_text = badge.get_text(" ", strip=True)

        if any(term in badge_text.lower() for term in sale_terms):
            return badge_text

    if regular_price is not None:
        return "sale"

    return None


def parse_search_results(html, search_term, observed_date=None):
    """Parse Walmart product cards into raw listing dictionaries."""
    soup = BeautifulSoup(html, "html.parser")
    observed_date = observed_date or date.today().isoformat()
    listings = []
    seen_item_ids = set()

    for card in soup.select('[data-item-id][role="group"]'):
        item_id = card.get("data-item-id")

        if item_id and item_id in seen_item_ids:
            continue

        title_node = card.select_one(
            '[data-automation-id="product-title"]'
        )
        title = (
            title_node.get_text(" ", strip=True)
            if title_node is not None
            else None
        )

        if not title:
            image = card.select_one(
                'img[data-testid="productTileImage"][alt]'
            )
            title = image.get("alt", "").strip() if image else None

        if not title:
            continue

        price_node = card.select_one(
            '[data-automation-id="product-price"]'
        )
        price_text = (
            price_node.get_text(" ", strip=True)
            if price_node is not None
            else ""
        )
        displayed_price, regular_price = _extract_displayed_prices(
            price_text
        )

        unit_price_node = card.select_one(
            '[data-testid="product-price-per-unit"]'
        )
        unit_price_text = (
            unit_price_node.get_text(" ", strip=True)
            if unit_price_node is not None
            else None
        )
        listed_unit_price, listed_unit = _extract_unit_price(
            unit_price_text
        )

        card_text = card.get_text(" ", strip=True)
        is_variable_weight = (
            "final cost by weight" in card_text.lower()
            or "avg price" in price_text.lower()
        )
        sale_status = _extract_sale_status(card, regular_price)

        link = card.select_one("a[link-identifier][href]")
        if link is None:
            link = card.select_one('a[href*="/en/ip/"]')

        listing = {
            "date_observed": str(observed_date),
            "search_term": search_term,
            "store": "Walmart Canada",
            "product_title": title,
            "product_url": _canonical_product_url(
                link.get("href") if link else None
            ),
            **extract_package_details(title),
            "displayed_price": displayed_price,
            "unit_price_text": unit_price_text,
            "listed_unit_price": listed_unit_price,
            "listed_unit": listed_unit,
            "is_variable_weight": is_variable_weight,
            "sale_status": sale_status,
            "sale_price": (
                displayed_price if sale_status is not None else None
            ),
            "regular_price": regular_price,
        }
        listings.append(listing)

        if item_id:
            seen_item_ids.add(item_id)

    return listings


def collect_walmart_listings(
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
                f"No Walmart product cards were found for {search_term!r}; "
                "the page may have been blocked or its markup may have changed."
            )

        if max_results_per_term is not None:
            parsed = parsed[:max_results_per_term]

        listings.extend(parsed)

        if delay_seconds and index < len(search_terms) - 1:
            time.sleep(delay_seconds)

    return normalize_listings(listings)


def save_listings(listings, output_path=DEFAULT_OUTPUT_PATH):
    return _save_listings(listings, output_path)


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Collect Walmart Canada grocery search listings and write a "
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
    listings = collect_walmart_listings(
        search_terms=tuple(search_terms),
        max_results_per_term=args.max_results,
        delay_seconds=args.delay_seconds,
    )
    output_path = save_listings(listings, args.output)
    print(f"Saved {len(listings)} listings to {output_path}")


if __name__ == "__main__":
    main()
