"""Offline regression tests: reduced live Metro tiles plus explicit edge cases."""

from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from src.basket_costing import estimate_product_cost
from src.classification import classify_product, expected_standardized_unit
from src.collectors.common import DEFAULT_SEARCH_TERMS
from src.collectors.metro import (
    METRO_OUTPUT_COLUMNS,
    collect_metro_listings,
    fetch_search_results,
    main,
    normalize_listing,
    normalize_listings,
    parse_product_records,
    parse_search_results,
    save_listings,
)
from src.integration import SUPERSET_COLUMNS, integrate_price_frames


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "metro_search_results.html"


@pytest.fixture
def html():
    return FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def listings(html):
    return parse_search_results(html, "grocery", "2026-09-20")


def test_live_variable_weight_fields_are_not_inferred(listings):
    assert len(listings) == 12
    chicken = listings[0]
    assert chicken["store"] == "Metro Canada"
    assert chicken["date_observed"] == "2026-09-20"
    assert chicken["package_size_text"] == "5 breasts per tray"
    assert chicken["package_size"] is None
    assert chicken["package_unit"] is None
    assert chicken["is_variable_weight"] is True
    assert chicken["displayed_price"] == 12.58
    assert "avg." in chicken["displayed_price_text"]
    assert chicken["listed_unit_price"] == 11
    assert chicken["listed_unit"] == "kg"
    assert chicken["regular_price"] == 20.92
    assert chicken["regular_price_unit"] == "kg"
    assert chicken["sale_status"] == "sale"
    assert chicken["product_url"].startswith("https://www.metro.ca/en/")
    assert normalize_listing(chicken)["price_per_standard_unit"] == pytest.approx(1.1)
    # An exposed range is not an exact package count or weight.
    assert listings[1]["package_size_text"] == "3 to 4 units per tray"
    assert listings[1]["package_size"] is None
    assert "/ 100" in listings[2]["displayed_price_text"]
    assert listings[2]["listed_unit_price"] == 29.9


def test_count_fixed_weight_and_bagged_apples(listings):
    rows = normalize_listings(listings)
    eggs, apples, loose_apples = rows[3:6]
    assert eggs["comparison_group"] == "large_eggs"
    assert eggs["package_size"] == 18
    assert eggs["package_unit"] == "count"
    assert eggs["price_per_standard_unit"] == pytest.approx(5.99 / 18)
    assert apples["product_form"] == "bagged"
    assert apples["package_size"] == 3
    assert apples["package_unit"] == "lb"
    assert apples["price_per_standard_unit"] == pytest.approx(
        5.99 / (3 * 453.59237) * 100
    )
    assert loose_apples["package_size"] is None
    assert loose_apples["price_per_standard_unit"] == pytest.approx(.439)
    assert rows[9]["price_per_standard_unit"] == pytest.approx(2.29 / 900 * 100)
    assert rows[11]["regular_price"] == 5.99
    assert rows[11]["displayed_price"] == 4.99


def test_noise_is_retained_and_classification_rules_are_reused(listings):
    rows = normalize_listings(listings)
    assert rows[6]["comparison_group"] == "unsupported"  # Frozen fruit blend
    assert rows[8]["comparison_group"] == "unsupported"  # Baby cereal
    assert rows[8]["price_per_standard_unit"] is not None
    for row in rows:
        assert row["comparison_group"] == classify_product(
            row["product_title"]
        )["comparison_group"]


def test_explicit_multibuy_uses_single_price_for_normalization(listings):
    bread = normalize_listing(listings[10])
    assert bread["multi_buy_quantity"] == 2
    assert bread["multi_buy_unit_price"] == 3.5
    assert bread["single_item_price"] == 3.99
    assert bread["displayed_price"] == 3.99
    assert bread["regular_price"] == 4.29
    assert bread["listed_unit_price"] == .52
    assert bread["price_per_standard_unit"] == pytest.approx(3.99 / 675 * 100)
    bread["attributes"] = "|".join(bread["attributes"])
    assert estimate_product_cost(bread, 1, "count")["estimated_item_cost"] == 3.99
    assert estimate_product_cost(bread, 2, "count")["estimated_item_cost"] == 7


def test_conditional_only_price_preserved_but_not_normalized(listings):
    yogurt = normalize_listing(listings[7])
    assert yogurt["multi_buy_quantity"] == 3
    assert yogurt["multi_buy_unit_price"] == pytest.approx(3.66)
    assert yogurt["single_item_price"] is None
    assert yogurt["displayed_price"] is None
    assert yogurt["listed_unit_price"] == .92
    assert yogurt["listed_unit"] == "100g"
    assert yogurt["price_per_standard_unit"] is None
    assert "no explicit single-item price" in yogurt["normalization_error"]


def test_missing_multibuy_quantity_not_invented():
    row = parse_product_records([{
        "title": "White Sliced Bread", "unit_details": "675 g",
        "is_weighted": "false", "current_price_text": "$3.50 ea.",
        "secondary_price_text": "or $3.99 ea. $0.52/100g",
    }], "bread")[0]
    assert row["multi_buy_quantity"] is None
    assert row["displayed_price"] == 3.99
    assert row["multi_buy_unit_price"] == 3.5


def test_missing_fields_duplicates_and_canonical_urls():
    records = [
        {"product_code": "missing", "title": "Mystery listing"},
        {"product_code": "missing", "title": "Duplicate"},
        {"title": "Large Eggs", "href": "/en/product/p/123?q=x#details"},
    ]
    rows = normalize_listings(parse_product_records(records, "eggs"))
    assert len(rows) == 2
    assert rows[0]["displayed_price"] is None
    assert rows[0]["package_size"] is None
    assert rows[0]["is_variable_weight"] is None
    assert rows[0]["normalization_error"]
    assert rows[1]["product_url"] == "https://www.metro.ca/en/product/p/123"


