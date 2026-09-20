"""Product-tile parsing shared by the Metro and Food Basics storefronts."""

import re
from datetime import date
from urllib.parse import urljoin, urlsplit, urlunsplit

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
    """Parse package size text shown on a Metro-family product tile."""
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


def _canonical_product_url(href, base_url):
    if not href:
        return None

    absolute_url = urljoin(base_url, href)
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


def parse_product_records(
    records, search_term, observed_date=None, *, store, base_url
):
    """Convert explicit Metro-family product-tile fields to raw listings."""
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
                "store": store,
                "product_title": title,
                "product_url": _canonical_product_url(record.get("href"), base_url),
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
