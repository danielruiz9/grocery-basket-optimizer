"""Optimize quantity-aware grocery baskets using normalized real-price data."""

from collections.abc import Mapping
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from src.basket_costing import cost_candidate_products


DEFAULT_REAL_PRICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "optimizer_ready_prices.csv"
)
REQUIRED_PRICE_COLUMNS = {
    "comparison_group",
    "store",
    "standardized_unit",
    "price_per_standard_unit",
    "normalization_error",
}


def load_real_prices(prices_path=DEFAULT_REAL_PRICE_PATH):
    """Load the optimizer-ready normalized price dataset."""
    path = Path(prices_path)

    if not path.exists():
        raise FileNotFoundError(f"Optimizer-ready price file not found: {path}")

    return pd.read_csv(path)


def _validate_basket(basket):
    if isinstance(basket, (str, bytes, Mapping)):
        raise ValueError("basket must be a list of requested item mappings.")

    try:
        requested_items = list(basket)
    except TypeError as exc:
        raise ValueError(
            "basket must be a list of requested item mappings."
        ) from exc

    if not requested_items:
        raise ValueError("The grocery basket cannot be empty.")

    validated = []
    seen_groups = set()

    for index, item in enumerate(requested_items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(
                f"Basket item {index} must be a mapping with "
                "comparison_group, quantity, and unit."
            )

        comparison_group = str(item.get("comparison_group") or "").strip()
        unit = str(item.get("unit") or "").strip()
        quantity = item.get("quantity")

        if not comparison_group:
            raise ValueError(
                f"Basket item {index} requires a comparison_group."
            )

        if comparison_group in seen_groups:
            raise ValueError(
                "The basket cannot contain duplicate comparison groups."
            )

        if not unit:
            raise ValueError(f"Basket item {index} requires a unit.")

        if isinstance(quantity, bool):
            raise ValueError(
                f"Basket item {index} quantity must be numeric and finite."
            )

        try:
            quantity = float(quantity)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Basket item {index} quantity must be numeric and finite."
            ) from exc

        if not np.isfinite(quantity):
            raise ValueError(
                f"Basket item {index} quantity must be numeric and finite."
            )

        if quantity <= 0:
            raise ValueError(
                f"Basket item {index} quantity must be greater than zero."
            )

        seen_groups.add(comparison_group)
        validated.append(
            {
                "comparison_group": comparison_group,
                "quantity": quantity,
                "unit": unit,
            }
        )

    return tuple(validated)


def _validate_settings(max_stores, savings_threshold):
    if isinstance(max_stores, bool) or max_stores not in {1, 2}:
        raise ValueError("max_stores must currently be either 1 or 2.")

    if isinstance(savings_threshold, bool):
        raise ValueError(
            "savings_threshold must be a finite non-negative number."
        )

    try:
        savings_threshold = float(savings_threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "savings_threshold must be a finite non-negative number."
        ) from exc

    if not np.isfinite(savings_threshold) or savings_threshold < 0:
        raise ValueError(
            "savings_threshold must be a finite non-negative number."
        )

    return savings_threshold


def prepare_real_price_data(prices):
    """Validate and retain rows eligible for quantity-aware costing."""
    missing_columns = REQUIRED_PRICE_COLUMNS - set(prices.columns)

    if missing_columns:
        raise ValueError(
            "Real-price data is missing columns: "
            f"{sorted(missing_columns)}"
        )

    prepared = prices.copy()
    normalized_prices = pd.to_numeric(
        prepared["price_per_standard_unit"],
        errors="coerce",
    )
    empty_error = (
        prepared["normalization_error"]
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("")
    )
    valid_group = (
        prepared["comparison_group"].notna()
        & prepared["comparison_group"].astype(str).str.strip().ne("")
        & prepared["comparison_group"].ne("unsupported")
    )
    valid_store = (
        prepared["store"].notna()
        & prepared["store"].astype(str).str.strip().ne("")
    )
    valid_unit = (
        prepared["standardized_unit"].notna()
        & prepared["standardized_unit"].astype(str).str.strip().ne("")
    )
    valid_price = (
        normalized_prices.notna()
        & np.isfinite(normalized_prices)
        & normalized_prices.ge(0)
    )

    prepared["price_per_standard_unit"] = normalized_prices
    return prepared.loc[
        empty_error & valid_group & valid_store & valid_unit & valid_price
    ].copy()


def _python_value(value):
    if pd.api.types.is_scalar(value) and pd.isna(value):
        return None

    if isinstance(value, np.generic):
        return value.item()

    return value


def _shopping_plan_item(product):
    effective_package_price = product.get("effective_package_price")
    listed_unit_price = product.get("listed_unit_price")
    is_variable_weight = product.get("costing_method") == "variable_weight"
    effective_price = (
        listed_unit_price if is_variable_weight else effective_package_price
    )
    effective_price_unit = (
        product.get("listed_unit") if is_variable_weight else "package"
    )
    columns = (
        "comparison_group",
        "requested_quantity",
        "requested_unit",
        "store",
        "product_title",
        "product_url",
        "package_size_text",
        "package_size",
        "package_unit",
        "package_count",
        "fulfilled_quantity",
        "fulfilled_unit",
        "excess_quantity",
        "excess_unit",
        "effective_package_price",
        "listed_unit_price",
        "listed_unit",
        "estimated_item_cost",
        "displayed_price",
        "sale_status",
        "multi_buy_quantity",
        "multi_buy_unit_price",
        "single_item_price",
        "multi_buy_applied",
        "is_variable_weight",
        "costing_method",
        "standardized_unit",
        "price_per_standard_unit",
        "attributes",
        "data_quality_warning",
    )
    shopping_plan_item = {
        column: _python_value(product.get(column))
        for column in columns
    }
    shopping_plan_item["effective_price"] = _python_value(effective_price)
    shopping_plan_item["effective_price_unit"] = _python_value(
        effective_price_unit
    )
    return shopping_plan_item