def test_search_scope_ignores_unrelated_tiles(html):
    outside = '''<div class="default-product-tile" data-product-code="AD"
        data-product-name="Unrelated recommendation"></div>'''
    assert len(parse_search_results(outside + html, "eggs")) == 12
    assert parse_search_results(outside, "eggs") == []


def test_shared_quality_rules_flag_discrepancy_and_reject_incompatible_units():
    records = [
        {"title": "Cellentani Pasta", "unit_details": "340 g",
         "current_price_text": "$2.50", "is_weighted": "false",
         "secondary_price_text": "$7.35/100g"},
        {"title": "Boneless Chicken Breast", "unit_details": "1 ea",
         "current_price_text": "$21", "is_weighted": "false"},
        {"title": "Fusilli Pasta", "is_weighted": "false",
         "current_price_text": "$2.29", "secondary_price_text": "$0.25/100g"},
    ]
    pasta, chicken, fallback = normalize_listings(
        parse_product_records(records, "grocery")
    )
    assert pasta["price_per_standard_unit"] == pytest.approx(2.5 / 340 * 100)
    assert "10.00x" in pasta["data_quality_warning"]
    assert chicken["price_per_standard_unit"] is None
    assert "expected 100g, found 1unit" in chicken["normalization_error"]
    assert fallback["package_size"] is None
    assert fallback["price_per_standard_unit"] == .25


def test_default_seven_terms_use_injected_offline_fetcher(html):
    fetcher = Mock(return_value=html)
    rows = collect_metro_listings(
        fetcher=fetcher, max_results_per_term=2,
        observed_date="2026-09-20", delay_seconds=0,
    )
    assert [call.args[0] for call in fetcher.call_args_list] == list(DEFAULT_SEARCH_TERMS)
    assert len(rows) == 14
    assert {row["date_observed"] for row in rows} == {"2026-09-20"}
    with pytest.raises(ValueError, match="greater than zero"):
        collect_metro_listings(max_results_per_term=0, fetcher=fetcher)
    with pytest.raises(RuntimeError, match="No Metro search product tiles"):
        collect_metro_listings(fetcher=lambda _: "<html>Blocked</html>")


@pytest.mark.parametrize("status", [401, 403, 429])
def test_blocked_requests_stop_without_retries(status):
    session = Mock()
    session.get.return_value.status_code = status
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        fetch_search_results("eggs", session=session)
    assert session.get.call_count == 1


def test_http_success_and_access_denied_body():
    session = Mock()
    session.get.return_value.status_code = 200
    session.get.return_value.text = "<html>Search results</html>"
    assert fetch_search_results("eggs", session=session) == "<html>Search results</html>"
    session.get.return_value.raise_for_status.assert_called_once()
    session.get.return_value.text = "Access Denied"
    with pytest.raises(RuntimeError, match="access-denied"):
        fetch_search_results("eggs", session=session)


def test_schema_round_trip_and_existing_integration_rules(listings, tmp_path):
    path = save_listings(normalize_listings(listings), tmp_path / "metro.csv")
    frame = pd.read_csv(path)
    assert tuple(frame.columns) == METRO_OUTPUT_COLUMNS
    result = integrate_price_frames([frame])
    assert set(SUPERSET_COLUMNS) <= set(result.combined.columns)
    assert result.combined[["availability", "product_id", "store_context"]].isna().all().all()
    assert len(result.combined) == 12
    assert "unsupported" not in set(result.optimizer_ready.comparison_group)
    assert result.optimizer_ready.normalization_error.isna().all()
    assert "regular_price_unit" in result.combined


def test_saved_html_cli_requires_date_and_works_offline(html, tmp_path):
    with pytest.raises(SystemExit):
        main(["--html-dir", str(tmp_path)])
    (tmp_path / "eggs.html").write_text(html, encoding="utf-8")
    output = tmp_path / "output.csv"
    main([
        "--html-dir", str(tmp_path), "--observed-date", "2026-09-20",
        "--search-term", "eggs", "--max-results", "2", "--output", str(output),
    ])
    assert len(pd.read_csv(output)) == 2


def test_metro_snapshot_excludes_prepared_food_and_has_consistent_units():
    """Check the stored audit, without collecting or updating integrated data."""
    frame = pd.read_csv(
        Path(__file__).resolve().parents[1] / "data" / "metro_prices_normalized.csv"
    )
    prepared_titles = {
        "Oven Roasted Chicken Breast Strips",
        "Cajun Chicken Breast Roast",
        "Banana Ice Cream with Fudge Chunks and Walnuts, Chunky Monkey",
        "Naan Bread",
    }
    assert prepared_titles <= set(frame.product_title)
    assert frame.loc[
        frame.product_title.isin(prepared_titles), "comparison_group"
    ].eq("unsupported").all()
    supported = frame[frame.comparison_group.ne("unsupported")]
    for row in supported.itertuples(index=False):
        assert row.standardized_unit == expected_standardized_unit(row.comparison_group)
    assert supported.groupby("comparison_group").standardized_unit.nunique().eq(1).all()
    ready = integrate_price_frames([frame]).optimizer_ready
    assert not prepared_titles & set(ready.product_title)
    assert not ready.product_title.str.contains(
        r"\b(?:cooked|coocked|roast|roasted|rotisserie|grilled)\b", case=False
    ).any()
    for title in ("Organic Banana", "Miniature banana", "Thai Banana"):
        assert frame.loc[frame.product_title.eq(title), "comparison_group"].eq(
            "fresh_bananas"
        ).all()
