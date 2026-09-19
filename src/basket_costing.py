"""Estimate quantity-aware purchase costs from normalized grocery listings."""

import math
from pathlib import Path

import pandas as pd


DEFAULT_PRICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "optimizer_ready_prices.csv"
)

_UNIT_DEFINITIONS = {
    "g": ("weight", 1.0, "g"),
    "gram": ("weight", 1.0, "g"),
    "grams": ("weight", 1.0, "g"),
    "100g": ("weight", 100.0, "100g"),
    "kg": ("weight", 1000.0, "kg"),
    "1kg": ("weight", 1000.0, "kg"),
    "kilogram": ("weight", 1000.0, "kg"),
    "kilograms": ("weight", 1000.0, "kg"),
    "lb": ("weight", 453.59237, "lb"),
    "lbs": ("weight", 453.59237, "lb"),
    "1lb": ("weight", 453.59237, "lb"),
    "pound": ("weight", 453.59237, "lb"),
    "pounds": ("weight", 453.59237, "lb"),
    "ml": ("volume", 1.0, "mL"),
    "millilitre": ("volume", 1.0, "mL"),
    "millilitres": ("volume", 1.0, "mL"),
    "milliliter": ("volume", 1.0, "mL"),
    "milliliters": ("volume", 1.0, "mL"),
    "100ml": ("volume", 100.0, "100mL"),
    "l": ("volume", 1000.0, "L"),
    "1l": ("volume", 1000.0, "L"),
    "litre": ("volume", 1000.0, "L"),
    "litres": ("volume", 1000.0, "L"),
    "liter": ("volume", 1000.0, "L"),
    "liters": ("volume", 1000.0, "L"),
    "count": ("count", 1.0, "count"),
    "unit": ("count", 1.0, "count"),
    "units": ("count", 1.0, "count"),
    "1unit": ("count", 1.0, "count"),
    "ea": ("count", 1.0, "count"),
    "1ea": ("count", 1.0, "count"),
    "each": ("count", 1.0, "count"),
    "un": ("count", 1.0, "count"),
    "egg": ("count", 1.0, "count"),
    "eggs": ("count", 1.0, "count"),
}


def load_optimizer_ready_prices(prices_path=DEFAULT_PRICE_PATH):
    """Load the integrated optimizer-ready price data."""
    path = Path(prices_path)

    if not path.exists():
        raise FileNotFoundError(f"Optimizer-ready price file not found: {path}")

    return pd.read_csv(path)


def _is_missing(value):
    if value is None:
        return True

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _number(value, field_name, *, required=True, positive=False):
    if _is_missing(value) or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"{field_name} is required.")
        return None

    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric and finite.")

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be numeric and finite."
        ) from exc

    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be numeric and finite.")

    if positive and number <= 0:
        raise ValueError(f"{field_name} must be greater than zero.")

    if not positive and number < 0:
        raise ValueError(f"{field_name} cannot be negative.")

    return number


def _unit_definition(unit, field_name):
    if _is_missing(unit) or not str(unit).strip():
        raise ValueError(f"{field_name} is required.")

    normalized = str(unit).lower().strip()
    normalized = normalized.replace("$", "").replace("/", "")
    normalized = normalized.replace(" ", "").replace(".", "")

    if normalized not in _UNIT_DEFINITIONS:
        raise ValueError(f"Unsupported {field_name}: {unit}.")

    return _UNIT_DEFINITIONS[normalized]


def _variable_weight(value):
    if _is_missing(value):
        return False

    normalized = str(value).strip().lower()

    if normalized in {"true", "yes", "1"}:
        return True

    if normalized in {"false", "no", "0", ""}:
        return False

    raise ValueError("is_variable_weight must be a boolean value.")


def _python_value(value):
    if _is_missing(value):
        return None

    if hasattr(value, "item"):
        try:
            return value.item()
        except (AttributeError, ValueError):
            pass

    return value


def _product_details(product):
    if isinstance(product, pd.Series):
        product = product.to_dict()
    else:
        product = dict(product)

    return {
        key: _python_value(value)
        for key, value in product.items()
    }


def _multi_buy_quantity(value):
    quantity = _number(
        value,
        "multi_buy_quantity",
        required=False,
        positive=True,
    )

    if quantity is None:
        return None

    if not quantity.is_integer():
        raise ValueError("multi_buy_quantity must be a whole number.")

    return int(quantity)


def _effective_fixed_package_price(product, package_count):
    multi_buy_quantity = _multi_buy_quantity(
        product.get("multi_buy_quantity")
    )
    multi_buy_unit_price = _number(
        product.get("multi_buy_unit_price"),
        "multi_buy_unit_price",
        required=False,
    )
    single_item_price = _number(
        product.get("single_item_price"),
        "single_item_price",
        required=False,
    )
    displayed_price = _number(
        product.get("displayed_price"),
        "displayed_price",
        required=False,
    )
    sale_status = str(product.get("sale_status") or "").lower()
    has_multi_buy_data = (
        multi_buy_quantity is not None
        or multi_buy_unit_price is not None
        or single_item_price is not None
        or "multi" in sale_status
    )

    if (
        multi_buy_quantity is not None
        and multi_buy_unit_price is not None
        and package_count >= multi_buy_quantity
    ):
        return multi_buy_unit_price, True

    if has_multi_buy_data:
        if single_item_price is None:
            raise ValueError(
                "A single-item price is required when multi-buy "
                "qualification is not known or not met."
            )

        return single_item_price, False

    if displayed_price is None:
        raise ValueError(
            "Fixed-package products require a displayed package price."
        )

    return displayed_price, False


