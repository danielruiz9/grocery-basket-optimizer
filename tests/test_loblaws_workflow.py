"""Offline regression checks for the cleaned five-store snapshot."""

from pathlib import Path

import pandas as pd
import pytest

from src.integration import integrate_price_frames, load_normalized_datasets
from src.real_price_optimizer import optimize_real_price_basket

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def prices():
    frame = pd.read_csv(ROOT / "data" / "optimizer_ready_prices.csv")
    return frame.loc[frame.store.ne("Sobeys Canada")]


def item(group, quantity, unit):
    return {"comparison_group": group, "quantity": quantity, "unit": unit}


def basket():
    return [
        item("chicken_breast_boneless", 1.5, "kg"),
        item("large_eggs", 18, "count"), item("fresh_apples", 1, "kg"),
        item("dry_pasta", 900, "g"), item("sliced_bread", 1, "count"),
    ]


def test_five_store_snapshot_deduplicates_without_losing_audit_rows(prices):
    inputs = load_normalized_datasets()
    assert len(inputs[-2]) == 84  # The raw Loblaws cross-search rows stay intact.
    result = integrate_price_frames(inputs[:-1])
    assert result.duplicates_removed == 3
    assert len(result.combined) == 396
    assert len(result.optimizer_ready) == len(prices) == 304
    assert result.optimizer_ready.groupby("store").size().to_dict() == {
        "Food Basics Canada": 66, "Loblaws Canada": 54, "Metro Canada": 71,
        "No Frills Canada": 59, "Walmart Canada": 54,
    }
    assert len(result.combined.query('store == "Loblaws Canada"')) == 82
    assert result.optimizer_ready.data_quality_warning.notna().sum() == 3
    assert result.optimizer_ready.groupby("comparison_group").standardized_unit.nunique().eq(1).all()
    assert not prices.comparison_group.eq("unsupported").any()
    assert prices.normalization_error.isna().all()


def test_standard_basket_before_after_and_dollar_thresholds(prices):
    before = optimize_real_price_basket(
        basket(), prices=prices.loc[prices.store.ne("Loblaws Canada")]
    )
    assert before["best_pair"]["total_cost"] == pytest.approx(29.46)
    for threshold, worth_it in ((4, True), (4.10, True), (4.11, False), (5, False)):
        result = optimize_real_price_basket(basket(), prices=prices, savings_threshold=threshold)
        assert len(result["store_coverage"]) == 5
        assert result["best_single"]["stores"] == ("Metro Canada",)
        assert result["best_single"]["total_cost"] == pytest.approx(31.65)
        assert result["store_coverage"]["Loblaws Canada"]["total_cost"] == pytest.approx(33.77)
        assert result["best_pair"]["stores"] == ("Loblaws Canada", "Walmart Canada")
        assert result["best_pair"]["total_cost"] == pytest.approx(27.55)
        assert result["savings"] == pytest.approx(4.10)
        assert result["worth_it"] is worth_it
        expected = result["best_pair"] if worth_it else result["best_single"]
        assert result["recommended_option"] == expected
    plan = result["best_pair"]["shopping_plan"]
    assert [r["package_count"] for r in plan] == [None, 1, None, 1, 1]
    assert [r["estimated_item_cost"] for r in plan] == pytest.approx([16.14, 4.44, 2.20, 2.29, 2.48])
    assert all(r["product_url"].startswith("https://www.loblaws.ca/") for r in plan if r["store"] == "Loblaws Canada")


def test_loblaws_single_store_recommendation_and_one_store_mode(prices):
    eggs = optimize_real_price_basket([item("large_eggs", 18, "count")], prices=prices, max_stores=1)
    assert eggs["recommended_option"]["stores"] == ("Loblaws Canada",)
    assert eggs["best_single"]["total_cost"] == pytest.approx(4.44)
    assert eggs["best_pair"] is None
    result = optimize_real_price_basket(basket(), prices=prices, max_stores=1, savings_threshold=0)
    assert result["best_pair"] is None
    assert result["recommended_option"]["stores"] == ("Metro Canada",)


def test_loblaws_pair_only_sparse_coverage_and_unfulfillable_basket(prices):
    sparse = [item("shell_eggs_unspecified_size", 18, "count"), item("cooking_bananas", 1, "kg")]
    result = optimize_real_price_basket(sparse, prices=prices, savings_threshold=100)
    assert result["best_single"] is None
    assert result["savings"] is None
    assert result["recommended_option"]["stores"] == ("Loblaws Canada", "No Frills Canada")
    assert result["recommended_option"]["total_cost"] == pytest.approx(13.23)
    with pytest.raises(ValueError, match="No single store"):
        optimize_real_price_basket(sparse, prices=prices, max_stores=1)
    with pytest.raises(ValueError, match="No one-store or two-store"):
        optimize_real_price_basket(sparse + [item("chicken_breast_bone_in", 1, "kg")], prices=prices)


def test_tied_pasta_choices_are_deterministic(prices):
    first = optimize_real_price_basket(basket(), prices=prices, savings_threshold=4)
    pasta = first["best_pair"]["shopping_plan"][3]
    assert pasta["store"] == "Loblaws Canada"
    assert pasta["product_title"] == "Cavatappi"
    for seed in (1, 7, 42):
        shuffled = optimize_real_price_basket(basket(), prices=prices.sample(frac=1, random_state=seed), savings_threshold=4)
        for key in ("best_single", "best_pair", "recommended_option"):
            assert shuffled[key] == first[key]


def test_streamlit_five_store_labels_snapshot_date_and_single_store_flow():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "app.py")).run()
    assert not app.exception
    assert any("Prices last updated: 2026-09-20" in c.value for c in app.caption)
    app.multiselect[0].set_value(["large_eggs"]).run()
    app.radio[0].set_value(1).run()
    app.button[0].click().run()
    assert not app.exception
    assert any("Shop at Loblaws" in s.value and "4.44" in s.value for s in app.success)
    assert all("Loblaws Canada" not in s.value for s in app.success)
