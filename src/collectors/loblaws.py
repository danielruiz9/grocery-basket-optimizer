"""Loblaws search collection using the No Frills-compatible product tiles.

Direct HTTP access can return 403. Use normally accessible browser captures,
never a protection bypass. Read only __NEXT_DATA__ search productTiles, not
recommendations or private session state. The default retains 12 results per
search, including sponsored/search noise. Prices depend on the browser's store.

    python -m src.collectors.loblaws --capture-json capture.json
    python -m src.collectors.loblaws --html-dir captures --observed-date 2026-09-20

JSON captures contain observed_date and searches (term -> native tile records).
The offline fixture is a reduced, public-product-only browser capture; omitted
fields are not inferred. Neither mode updates integration or the Streamlit app.
"""

import argparse
import json
import re
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
from src.collectors.nofrills import (
    _extract_product_tiles,
    _parse_money,
    parse_product_tiles as _parse_product_tiles,
)


LOBLAWS_BASE_URL = "https://www.loblaws.ca"
LOBLAWS_SEARCH_URL = f"{LOBLAWS_BASE_URL}/en/search"
DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "loblaws_prices_normalized.csv"
)
LOBLAWS_OUTPUT_COLUMNS = (
    *QUALITY_OUTPUT_COLUMNS[:16],
    "multi_buy_quantity", "multi_buy_unit_price", "single_item_price",
    *QUALITY_OUTPUT_COLUMNS[16:],
    "displayed_price_text", "promotion_text",
)
_MULTI_PRICE = re.compile(r"\$\s*(\d+(?:\.\d+)?)\s+MIN\s+(\d+)\b", re.I)
_SINGLE_DISPLAY = re.compile(r"(?:about\s+)?\$\s*\d+(?:\.\d+)?", re.I)


def fetch_search_results(search_term, session=None, timeout=30):
    client = session or requests.Session()
    response = client.get(
        LOBLAWS_SEARCH_URL, params={"search-bar": search_term}, timeout=timeout,
    )
    if response.status_code in {401, 403, 429}:
        raise RuntimeError(
            f"Loblaws Canada blocked the request with HTTP {response.status_code}. "
            "Use normally accessible browser captures; do not bypass the block."
        )
    response.raise_for_status()
    if "access denied" in response.text.lower():
        raise RuntimeError("Loblaws Canada returned an access-denied page.")
    return response.text


def parse_product_tiles(tiles, search_term, observed_date=None):
    """Keep single prices distinct from explicitly conditional MULTI deals."""
    rows, seen = [], set()
    for tile in tiles:
        key = tile.get("productId") or tile.get("link") or tile.get("title")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        row = _parse_product_tiles(
            [tile], search_term, observed_date,
            store="Loblaws Canada", base_url=LOBLAWS_BASE_URL,
        )[0]
        pricing, deal = tile.get("pricing") or {}, tile.get("deal") or {}
        text = str(deal.get("text") or "")
        display = str(pricing.get("displayPrice") or "")
        row.update(
            displayed_price_text=display or None, promotion_text=text or None,
            multi_buy_quantity=None, multi_buy_unit_price=None,
            single_item_price=None,
        )
        is_multi = (
            "MULTI" in str(deal.get("type") or deal.get("name") or "").upper()
            or re.search(r"\bMIN\s+\d+\b", text, re.I) is not None
        )
        if is_multi:
            row["sale_status"] = "multi-buy"
            match = _MULTI_PRICE.fullmatch(text.strip())
            if match:
                row["multi_buy_unit_price"] = float(match[1])
                quantity = int(match[2])
                row["multi_buy_quantity"] = quantity if quantity > 0 else None
            # pricing.price is the main tile price, not deal.text's MIN price.
            # Only accept a separate, plainly displayed unconditional amount.
            single = _parse_money(pricing.get("price"))
            if (
                _SINGLE_DISPLAY.fullmatch(display.strip())
                and single == _parse_money(display)
                and single != row["multi_buy_unit_price"]
            ):
                row["single_item_price"] = single
            row["sale_price"] = row["multi_buy_unit_price"]
            # Preserve the displayed amount for audit. Normalization below
            # fails closed if there is no usable unconditional price.
        if row["is_variable_weight"]:
            row["package_size"] = None
            row["package_unit"] = None
        rows.append(row)
    return rows


def parse_search_results(content, search_term, observed_date=None):
    """Accept rendered HTML or a public productTiles JSON projection."""
    if content.lstrip().startswith("{"):
        try:
            tiles = json.loads(content).get("productTiles", [])
        except (json.JSONDecodeError, AttributeError):
            return []
    else:
        node = BeautifulSoup(content, "html.parser").select_one("script#__NEXT_DATA__")
        if node is None or not node.string:
            return []
        try:
            tiles = _extract_product_tiles(json.loads(node.string))
        except json.JSONDecodeError:
            return []
    return parse_product_tiles(tiles, search_term, observed_date)


def normalize_listing(listing):
    normalized = normalize_listing_with_quality_checks(listing)
    if listing.get("sale_status") == "multi-buy" and listing.get("single_item_price") is None:
        normalized["price_per_standard_unit"] = None
        normalized["normalization_error"] = (
            "Multi-buy listing has no explicit single-item price; "
            "unconditional price cannot be normalized."
        )
    return normalized


def normalize_listings(listings):
    return [normalize_listing(row) for row in listings]


def collect_loblaws_listings(
    search_terms=DEFAULT_SEARCH_TERMS, max_results_per_term=12,
    observed_date=None, delay_seconds=1.0, fetcher=None,
):
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
                f"No Loblaws search product tiles found for {term!r}; "
                "the page may be blocked, not rendered, or changed. No output saved."
            )
        rows.extend(parsed[:max_results_per_term])
        if delay_seconds and index < len(terms) - 1:
            time.sleep(delay_seconds)
    return normalize_listings(rows)


def save_listings(listings, output_path=DEFAULT_OUTPUT_PATH):
    return _save_listings(listings, output_path, LOBLAWS_OUTPUT_COLUMNS)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-term", action="append", dest="search_terms")
    parser.add_argument("--max-results", type=int, default=12)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--html-dir", type=Path)
    source.add_argument("--capture-json", type=Path)
    parser.add_argument("--observed-date", type=date.fromisoformat)
    args = parser.parse_args(argv)
    observed_date, fetcher = args.observed_date, None
    if args.capture_json:
        capture = json.loads(args.capture_json.read_text(encoding="utf-8"))
        observed_date = date.fromisoformat(capture["observed_date"])
        if args.observed_date and args.observed_date != observed_date:
            parser.error("--observed-date must match the capture date.")

        def fetcher(term):
            return json.dumps({"productTiles": capture["searches"][term]})
    if args.html_dir:
        if observed_date is None:
            parser.error("--observed-date is required for saved HTML.")

        def fetcher(term):
            filename = term.replace(" ", "_") + ".html"
            if Path(filename).name != filename:
                raise ValueError("Search term must not contain path separators.")
            return (args.html_dir / filename).read_text(encoding="utf-8")
    rows = collect_loblaws_listings(
        args.search_terms or DEFAULT_SEARCH_TERMS, args.max_results,
        observed_date, args.delay_seconds, fetcher,
    )
    print(f"Saved {len(rows)} Loblaws listings to {save_listings(rows, args.output)}")


if __name__ == "__main__":
    main()
