"""Optimize comparison-group selections using normalized real-price data."""

from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_REAL_PRICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "optimizer_ready_prices.csv"
)
SHOPPING_PLAN_COLUMNS = (
    "comparison_group",
    "store",
    "product_title",
    "product_url",
    "package_size_text",
    "displayed_price",
    "standardized_unit",
    "price_per_standard_unit",
    "sale_status",
    "attributes",
)
REQUIRED_PRICE_COLUMNS = set(SHOPPING_PLAN_COLUMNS) | {
    "normalization_error",
}


def load_real_prices(prices_path=DEFAULT_REAL_PRICE_PATH):
    """Load the optimizer-ready normalized price dataset."""
    path = Path(prices_path)

    if not path.exists():
        raise FileNotFoundError(f"Optimizer-ready price file not found: {path}")

    return pd.read_csv(path)


def _validate_requested_groups(requested_groups):
    if isinstance(requested_groups, str):
        raise ValueError("requested_groups must be a list of group names.")

    try:
        groups = tuple(str(group).strip() for group in requested_groups)
    except TypeError as exc:
        raise ValueError(
            "requested_groups must be a list of group names."
        ) from exc

    if not groups or any(not group for group in groups):
        raise ValueError(
            "At least one non-empty comparison group is required."
        )

    if len(groups) != len(set(groups)):
        raise ValueError("requested_groups cannot contain duplicates.")

    return groups


def _validate_settings(max_stores, savings_threshold):
    if max_stores not in {1, 2}:
        raise ValueError("max_stores must currently be either 1 or 2.")

    if (
        isinstance(savings_threshold, bool)
        or not isinstance(savings_threshold, (int, float))
        or not np.isfinite(savings_threshold)
        or savings_threshold < 0
    ):
        raise ValueError(
            "savings_threshold must be a finite non-negative number."
        )


def prepare_real_price_data(prices):
    """Validate and retain only rows eligible for normalized comparison."""
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


def _shopping_plan_item(row):
    return {
        column: _python_value(row[column])
        for column in SHOPPING_PLAN_COLUMNS
    }


def _select_product(candidates):
    ordered = candidates.sort_values(
        [
            "price_per_standard_unit",
            "store",
            "product_title",
            "product_url",
        ],
        kind="mergesort",
        na_position="last",
    )
    return ordered.iloc[0]


def _evaluate_store_option(prices, stores, requested_groups):
    option_prices = prices.loc[prices["store"].isin(stores)]
    shopping_plan = []
    missing_groups = []

    for comparison_group in requested_groups:
        candidates = option_prices.loc[
            option_prices["comparison_group"].eq(comparison_group)
        ]

        if candidates.empty:
            missing_groups.append(comparison_group)
            continue

        shopping_plan.append(_shopping_plan_item(_select_product(candidates)))

    complete = not missing_groups
    used_stores = tuple(sorted({item["store"] for item in shopping_plan}))
    total_normalized_price = (
        float(
            sum(
                item["price_per_standard_unit"]
                for item in shopping_plan
            )
        )
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
        "total_normalized_price": total_normalized_price,
        "shopping_plan": shopping_plan,
        "uses_all_selected_stores": used_stores == tuple(stores),
    }


def _best_option(options):
    if not options:
        return None

    return min(
        options,
        key=lambda option: (
            option["total_normalized_price"],
            option["stores"],
        ),
    )


def optimize_real_price_basket(
    requested_groups,
    prices=None,
    prices_path=DEFAULT_REAL_PRICE_PATH,
    max_stores=2,
    savings_threshold=0.0,
):
    """Compare one- and two-store plans for normalized product groups."""
    groups = _validate_requested_groups(requested_groups)
    _validate_settings(max_stores, savings_threshold)
    source_prices = (
        load_real_prices(prices_path)
        if prices is None
        else prices
    )
    prepared = prepare_real_price_data(source_prices)
    stores = tuple(sorted(prepared["store"].unique().tolist()))

    store_coverage = {
        store: _evaluate_store_option(prepared, (store,), groups)
        for store in stores
    }
    complete_single_options = [
        option
        for option in store_coverage.values()
        if option["complete"]
    ]
    best_single = _best_option(complete_single_options)

    if max_stores == 1:
        if best_single is None:
            raise ValueError(
                "No single store covers every requested comparison group."
            )

        return {
            "requested_groups": groups,
            "store_coverage": store_coverage,
            "best_single": best_single,
            "best_pair": None,
            "savings": 0.0,
            "worth_it": False,
            "recommended_option": best_single,
            "recommendation": (
                f"Use {best_single['stores'][0]} for all requested groups."
            ),
        }

    pair_options = [
        _evaluate_store_option(prepared, store_pair, groups)
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
            "No one-store or two-store combination covers every requested "
            "comparison group."
        )

    if best_pair is None:
        return {
            "requested_groups": groups,
            "store_coverage": store_coverage,
            "best_single": best_single,
            "best_pair": None,
            "savings": 0.0,
            "worth_it": False,
            "recommended_option": best_single,
            "recommendation": (
                f"Use {best_single['stores'][0]}; no genuine two-store "
                "option improves group coverage or price."
            ),
        }

    if best_single is None:
        return {
            "requested_groups": groups,
            "store_coverage": store_coverage,
            "best_single": None,
            "best_pair": best_pair,
            "savings": None,
            "worth_it": True,
            "recommended_option": best_pair,
            "recommendation": (
                "Use two stores because no single store covers every "
                "requested comparison group."
            ),
        }

    savings = (
        best_single["total_normalized_price"]
        - best_pair["total_normalized_price"]
    )
    worth_it = savings > 0 and savings >= savings_threshold
    recommended_option = best_pair if worth_it else best_single
    recommendation = (
        "Use two stores because the normalized savings meet or exceed "
        "the threshold."
        if worth_it
        else "Use one store because the normalized savings do not meet "
        "the threshold."
    )

    return {
        "requested_groups": groups,
        "store_coverage": store_coverage,
        "best_single": best_single,
        "best_pair": best_pair,
        "savings": float(savings),
        "worth_it": worth_it,
        "recommended_option": recommended_option,
        "recommendation": recommendation,
    }
