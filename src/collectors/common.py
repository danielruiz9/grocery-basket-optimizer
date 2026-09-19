"""Shared classification, price normalization, and CSV output helpers."""

from pathlib import Path

import pandas as pd

from src.classification import classify_product
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
