"""Offline regression checks for the cleaned six-store snapshot."""

from pathlib import Path

import pandas as pd
import pytest

from src.integration import integrate_price_frames, load_normalized_datasets
from src.real_price_optimizer import optimize_real_price_basket


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def prices():
    return pd.read_csv(ROOT / "data" / "optimizer_ready_prices.csv")


def item(group, quantity, unit):
    return {"comparison_group": group, "quantity": quantity, "unit": unit}


def standard_basket():
    return [
        item("chicken_breast_boneless", 1.5, "kg"),
        item("large_eggs", 18, "count"),
        item("fresh_apples", 1, "kg"),
        item("dry_pasta", 900, "g"),
        item("sliced_bread", 1, "count"),
    ]


def test_six_store_snapshot_preserves_audit_and_filters_availability(prices):
    inputs = load_normalized_datasets()
    result = integrate_price_frames(inputs)

    assert len(inputs) == 6
    assert len(inputs[-1]) == 84
    assert result.duplicates_removed == 3
    assert len(result.combined) == 480
    assert len(result.optimizer_ready) == len(prices) == 363
    assert result.optimizer_ready.groupby("store").size().to_dict() == {
        "Food Basics Canada": 66,
        "Loblaws Canada": 54,
        "Metro Canada": 71,
        "No Frills Canada": 59,
        "Sobeys Canada": 59,
        "Walmart Canada": 54,
    }

    audit_sobeys = result.combined.loc[
        result.combined.store.eq("Sobeys Canada")
    ]
    ready_sobeys = result.optimizer_ready.loc[
        result.optimizer_ready.store.eq("Sobeys Canada")
    ]
    assert len(audit_sobeys) == 84
    assert audit_sobeys.availability.value_counts().to_dict() == {
        "in_stock": 79,
        "out_of_stock": 5,
    }
    assert len(ready_sobeys) == 59
    assert set(ready_sobeys.availability) == {"in_stock"}
    assert audit_sobeys.product_id.notna().all()
    assert audit_sobeys.store_context.nunique() == 1
    assert "Sobeys Urban Fresh Spadina" in audit_sobeys.store_context.iloc[0]

    eligible_before_availability = audit_sobeys.loc[
        audit_sobeys.comparison_group.ne("unsupported")
        & audit_sobeys.normalization_error.fillna("").eq("")
        & audit_sobeys.price_per_standard_unit.notna()
    ]
    assert len(eligible_before_availability) == 61
    assert eligible_before_availability.availability.eq("out_of_stock").sum() == 2
    assert ready_sobeys.data_quality_warning.notna().sum() == 2
    assert result.optimizer_ready.groupby(
        "comparison_group"
    ).standardized_unit.nunique().eq(1).all()


def test_sobeys_apple_fallback_and_warning_survive_integration():
    audit = pd.read_csv(ROOT / "data" / "all_stores_prices_normalized.csv")
    paula = audit.loc[
        audit.product_title.eq("Paula Apples Red 3 lb")
        & audit.store.eq("Sobeys Canada")
    ].iloc[0]
    assert paula.package_size_text == "3 lb"
    assert paula.package_size == 3
    assert paula.package_unit == "lb"
    assert "title weight 3 lb was used" in paula.data_quality_warning

    honeycrisp = audit.loc[
        audit.product_title.eq(
            "Lil Snappers Organic Apples Honeycrisp 1.36 kg"
        )
        & audit.store.eq("Sobeys Canada")
    ].iloc[0]
    assert honeycrisp.package_size == 907
    assert honeycrisp.package_unit == "g"
    assert "captured structured data was retained" in (
        honeycrisp.data_quality_warning
    )


def test_standard_basket_is_unchanged_after_sobeys(prices):
    before = optimize_real_price_basket(
        standard_basket(),
        prices=prices.loc[prices.store.ne("Sobeys Canada")],
        savings_threshold=5,
    )
    after = optimize_real_price_basket(
        standard_basket(), prices=prices, savings_threshold=5
    )

    assert before["best_single"]["stores"] == after["best_single"]["stores"] == (
        "Metro Canada",
    )
    assert before["best_single"]["total_cost"] == pytest.approx(31.65)
    assert after["best_single"]["total_cost"] == pytest.approx(31.65)
    assert before["best_pair"]["stores"] == after["best_pair"]["stores"] == (
        "Loblaws Canada", "Walmart Canada",
    )
    assert before["best_pair"]["total_cost"] == pytest.approx(27.55)
    assert after["best_pair"]["total_cost"] == pytest.approx(27.55)
    assert after["store_coverage"]["Sobeys Canada"]["total_cost"] == pytest.approx(
        52.085
    )
    assert after["recommended_option"] == after["best_single"]