def estimate_product_cost(product, requested_quantity, requested_unit):
    """Estimate the purchase cost of one product for a requested quantity."""
    product = _product_details(product)
    requested_quantity = _number(
        requested_quantity,
        "requested_quantity",
        positive=True,
    )
    requested_dimension, requested_factor, canonical_requested_unit = (
        _unit_definition(requested_unit, "requested_unit")
    )
    requested_base_quantity = requested_quantity * requested_factor

    if _variable_weight(product.get("is_variable_weight")):
        listed_unit_price = _number(
            product.get("listed_unit_price"),
            "listed_unit_price",
        )
        listed_dimension, listed_factor, _ = _unit_definition(
            product.get("listed_unit"),
            "listed_unit",
        )

        if requested_dimension != listed_dimension:
            raise ValueError(
                "Requested unit is incompatible with the product's "
                "listed unit."
            )

        estimated_item_cost = (
            requested_base_quantity / listed_factor * listed_unit_price
        )
        result = {
            **product,
            "requested_quantity": requested_quantity,
            "requested_unit": canonical_requested_unit,
            "package_count": None,
            "fulfilled_quantity": requested_quantity,
            "fulfilled_unit": canonical_requested_unit,
            "excess_quantity": 0.0,
            "excess_unit": canonical_requested_unit,
            "effective_package_price": None,
            "estimated_item_cost": estimated_item_cost,
            "multi_buy_applied": False,
            "costing_method": "variable_weight",
        }
        return result

    package_size = _number(
        product.get("package_size"),
        "package_size",
        positive=True,
    )
    package_dimension, package_factor, _ = _unit_definition(
        product.get("package_unit"),
        "package_unit",
    )

    if requested_dimension != package_dimension:
        raise ValueError(
            "Requested unit is incompatible with the product's package unit."
        )

    package_base_quantity = package_size * package_factor
    package_ratio = requested_base_quantity / package_base_quantity
    package_count = max(1, math.ceil(package_ratio - 1e-12))
    fulfilled_base_quantity = package_count * package_base_quantity
    fulfilled_quantity = fulfilled_base_quantity / requested_factor
    excess_quantity = fulfilled_quantity - requested_quantity
    effective_package_price, multi_buy_applied = (
        _effective_fixed_package_price(product, package_count)
    )

    return {
        **product,
        "requested_quantity": requested_quantity,
        "requested_unit": canonical_requested_unit,
        "package_count": package_count,
        "fulfilled_quantity": fulfilled_quantity,
        "fulfilled_unit": canonical_requested_unit,
        "excess_quantity": excess_quantity,
        "excess_unit": canonical_requested_unit,
        "effective_package_price": effective_package_price,
        "estimated_item_cost": package_count * effective_package_price,
        "multi_buy_applied": multi_buy_applied,
        "costing_method": "fixed_package",
    }


def _eligible_optimizer_row(row):
    if str(row.get("comparison_group") or "").strip() == "unsupported":
        return False

    normalization_error = row.get("normalization_error")

    if not _is_missing(normalization_error) and str(normalization_error).strip():
        return False

    try:
        _number(row.get("price_per_standard_unit"), "price_per_standard_unit")
    except ValueError:
        return False

    return True


def cost_candidate_products(
    store,
    comparison_group,
    requested_quantity,
    requested_unit,
    prices=None,
    prices_path=DEFAULT_PRICE_PATH,
):
    """Cost every compatible product for one store and comparison group."""
    _number(requested_quantity, "requested_quantity", positive=True)
    _unit_definition(requested_unit, "requested_unit")
    source_prices = (
        load_optimizer_ready_prices(prices_path)
        if prices is None
        else prices
    )
    required_columns = {
        "store",
        "comparison_group",
        "price_per_standard_unit",
    }
    missing_columns = required_columns - set(source_prices.columns)

    if missing_columns:
        raise ValueError(
            "Price data is missing columns: "
            f"{sorted(missing_columns)}"
        )

    candidates = source_prices.loc[
        source_prices["store"].eq(store)
        & source_prices["comparison_group"].eq(comparison_group)
    ]
    costed_candidates = []

    for _, candidate in candidates.iterrows():
        if not _eligible_optimizer_row(candidate):
            continue

        try:
            costed_candidates.append(
                estimate_product_cost(
                    candidate,
                    requested_quantity=requested_quantity,
                    requested_unit=requested_unit,
                )
            )
        except ValueError:
            continue

    return sorted(
        costed_candidates,
        key=lambda product: (
            product["estimated_item_cost"],
            str(product.get("product_title") or ""),
            str(product.get("product_url") or ""),
        ),
    )


def select_lowest_cost_product(
    store,
    comparison_group,
    requested_quantity,
    requested_unit,
    prices=None,
    prices_path=DEFAULT_PRICE_PATH,
):
    """Select the compatible product with the lowest estimated item cost."""
    costed_candidates = cost_candidate_products(
        store=store,
        comparison_group=comparison_group,
        requested_quantity=requested_quantity,
        requested_unit=requested_unit,
        prices=prices,
        prices_path=prices_path,
    )

    if not costed_candidates:
        raise ValueError(
            f"No compatible products are available for {comparison_group} "
            f"at {store} for {requested_quantity} {requested_unit}."
        )

    return costed_candidates[0]
