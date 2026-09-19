"""Shared classification, price normalization, and CSV output helpers."""

import math
from pathlib import Path

import pandas as pd

from src.classification import classify_product, expected_standardized_unit
from src.pricing import calculate_price_per_standard_unit


DEFAULT_SEARCH_TERMS = (
    "chicken breast",
    "eggs",
    "apples",
    "bananas",
    "pasta",
    "bread",
    "cheese",
)

OUTPUT_COLUMNS = (
    "date_observed",
    "search_term",
    "store",
    "product_title",
    "product_url",
    "package_size_text",
    "package_size",
    "package_unit",
    "displayed_price",
    "unit_price_text",
    "listed_unit_price",
    "listed_unit",
    "is_variable_weight",
    "sale_status",
    "sale_price",
    "regular_price",
    "category",
    "product_family",
    "comparison_group",
    "product_form",
    "attributes",
    "standardized_unit",
    "price_per_standard_unit",
    "normalization_error",
)
QUALITY_OUTPUT_COLUMNS = (
    *OUTPUT_COLUMNS[:-1],
    "data_quality_warning",
    OUTPUT_COLUMNS[-1],
)
IMPLAUSIBLE_UNIT_PRICE_FACTOR = 5.0


def _standardized_unit(listing):
    if listing.get("is_variable_weight"):
        unit = listing.get("listed_unit") or listing.get("package_unit")
    else:
        unit = listing.get("package_unit") or listing.get("listed_unit")

    if not unit:
        return None

    normalized = str(unit).lower().replace(" ", "")

    if normalized in {"g", "kg", "100g", "lb", "lbs"}:
        return "100g"

    if normalized in {"ml", "100ml", "l", "1l"}:
        return "1L"

    if normalized in {"count", "1unit", "ea", "each"}:
        return "1unit"

    return None


def normalize_listing(listing):
    """Add classification and standardized pricing to one raw listing."""
    normalized = dict(listing)
    classification = classify_product(listing.get("product_title"))
    normalized.update(classification)
    normalized["standardized_unit"] = _standardized_unit(listing)
    normalized["price_per_standard_unit"] = None
    normalized["normalization_error"] = None

    if normalized["standardized_unit"] is None:
        normalized["normalization_error"] = (
            "No supported package or listed unit was available."
        )
        return normalized

    try:
        normalized["price_per_standard_unit"] = (
            calculate_price_per_standard_unit(
                price=listing.get("displayed_price"),
                package_size=listing.get("package_size"),
                package_unit=listing.get("package_unit"),
                standardized_unit=normalized["standardized_unit"],
                is_variable_weight=listing.get(
                    "is_variable_weight", False
                ),
                listed_unit_price=listing.get("listed_unit_price"),
                listed_unit=listing.get("listed_unit"),
            )
        )
    except (TypeError, ValueError) as exc:
        normalized["normalization_error"] = str(exc)

    return normalized


def normalize_listings(listings):
    return [normalize_listing(listing) for listing in listings]


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


def normalize_listing_with_quality_checks(listing):
    """Normalize one row and enforce comparison-data quality rules."""
    normalized = normalize_listing(listing)
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


def normalize_listings_with_quality_checks(listings):
    return [
        normalize_listing_with_quality_checks(listing)
        for listing in listings
    ]


def save_listings(listings, output_path, output_columns=OUTPUT_COLUMNS):
    """Write normalized listings to CSV with a stable column order."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for listing in listings:
        row = dict(listing)
        row["attributes"] = "|".join(row.get("attributes", []))
        rows.append(row)

    prices = pd.DataFrame(rows, columns=output_columns)
    prices.to_csv(output_path, index=False)
    return output_path
