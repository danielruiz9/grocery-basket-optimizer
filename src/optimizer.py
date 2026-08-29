from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


def prepare_basket_data(
    prices: pd.DataFrame,
    basket: pd.DataFrame
) -> pd.DataFrame:
    """
    Validate the input tables and merge basket quantities with store prices.
    """

    required_price_columns = {"item", "store", "price"}
    required_basket_columns = {"item", "quantity"}

    missing_price_columns = required_price_columns - set(prices.columns)
    missing_basket_columns = required_basket_columns - set(basket.columns)

    if missing_price_columns:
        raise ValueError(
            f"Prices data is missing columns: {sorted(missing_price_columns)}"
        )

    if missing_basket_columns:
        raise ValueError(
            f"Basket data is missing columns: {sorted(missing_basket_columns)}"
        )

    if basket.empty:
        raise ValueError("The grocery basket cannot be empty.")

    if (basket["quantity"] <= 0).any():
        raise ValueError("Every basket quantity must be greater than zero.")

    price_values = prices["price"]

    if price_values.isna().any():
        raise ValueError("Prices data cannot contain null price values.")

    if (
        not pd.api.types.is_numeric_dtype(price_values)
        or pd.api.types.is_bool_dtype(price_values)
        or pd.api.types.is_complex_dtype(price_values)
    ):
        raise ValueError("Prices data column 'price' must be numeric.")

    if not np.isfinite(price_values.to_numpy()).all():
        raise ValueError("Prices data must contain only finite price values.")

    if (price_values < 0).any():
        raise ValueError("Prices data cannot contain negative price values.")

    prices_clean = prices.copy()
    basket_clean = basket.copy()

    # Standardize item names for more reliable matching
    prices_clean["item"] = prices_clean["item"].str.strip().str.lower()
    basket_clean["item"] = basket_clean["item"].str.strip().str.lower()

    # Combine duplicate basket items
    basket_clean = (
        basket_clean
        .groupby("item", as_index=False)["quantity"]
        .sum()
    )

    unavailable_items = sorted(
        set(basket_clean["item"]) - set(prices_clean["item"])
    )

    if unavailable_items:
        raise ValueError(
            f"No price data was found for: {unavailable_items}"
        )

    basket_prices = basket_clean.merge(
        prices_clean,
        on="item",
        how="left"
    )

    basket_prices["total_item_cost"] = (
        basket_prices["quantity"] * basket_prices["price"]
    )

    return basket_prices

def calculate_store_combo(
    basket_prices: pd.DataFrame,
    selected_stores: tuple[str, ...]
) -> dict:
    """
    Assign each basket item to its cheapest available store
    within the selected combination.
    """

    combo_data = basket_prices[
        basket_prices["store"].isin(selected_stores)
    ].copy()

    shopping_plan = (
        combo_data
        .sort_values(["item", "total_item_cost", "store"])
        .groupby("item", as_index=False)
        .first()
    )

    required_items = set(basket_prices["item"].unique())
    covered_items = set(shopping_plan["item"])

    if covered_items != required_items:
        return {
            "stores": selected_stores,
            "total_cost": float("inf"),
            "shopping_plan": None
        }

    return {
        "stores": selected_stores,
        "total_cost": shopping_plan["total_item_cost"].sum(),
        "shopping_plan": shopping_plan
    }

def optimize_basket(
    prices: pd.DataFrame,
    basket: pd.DataFrame,
    max_stores: int = 2,
    savings_threshold: float = 5.00
) -> dict:
    """
    Optimize a grocery basket for one or two stores.
    """

    if max_stores not in {1, 2}:
        raise ValueError("max_stores must currently be either 1 or 2.")

    if savings_threshold < 0:
        raise ValueError("savings_threshold cannot be negative.")

    basket_prices = prepare_basket_data(prices, basket)
    stores = sorted(basket_prices["store"].dropna().unique())

    # Test every single-store option
    single_store_results = [
        calculate_store_combo(basket_prices, (store,))
        for store in stores
    ]

    valid_single_results = [
        result
        for result in single_store_results
        if result["total_cost"] != float("inf")
    ]

    valid_single_results = sorted(
        valid_single_results,
        key=lambda result: result["total_cost"]
    )

    best_single = (
        valid_single_results[0]
        if valid_single_results
        else None
    )

    second_best_single = (
        valid_single_results[1]
        if len(valid_single_results) > 1
        else None
    )

    # Users allowing only one store stop here
    if max_stores == 1:
        if best_single is None:
            raise ValueError(
                "No single store contains every item in the basket."
            )

        return {
            "best_single": best_single,
            "second_best_single": second_best_single,
            "best_multi": None,
            "savings": 0.0,
            "worth_it": False,
            "recommended_option": best_single,
            "recommendation": (
                f"Shop at {best_single['stores'][0]}."
            )
        }

    # Test every two-store combination
    two_store_results = [
        calculate_store_combo(basket_prices, store_pair)
        for store_pair in combinations(stores, 2)
    ]

    valid_two_store_results = [
        result
        for result in two_store_results
        if result["total_cost"] != float("inf")
    ]

    if best_single is None and not valid_two_store_results:
        raise ValueError(
            "No one-store or two-store combination covers the full basket."
        )

    if not valid_two_store_results:
        return {
            "best_single": best_single,
            "second_best_single": second_best_single,
            "best_multi": None,
            "savings": 0.0,
            "worth_it": False,
            "recommended_option": best_single,
            "recommendation": (
                f"Shop at {best_single['stores'][0]}."
            )
        }

    best_two = min(
        valid_two_store_results,
        key=lambda result: result["total_cost"]
    )

    if best_single is None:
        return {
            "best_single": None,
            "second_best_single": None,
            "best_multi": best_two,
            "savings": None,
            "worth_it": True,
            "recommended_option": best_two,
            "recommendation": (
                "Visit two stores because no single store covers the full basket."
            )
        }

    savings = best_single["total_cost"] - best_two["total_cost"]
    worth_it = savings > 0 and savings >= savings_threshold

    if worth_it:
        recommended_option = best_two
        recommendation = (
            "Visit two stores because the savings meet or exceed "
            "your minimum threshold."
        )
    else:
        recommended_option = best_single
        recommendation = (
            "Stick with one store because the extra savings do not "
            "meet your minimum threshold."
        )

    return {
        "best_single": best_single,
        "second_best_single": second_best_single,
        "best_multi": best_two,
        "savings": savings,
        "worth_it": worth_it,
        "recommended_option": recommended_option,
        "recommendation": recommendation
    }