def _product_tie_key(product):
    return (
        product["estimated_item_cost"],
        str(product.get("store") or ""),
        str(product.get("product_title") or ""),
        str(product.get("product_url") or ""),
    )


def _build_store_item_options(prices, stores, basket):
    store_item_options = {}

    for store in stores:
        store_item_options[store] = {}

        for requested_item in basket:
            candidates = cost_candidate_products(
                store=store,
                comparison_group=requested_item["comparison_group"],
                requested_quantity=requested_item["quantity"],
                requested_unit=requested_item["unit"],
                prices=prices,
            )
            store_item_options[store][
                requested_item["comparison_group"]
            ] = candidates[0] if candidates else None

    return store_item_options


def _evaluate_store_option(stores, basket, store_item_options):
    shopping_plan = []
    missing_groups = []

    for requested_item in basket:
        comparison_group = requested_item["comparison_group"]
        available_products = [
            store_item_options[store][comparison_group]
            for store in stores
            if store_item_options[store][comparison_group] is not None
        ]

        if not available_products:
            missing_groups.append(comparison_group)
            continue

        selected_product = min(available_products, key=_product_tie_key)
        shopping_plan.append(_shopping_plan_item(selected_product))

    complete = not missing_groups
    used_stores = tuple(sorted({item["store"] for item in shopping_plan}))
    total_cost = (
        float(sum(item["estimated_item_cost"] for item in shopping_plan))
        if complete
        else None
    )

    return {
        "stores": tuple(stores),
        "complete": complete,
        "covered_groups": [
            item["comparison_group"] for item in shopping_plan
        ],
        "missing_groups": missing_groups,
        "total_cost": total_cost,
        "shopping_plan": shopping_plan,
        "uses_all_selected_stores": used_stores == tuple(stores),
    }


def _best_option(options):
    if not options:
        return None

    return min(
        options,
        key=lambda option: (
            option["total_cost"],
            option["stores"],
        ),
    )


def optimize_real_price_basket(
    basket,
    prices=None,
    prices_path=DEFAULT_REAL_PRICE_PATH,
    max_stores=2,
    savings_threshold=0.0,
):
    """Compare one- and two-store plans using estimated checkout dollars."""
    requested_basket = _validate_basket(basket)
    savings_threshold = _validate_settings(max_stores, savings_threshold)
    source_prices = (
        load_real_prices(prices_path)
        if prices is None
        else prices
    )
    prepared = prepare_real_price_data(source_prices)
    stores = tuple(sorted(prepared["store"].unique().tolist()))
    store_item_options = _build_store_item_options(
        prepared,
        stores,
        requested_basket,
    )
    store_coverage = {
        store: _evaluate_store_option(
            (store,),
            requested_basket,
            store_item_options,
        )
        for store in stores
    }
    complete_single_options = [
        option
        for option in store_coverage.values()
        if option["complete"]
    ]
    best_single = _best_option(complete_single_options)
    result_base = {
        "requested_basket": [dict(item) for item in requested_basket],
        "requested_groups": tuple(
            item["comparison_group"] for item in requested_basket
        ),
        "store_coverage": store_coverage,
        "savings_threshold": savings_threshold,
    }

    if max_stores == 1:
        if best_single is None:
            raise ValueError(
                "No single store can fulfill every requested basket item."
            )

        return {
            **result_base,
            "best_single": best_single,
            "best_pair": None,
            "savings": 0.0,
            "worth_it": False,
            "recommended_option": best_single,
            "recommendation": (
                f"Use {best_single['stores'][0]} for an estimated "
                f"${best_single['total_cost']:.2f}."
            ),
        }

    pair_options = [
        _evaluate_store_option(
            store_pair,
            requested_basket,
            store_item_options,
        )
        for store_pair in combinations(stores, 2)
    ]
    complete_pair_options = [
        option
        for option in pair_options
        if option["complete"] and option["uses_all_selected_stores"]
    ]
    best_pair = _best_option(complete_pair_options)

    if best_single is None and best_pair is None:
        raise ValueError(
            "No one-store or two-store combination can fulfill every "
            "requested basket item."
        )

    if best_pair is None:
        return {
            **result_base,
            "best_single": best_single,
            "best_pair": None,
            "savings": 0.0,
            "worth_it": False,
            "recommended_option": best_single,
            "recommendation": (
                f"Use {best_single['stores'][0]}; no genuine two-store "
                "plan can fulfill the basket more cheaply."
            ),
        }

    if best_single is None:
        return {
            **result_base,
            "best_single": None,
            "best_pair": best_pair,
            "savings": None,
            "worth_it": True,
            "recommended_option": best_pair,
            "recommendation": (
                "Use two stores because no single store can fulfill every "
                "requested basket item."
            ),
        }

    savings = best_single["total_cost"] - best_pair["total_cost"]
    worth_it = savings > 0 and savings >= savings_threshold
    recommended_option = best_pair if worth_it else best_single
    recommendation = (
        "Use two stores because the estimated dollar savings meet or "
        "exceed the threshold."
        if worth_it
        else "Use one store because the estimated dollar savings do not "
        "meet the threshold."
    )

    return {
        **result_base,
        "best_single": best_single,
        "best_pair": best_pair,
        "savings": float(savings),
        "worth_it": worth_it,
        "recommended_option": recommended_option,
        "recommendation": recommendation,
    }