def test_sobeys_single_pair_threshold_and_one_store_mode(prices):
    cheese = [item("cheese_unspecified_type", 100, "g")]
    one_store = optimize_real_price_basket(
        cheese, prices=prices, max_stores=1
    )
    assert one_store["best_pair"] is None
    assert one_store["recommended_option"]["stores"] == ("Sobeys Canada",)
    assert one_store["recommended_option"]["total_cost"] == pytest.approx(3.29)

    basket = cheese + [item("large_eggs", 18, "count")]
    low_threshold = optimize_real_price_basket(
        basket, prices=prices, savings_threshold=1.71
    )
    assert low_threshold["best_single"]["stores"] == ("Loblaws Canada",)
    assert low_threshold["best_single"]["total_cost"] == pytest.approx(9.44)
    assert low_threshold["best_pair"]["stores"] == (
        "Loblaws Canada", "Sobeys Canada",
    )
    assert low_threshold["best_pair"]["total_cost"] == pytest.approx(7.73)
    assert low_threshold["savings"] == pytest.approx(1.71)
    assert low_threshold["worth_it"] is True
    assert low_threshold["recommended_option"] == low_threshold["best_pair"]

    high_threshold = optimize_real_price_basket(
        basket, prices=prices, savings_threshold=2
    )
    assert high_threshold["worth_it"] is False
    assert high_threshold["recommended_option"] == high_threshold["best_single"]


def test_sobeys_pair_only_sparse_coverage(prices):
    basket = [
        item("cheese_unspecified_type", 100, "g"),
        item("processed_cheese", 400, "g"),
        item("shell_eggs_unspecified_size", 12, "count"),
    ]
    result = optimize_real_price_basket(
        basket, prices=prices, savings_threshold=100
    )
    assert result["best_single"] is None
    assert result["savings"] is None
    assert result["recommended_option"]["stores"] == (
        "Loblaws Canada", "Sobeys Canada",
    )
    assert result["recommended_option"]["total_cost"] == pytest.approx(20.07)
    with pytest.raises(ValueError, match="No single store"):
        optimize_real_price_basket(basket, prices=prices, max_stores=1)


def test_six_store_results_and_product_links_are_deterministic(prices):
    basket = [
        item("cheese_unspecified_type", 100, "g"),
        item("large_eggs", 18, "count"),
    ]
    first = optimize_real_price_basket(
        basket, prices=prices, savings_threshold=1.71
    )
    sobeys_items = [
        row for row in first["recommended_option"]["shopping_plan"]
        if row["store"] == "Sobeys Canada"
    ]
    assert sobeys_items
    assert all(
        row["product_url"].startswith("https://www.sobeys.com/products/")
        for row in sobeys_items
    )
    for seed in (1, 7, 42):
        shuffled = optimize_real_price_basket(
            basket,
            prices=prices.sample(frac=1, random_state=seed),
            savings_threshold=1.71,
        )
        for key in ("best_single", "best_pair", "recommended_option"):
            assert shuffled[key] == first[key]


def test_streamlit_sobeys_label_snapshot_date_and_one_store_flow():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "app.py")).run()
    assert not app.exception
    assert any(
        "Prices last updated: 2026-09-20" in caption.value
        for caption in app.caption
    )
    assert any("Sobeys" in paragraph.value for paragraph in app.markdown)

    app.multiselect[0].set_value(["cheese_unspecified_type"]).run()
    app.number_input[0].set_value(100).run()
    app.radio[0].set_value(1).run()
    app.button[0].click().run()

    assert not app.exception
    assert any(
        "Shop at Sobeys" in success.value and "3.29" in success.value
        for success in app.success
    )
    assert all("Sobeys Canada" not in success.value for success in app.success)

