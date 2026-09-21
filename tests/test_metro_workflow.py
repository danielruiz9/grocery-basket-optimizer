"""Offline four-store baseline checks, excluding subsequently added stores."""

from pathlib import Path

import pandas as pd
import pytest

from src.real_price_optimizer import optimize_real_price_basket


@pytest.fixture
def prices():
    frame = pd.read_csv(
        Path(__file__).resolve().parents[1] / "data" / "optimizer_ready_prices.csv"
    )
    return frame.loc[frame["store"].isin({
        "Walmart Canada", "No Frills Canada", "Food Basics Canada", "Metro Canada",
    })]


def request(group, quantity, unit):
    return {"comparison_group": group, "quantity": quantity, "unit": unit}


def standard_basket():
    return [
        request("chicken_breast_boneless", 1.5, "kg"),
        request("large_eggs", 18, "count"),
        request("fresh_apples", 1, "kg"),
        request("dry_pasta", 900, "g"),
        request("sliced_bread", 1, "count"),
    ]


def test_four_store_basket_uses_checkout_costs_and_threshold(prices):
    basket = standard_basket()
    result = optimize_real_price_basket(basket, prices=prices, savings_threshold=5)
    assert len(result["store_coverage"]) == 4
    assert result["best_single"]["stores"] == ("Metro Canada",)
    assert result["best_single"]["total_cost"] == pytest.approx(31.65)
    assert result["best_pair"]["stores"] == ("Metro Canada", "Walmart Canada")
    assert result["best_pair"]["total_cost"] == pytest.approx(29.46)
    assert result["savings"] == pytest.approx(2.19)
    assert result["recommended_option"] == result["best_single"]
    for threshold, worth_it in ((2.00, True), (2.19, True), (2.20, False)):
        option = optimize_real_price_basket(
            basket, prices=prices, savings_threshold=threshold
        )
        assert option["worth_it"] is worth_it
    plan = result["best_single"]["shopping_plan"]
    assert [item["package_count"] for item in plan] == [None, 1, None, 1, 1]
    assert sum(item["estimated_item_cost"] for item in plan) == pytest.approx(31.65)
    assert all(item["product_url"].startswith("https://www.metro.ca/") for item in plan)


def test_one_store_and_single_item_fallback(prices):
    result = optimize_real_price_basket(standard_basket(), prices=prices, max_stores=1)
    assert result["best_pair"] is None
    assert result["recommended_option"]["stores"] == ("Metro Canada",)
    artisan = optimize_real_price_basket(
        [request("artisan_bread", 1, "count")], prices=prices
    )
    assert artisan["best_pair"] is None
    assert artisan["best_single"]["shopping_plan"][0]["product_title"] == "Asiago Cheese Bread Loaf"
    assert not artisan["store_coverage"]["Walmart Canada"]["complete"]


def test_metro_pair_only_coverage_and_one_store_error(prices):
    basket = [
        request("chicken_breast_bone_in", 1, "kg"),
        request("cooking_bananas", 1, "kg"),
    ]
    result = optimize_real_price_basket(basket, prices=prices, savings_threshold=100)
    assert result["best_single"] is None
    assert result["savings"] is None
    assert result["recommended_option"]["stores"] == ("Metro Canada", "No Frills Canada")
    assert result["recommended_option"]["total_cost"] == pytest.approx(18.25)
    with pytest.raises(ValueError, match="No single store"):
        optimize_real_price_basket(basket, prices=prices, max_stores=1)
    # Three store-exclusive items cannot be fulfilled with two stops.
    with pytest.raises(ValueError, match="No one-store or two-store"):
        optimize_real_price_basket(
            basket + [request("swiss_cheese", 200, "g")], prices=prices
        )


def test_metro_results_are_deterministic_when_rows_are_shuffled(prices):
    basket = standard_basket()
    first = optimize_real_price_basket(basket, prices=prices)
    shuffled = optimize_real_price_basket(
        basket, prices=prices.sample(frac=1, random_state=7)
    )
    for key in ("best_single", "best_pair", "recommended_option"):
        assert first[key] == shuffled[key]
