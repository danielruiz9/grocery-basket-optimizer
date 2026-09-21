"""Collect and normalize Sobeys Canada browser-rendered search listings.

Sobeys' catalogue shell is server rendered, but search results and store-specific
prices are populated in the browser. Ordinary requests are attempted once and
never used to bypass a block. For reliable collection, save a public browser
capture containing ``observed_date`` and ``searches`` (term -> card records):

    python -m src.collectors.sobeys --capture-json capture.json

Rendered HTML captured from a normal browser session is also supported via
``--html-dir``. Prices reflect the store selected in that browser session. The
default retains the first 12 results per search, including unsupported noise.
This module does not add Sobeys to integration or the Streamlit application.
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

from src.collectors.common import (
    DEFAULT_SEARCH_TERMS,
    QUALITY_OUTPUT_COLUMNS,
    normalize_listing_with_quality_checks,
    save_listings as _save_listings,
)
from src.collectors.nofrills import extract_package_details


SOBEYS_BASE_URL = "https://www.sobeys.com"
SOBEYS_SEARCH_URL = f"{SOBEYS_BASE_URL}/"
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "sobeys_prices_normalized.csv"
)
SOBEYS_OUTPUT_COLUMNS = (
    *QUALITY_OUTPUT_COLUMNS[:16],
    "multi_buy_quantity",
    "multi_buy_unit_price",
    "single_item_price",
    *QUALITY_OUTPUT_COLUMNS[16:],
    "displayed_price_text",
    "promotion_text",
    "availability",
    "product_id",
    "store_context",
)

_NUMBER = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
_MONEY = re.compile(rf"\$\s*(?P<amount>{_NUMBER})")
_UNIT_PRICE = re.compile(
    rf"\$\s*(?P<price>{_NUMBER})\s*(?:/|per\s+)\s*"
    r"(?P<amount>\d+(?:\.\d+)?)?\s*"
    r"(?P<unit>kg|g|ml|l|lbs?|ea|each)\b",
    re.IGNORECASE,
)
_MULTI_BUY = re.compile(
    rf"^\s*buy\s+(?P<count>\d+)\s+for\s+\$\s*(?P<total>{_NUMBER})\s*$",
    re.IGNORECASE,
)
_VARIABLE_RATE = re.compile(
    rf"\$\s*(?P<price>{_NUMBER})\s*/\s*(?P<unit>kg|lbs?)\b",
    re.IGNORECASE,
)
_TITLE_WEIGHT = re.compile(
    r"(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|lbs?)\b",
    re.IGNORECASE,
)
_SALE_BADGES = {"lockedprice", "savethisweek", "weeklyspecial"}


def fetch_search_results(search_term, session=None, timeout=30):
    """Make one ordinary search request; never retry around a protection."""
    client = session or requests.Session()
    response = client.get(
        SOBEYS_SEARCH_URL,
        params={"query": search_term, "tab": "products"},
        timeout=timeout,
    )
    if response.status_code in {401, 403, 429}:
        raise RuntimeError(
            f"Sobeys Canada blocked the request with HTTP {response.status_code}. "
            "Use a normally accessible browser capture; do not bypass the block."
        )
    response.raise_for_status()
    if "access denied" in response.text.lower():
        raise RuntimeError("Sobeys Canada returned an access-denied page.")
    return response.text


def _parse_money(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _MONEY.search(str(value))
    return float(match.group("amount").replace(",", "")) if match else None


def _canonical_unit(unit):
    normalized = str(unit).strip().lower()
    if normalized in {"ea", "each"}:
        return "count"
    if normalized == "lbs":
        return "lb"
    return normalized


def _canonical_product_url(href):
    if not href:
        return None
    parsed = urlsplit(urljoin(SOBEYS_BASE_URL, href))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _extract_unit_price(text):
    if not text:
        return None, None, None
    match = _UNIT_PRICE.search(str(text))
    if not match:
        return None, None, None
    price = float(match.group("price").replace(",", ""))
    amount = float(match.group("amount") or 1)
    unit = _canonical_unit(match.group("unit"))
    if unit == "count":
        listed_unit = "1unit" if amount == 1 else f"{amount:g}unit"
    elif amount == 1:
        listed_unit = unit
    else:
        listed_unit = f"{amount:g}{unit}"
    return match.group(0), price, listed_unit


def _extract_prices(price_text, promotion_text, badge_text):
    text = str(price_text or "")
    amounts = [
        float(match.group("amount").replace(",", ""))
        for match in _MONEY.finditer(text)
    ]
    displayed_price = amounts[0] if amounts else None
    regular_price = (
        amounts[1]
        if len(amounts) > 1 and "save" in text.lower()
        else None
    )
    multi_buy_quantity = None
    multi_buy_unit_price = None
    single_item_price = None
    multi_match = _MULTI_BUY.fullmatch(str(promotion_text or "").strip())
    if multi_match:
        multi_buy_quantity = int(multi_match.group("count")) or None
        total = float(multi_match.group("total").replace(",", ""))
        multi_buy_unit_price = (
            total / multi_buy_quantity if multi_buy_quantity else None
        )
        single_item_price = displayed_price
        sale_status = "multi-buy"
        sale_price = multi_buy_unit_price
    else:
        has_sale_badge = any(
            badge.strip().lower() in _SALE_BADGES
            for badge in str(badge_text or "").split(";")
        )
        sale_status = "sale" if regular_price is not None or has_sale_badge else None
        sale_price = displayed_price if sale_status else None
    return {
        "displayed_price": displayed_price,
        "sale_status": sale_status,
        "sale_price": sale_price,
        "regular_price": regular_price,
        "multi_buy_quantity": multi_buy_quantity,
        "multi_buy_unit_price": multi_buy_unit_price,
        "single_item_price": single_item_price,
    }


def _is_variable_weight(price_text):
    return _VARIABLE_RATE.search(str(price_text or "")) is not None


def _title_weight(product_title):
    matches = list(_TITLE_WEIGHT.finditer(str(product_title or "")))
    if not matches:
        return None
    match = matches[-1]
    unit = _canonical_unit(match.group("unit"))
    return {
        "package_size_text": match.group(0),
        "package_size": float(match.group("size")),
        "package_unit": unit,
    }


def _weight_in_grams(size, unit):
    factors = {"g": 1.0, "kg": 1000.0, "lb": 453.59237}
    return float(size) * factors[unit]


def _apply_title_weight_fallback(package, product_title, variable_weight):
    """Resolve title weights without overriding authoritative weight data."""
    if variable_weight:
        return package, None

    title_package = _title_weight(product_title)
    if title_package is None:
        return package, None

    package_unit = package.get("package_unit")
    package_size = package.get("package_size")
    if package_unit == "count" and package_size == 1:
        captured_text = package.get("package_size_text") or "1 EA"
        return title_package, (
            f"Structured package data reports {captured_text}; explicit "
            f"title weight {title_package['package_size_text']} was used "
            "for normalization."
        )

    if package_unit in {"g", "kg", "lb"} and package_size is not None:
        captured_grams = _weight_in_grams(package_size, package_unit)
        title_grams = _weight_in_grams(
            title_package["package_size"], title_package["package_unit"]
        )
        if not math.isclose(captured_grams, title_grams, rel_tol=0.02):
            return package, (
                f"Title package weight {title_package['package_size_text']} "
                f"differs from captured structured package weight "
                f"{package.get('package_size_text')}; captured structured "
                "data was retained."
            )

    return package, None


def _extract_rendered_records(html):
    """Project public fields from the rendered product grid into card records."""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for card in soup.select("div[data-id]"):
        link = card.select_one('a[href^="/products/"]')
        if link is None:
            continue
        paragraphs = [
            node.get_text(" ", strip=True)
            for node in card.select("p")
            if node.get_text(" ", strip=True)
        ]
        title_node = card.select_one("p.card-title")
        title = title_node.get_text(" ", strip=True) if title_node else None
        if not title:
            title = link.get("title")
        if not title:
            aria = link.get("aria-label") or ""
            match = re.match(
                r"Click here to go to (.+) product detail page$", aria
            )
            title = match.group(1) if match else None
        package_text = next(
            (text for text in paragraphs if " per " in text.lower()), None
        )
        price_text = next(
            (
                text for text in paragraphs
                if "$" in text and text != package_text
                and not text.lower().startswith("buy ")
            ),
            None,
        )
        promotion_text = next(
            (text for text in paragraphs if text.lower().startswith("buy ")),
            None,
        )
        badges = "; ".join(
            alt for alt in (image.get("alt") for image in card.select("img"))
            if alt and alt.strip().lower() in _SALE_BADGES
        )
        records.append(
            {
                "product_id": card.get("data-id"),
                "product_title": title,
                "product_url": link.get("href"),
                "price_text": price_text,
                "package_text": package_text,
                "promotion_text": promotion_text,
                "badge_text": badges,
                "availability": (
                    "out_of_stock"
                    if "out of stock" in card.get_text(" ", strip=True).lower()
                    else "in_stock"
                ),
            }
        )
    return records


def parse_product_records(
    records, search_term, observed_date=None, store_context=None
):
    """Convert explicit Sobeys card fields to the shared raw-listing schema."""
    observed_date = observed_date or date.today().isoformat()
    listings = []
    seen = set()
    for record in records:
        key = (
            record.get("product_id") or record.get("product_url")
            or record.get("product_title")
        )
        if key and key in seen:
            continue
        if key:
            seen.add(key)

        package_text = record.get("package_text")
        package_head = str(package_text or "").split("(", 1)[0].strip()
        package = extract_package_details(package_head)
        package["package_size_text"] = package_text or None
        unit_price_text, listed_unit_price, listed_unit = _extract_unit_price(
            package_text
        )
        variable_match = _VARIABLE_RATE.search(str(record.get("price_text") or ""))
        variable_weight = variable_match is not None
        if variable_match:
            unit_price_text = variable_match.group(0)
            listed_unit_price = float(
                variable_match.group("price").replace(",", "")
            )
            listed_unit = _canonical_unit(variable_match.group("unit"))
            package["package_size"] = None
            package["package_unit"] = None
        package, package_data_warning = _apply_title_weight_fallback(
            package,
            record.get("product_title"),
            variable_weight,
        )

        pricing = _extract_prices(
            record.get("price_text"),
            record.get("promotion_text"),
            record.get("badge_text"),
        )
        listings.append(
            {
                "date_observed": str(observed_date),
                "search_term": search_term,
                "store": "Sobeys Canada",
                "product_title": record.get("product_title"),
                "product_url": _canonical_product_url(record.get("product_url")),
                **package,
                **pricing,
                "unit_price_text": unit_price_text,
                "listed_unit_price": listed_unit_price,
                "listed_unit": listed_unit,
                "is_variable_weight": variable_weight,
                "displayed_price_text": record.get("price_text"),
                "promotion_text": record.get("promotion_text") or None,
                "availability": record.get("availability") or None,
                "product_id": record.get("product_id") or None,
                "store_context": store_context,
                "package_data_warning": package_data_warning,
            }
        )
    return listings


def parse_search_results(
    content, search_term, observed_date=None, store_context=None
):
    """Accept rendered HTML or a public browser-card JSON projection."""
    if content.lstrip().startswith("{"):
        try:
            data = json.loads(content)
            records = data.get("records", [])
        except (json.JSONDecodeError, AttributeError):
            return []
    else:
        records = _extract_rendered_records(content)
    return parse_product_records(
        records, search_term, observed_date, store_context
    )


def capture_records(capture, search_term):
    """Expand either object records or compact field-aligned capture rows."""
    records = capture["searches"][search_term]
    fields = capture.get("fields")
    if not fields:
        return records
    expanded = []
    for row in records:
        if len(row) != len(fields):
            raise ValueError(
                f"Sobeys capture row for {search_term!r} has {len(row)} "
                f"values; expected {len(fields)}."
            )
        expanded.append(dict(zip(fields, row)))
    return expanded


def normalize_listing(listing):
    normalized = normalize_listing_with_quality_checks(listing)
    warnings = [
        warning
        for warning in (
            normalized.get("data_quality_warning"),
            listing.get("package_data_warning"),
        )
        if warning
    ]
    normalized["data_quality_warning"] = "; ".join(warnings) or None
    if (
        listing.get("sale_status") == "multi-buy"
        and listing.get("single_item_price") is None
    ):
        normalized["price_per_standard_unit"] = None
        normalized["normalization_error"] = (
            "Multi-buy listing has no explicit single-item price; "
            "unconditional price cannot be normalized."
        )
    return normalized


def normalize_listings(listings):
    return [normalize_listing(listing) for listing in listings]


def collect_sobeys_listings(
    search_terms=DEFAULT_SEARCH_TERMS,
    max_results_per_term=12,
    observed_date=None,
    delay_seconds=1.0,
    fetcher=None,
    store_context=None,
):
    """Collect from ordinary responses or an injected browser-capture reader."""
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
        parsed = parse_search_results(
            fetcher(term), term, observed_date, store_context
        )
        if not parsed:
            raise RuntimeError(
                f"No Sobeys search product cards found for {term!r}; the "
                "response may be a server shell, blocked, or not rendered. "
                "Use a normally accessible browser capture. No output saved."
            )
        rows.extend(parsed[:max_results_per_term])
        if delay_seconds and index < len(terms) - 1:
            time.sleep(delay_seconds)
    return normalize_listings(rows)


def save_listings(listings, output_path=DEFAULT_OUTPUT_PATH):
    return _save_listings(listings, output_path, SOBEYS_OUTPUT_COLUMNS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-term", action="append", dest="search_terms")
    parser.add_argument("--max-results", type=int, default=12)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--capture-json", type=Path)
    source.add_argument("--html-dir", type=Path)
    parser.add_argument("--observed-date", type=date.fromisoformat)
    args = parser.parse_args(argv)

    observed_date = args.observed_date
    store_context = None
    fetcher = None
    if args.capture_json:
        capture = json.loads(args.capture_json.read_text(encoding="utf-8"))
        observed_date = date.fromisoformat(capture["observed_date"])
        store_context = capture.get("store_context")
        if args.observed_date and args.observed_date != observed_date:
            parser.error("--observed-date must match the capture date.")

        def fetcher(term):
            return json.dumps({"records": capture_records(capture, term)})

    if args.html_dir:
        if observed_date is None:
            parser.error("--observed-date is required for saved HTML captures.")

        def fetcher(term):
            filename = term.replace(" ", "_") + ".html"
            if Path(filename).name != filename:
                raise ValueError("Search term must not contain path separators.")
            return (args.html_dir / filename).read_text(encoding="utf-8")

    listings = collect_sobeys_listings(
        search_terms=args.search_terms or DEFAULT_SEARCH_TERMS,
        max_results_per_term=args.max_results,
        observed_date=observed_date,
        delay_seconds=args.delay_seconds,
        fetcher=fetcher,
        store_context=store_context,
    )
    output = save_listings(listings, args.output)
    print(f"Saved {len(listings)} Sobeys listings to {output}")


if __name__ == "__main__":
    main()
