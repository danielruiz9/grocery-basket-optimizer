"""Offline Sobeys collector tests using a public browser-card capture."""

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from src.classification import expected_standardized_unit
from src.collectors.common import DEFAULT_SEARCH_TERMS
from src.collectors.sobeys import (
    SOBEYS_OUTPUT_COLUMNS,
    capture_records,
    collect_sobeys_listings,
    fetch_search_results,
    main,
    normalize_listings,
    parse_product_records,
    parse_search_results,
    save_listings,
)


FIXTURE = Path(__file__).parent / "fixtures" / "sobeys_search_results.json"


@pytest.fixture
def capture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def collect_capture(capture):
    return collect_sobeys_listings(
        observed_date=capture["observed_date"],
        delay_seconds=0,
        store_context=capture["store_context"],
        fetcher=lambda term: json.dumps(
            {"records": capture_records(capture, term)}
        ),
    )


def test_all_seven_searches_and_public_card_fields(capture):
    assert tuple(capture["searches"]) == DEFAULT_SEARCH_TERMS
    rows = collect_capture(capture)
    assert len(rows) == 84
    assert len({row["product_url"] for row in rows}) == 84
    assert {row["store"] for row in rows} == {"Sobeys Canada"}
    assert {row["date_observed"] for row in rows} == {"2026-09-20"}
    assert {row["store_context"] for row in rows} == {
        "Sobeys Urban Fresh Spadina — 22 Fort York Boulevard, Toronto, ON M5V 3Z2"
    }
    assert all(row["product_url"].startswith("https://www.sobeys.com/products/") for row in rows)
    assert {row["availability"] for row in rows} == {"in_stock", "out_of_stock"}
    assert sum(row["availability"] == "in_stock" for row in rows) == 79
    assert sum(row["availability"] == "out_of_stock" for row in rows) == 5


def test_fixed_variable_and_sale_price_extraction(capture):
    rows = collect_capture(capture)
    variable = next(
        row for row in rows
        if row["product_title"].startswith(
            "Compliments Chicken Breasts Boneless Skinless Value Pack"
        )
    )
    assert variable["is_variable_weight"] is True
    assert variable["package_size"] is None
    assert variable["package_unit"] is None
    assert variable["listed_unit_price"] == 24.89
    assert variable["listed_unit"] == "kg"
    assert variable["price_per_standard_unit"] == pytest.approx(2.489)

    fixed = next(
        row for row in rows
        if row["product_title"] == "Catelli Gluten-Free Pasta Macaroni 340 g"
    )
    assert fixed["package_size"] == 340
    assert fixed["package_unit"] == "g"
    assert fixed["displayed_price"] == 3.49
    assert fixed["listed_unit_price"] == 1.03
    assert fixed["listed_unit"] == "100g"
    assert fixed["price_per_standard_unit"] == pytest.approx(3.49 / 3.4)

    sale = next(row for row in rows if row["product_title"].startswith("Compliments Cozy Coop"))
    assert sale["displayed_price"] == sale["sale_price"] == 4.99
    assert sale["regular_price"] == 5.79
    assert sale["sale_status"] == "sale"
    assert sale["promotion_text"] == "Buy 2 and get 100 PTS"


def test_cash_multi_buy_keeps_unconditional_price_separate(capture):
    rows = collect_capture(capture)
    pasta = next(
        row for row in rows
        if row["product_title"] == "Panache Pasta Casarecce 500 g"
    )
    assert pasta["sale_status"] == "multi-buy"
    assert pasta["multi_buy_quantity"] == 2
    assert pasta["multi_buy_unit_price"] == pasta["sale_price"] == 2.50
    assert pasta["single_item_price"] == pasta["displayed_price"] == 3.29
    assert pasta["price_per_standard_unit"] == pytest.approx(3.29 / 5)
    assert pasta["promotion_text"] == "Buy 2 for $5"
    assert sum(row["sale_status"] == "multi-buy" for row in rows) == 3


def test_points_and_percentage_off_promotions_are_not_fixed_price_multibuys(capture):
    rows = collect_capture(capture)
    points = next(row for row in rows if row["product_title"].startswith("Compliments Cozy Coop"))
    percentage = next(row for row in rows if row["product_title"] == "Banana Yogurt Bowl")
    for row in (points, percentage):
        assert row["sale_status"] == "sale"
        assert row["multi_buy_quantity"] is None
        assert row["multi_buy_unit_price"] is None
        assert row["single_item_price"] is None


def test_every_captured_title_has_reviewed_classification(capture):
    expected = {
        "chicken breast": [
            "unsupported", "unsupported", "unsupported", "unsupported",
            "chicken_breast_boneless", "unsupported",
            "chicken_breast_boneless", "chicken_breast_boneless",
            "chicken_breast_boneless", "chicken_breast_boneless",
            "unsupported", "chicken_breast_boneless",
        ],
        "eggs": [
            "large_eggs", "large_eggs", "large_eggs", "large_eggs",
            "unsupported", "extra_large_eggs", "large_eggs", "large_eggs",
            "unsupported", "large_eggs", "large_eggs", "unsupported",
        ],
        "apples": ["fresh_apples"] * 12,
        "bananas": [
            "fresh_bananas", "fresh_plantains",
        ] + ["unsupported"] * 10,
        "pasta": ["dry_pasta"] * 7 + ["unsupported"] + ["dry_pasta"] * 4,
        "bread": [
            "sliced_bread", "sliced_bread", "sliced_bread", "unsupported",
            "sliced_bread", "sliced_bread", "sliced_bread", "sliced_bread",
            "sliced_bread", "unsupported", "sliced_bread", "sliced_bread",
        ],
        "cheese": [
            "cheese_unspecified_type", "processed_cheese",
            "cheese_unspecified_type", "swiss_cheese",
            "cheese_unspecified_type", "cheese_unspecified_type",
            "cheese_unspecified_type", "cheddar_cheese",
            "cheese_unspecified_type", "cheese_unspecified_type",
            "cheese_unspecified_type", "cheese_unspecified_type",
        ],
    }
    rows = collect_capture(capture)
    for term, groups in expected.items():
        assert [row["comparison_group"] for row in rows if row["search_term"] == term] == groups

    comparable = [
        row for row in rows
        if row["comparison_group"] != "unsupported"
        and not row["normalization_error"]
    ]
    assert sum(row["comparison_group"] != "unsupported" for row in rows) == 62
    assert sum(row["comparison_group"] == "unsupported" for row in rows) == 22
    assert len(comparable) == 61
    for row in comparable:
        assert row["standardized_unit"] == expected_standardized_unit(row["comparison_group"])
    units = pd.DataFrame(comparable).groupby("comparison_group").standardized_unit.nunique()
    assert units.eq(1).all()


def test_false_positive_safeguards_and_forms(capture):
    rows = {row["product_title"]: row for row in collect_capture(capture)}
    assert rows["Compliments Chicken Breast Roast Cooked"]["comparison_group"] == "unsupported"
    assert rows["Piller's Smoked Chicken Breast Slices 125 g"]["comparison_group"] == "unsupported"
    assert rows["Honey Garlic Boneless Chicken Breast"]["comparison_group"] == "unsupported"
    assert rows["Mumbai Chicken Breast Boneless Skinless"]["comparison_group"] == "unsupported"
    assert rows["Chicken Breast Stir-Fry Boneless Skinless"]["comparison_group"] == "unsupported"
    assert rows["Plantain 1 Bunch"]["comparison_group"] == "fresh_plantains"
    assert rows["Tradition Smooth Strawberry Banana 950 ml"]["comparison_group"] == "unsupported"
    assert rows["NuPasta Gluten-Free Pasta Konjac Fettucine 210 g"]["comparison_group"] == "unsupported"
    assert rows["Cracker Barrel Signature Block Cheese Smoked Cheddar 340 g"]["comparison_group"] == "cheddar_cheese"
    assert rows["Compliments Processed Sliced Cheese Swiss 20 Slices 400 g"]["comparison_group"] == "processed_cheese"


def test_incompatible_page_unit_is_preserved_but_not_comparable(capture):
    rows = collect_capture(capture)
    failures = [row for row in rows if row["normalization_error"]]
    assert len(failures) == 1
    row = failures[0]
    assert row["product_title"] == (
        "Mina Halal Chicken Breasts Boneless Skinless 4 Pieces"
    )
    assert row["package_size_text"].startswith("1 EA")
    assert row["package_size"] == 1
    assert row["package_unit"] == "count"
    assert row["price_per_standard_unit"] is None
    assert "expected 100g, found 1unit" in row["normalization_error"]


def test_title_weight_fallback_and_structured_weight_conflict(capture):
    rows = {row["product_title"]: row for row in collect_capture(capture)}

    paula = rows["Paula Apples Red 3 lb"]
    assert paula["package_size_text"] == "3 lb"
    assert paula["package_size"] == 3
    assert paula["package_unit"] == "lb"
    assert paula["product_form"] == "packaged"
    assert paula["standardized_unit"] == "100g"
    assert paula["price_per_standard_unit"] == pytest.approx(
        2.99 / (3 * 453.59237 / 100)
    )
    assert paula["normalization_error"] is None
    assert "Structured package data reports 1 EA" in paula["data_quality_warning"]
    assert "title weight 3 lb was used" in paula["data_quality_warning"]

    honeycrisp = rows["Lil Snappers Organic Apples Honeycrisp 1.36 kg"]
    assert honeycrisp["package_size_text"] == "907 G ($1.10 per 100g)"
    assert honeycrisp["package_size"] == 907
    assert honeycrisp["package_unit"] == "g"
    assert honeycrisp["price_per_standard_unit"] == pytest.approx(9.99 / 9.07)
    assert honeycrisp["normalization_error"] is None
    assert "Title package weight 1.36 kg differs" in honeycrisp[
        "data_quality_warning"
    ]
    assert "captured structured data was retained" in honeycrisp[
        "data_quality_warning"
    ]


def test_rendered_html_card_extraction_is_scoped_and_deterministic():
    html = """
    <aside><div data-id="noise"><span>not a product</span></div></aside>
    <main><div data-id="85871">
      <a aria-label="Click here to go to Italpasta Pasta Macaroni Elbows 900 g product detail page"
         href="/products/italpasta-pasta-macaroni-elbows-900-g"></a>
      <img alt="lockedPrice"><p><span>$3.99</span></p>
      <p class="text-sm">900 G ($0.44 per 100g)</p>
      <p class="card-title">Italpasta Pasta Macaroni Elbows 900 g</p>
      <p>Buy 2 for $5</p><span>Out of Stock</span>
    </div></main>
    """
    rows = parse_search_results(html, "pasta", "2026-09-20", "Test Store")
    assert len(rows) == 1
    assert rows[0]["product_title"] == "Italpasta Pasta Macaroni Elbows 900 g"
    assert rows[0]["product_url"].endswith("/products/italpasta-pasta-macaroni-elbows-900-g")
    assert rows[0]["package_size"] == 900
    assert rows[0]["promotion_text"] == "Buy 2 for $5"
    assert rows[0]["availability"] == "out_of_stock"


def test_large_listed_unit_discrepancy_uses_shared_warning():
    records = [{
        "product_id": "bad-unit",
        "product_title": "Cellentani Pasta 340 g",
        "product_url": "/products/cellentani-pasta-340-g",
        "price_text": "$2.50",
        "package_text": "340 G ($7.35 per 100g)",
    }]
    row = normalize_listings(parse_product_records(records, "pasta"))[0]
    assert row["price_per_standard_unit"] == pytest.approx(2.50 / 3.4)
    assert "10.00x" in row["data_quality_warning"]
    assert "package-derived normalization was retained" in row["data_quality_warning"]


def test_multi_buy_without_single_price_fails_closed(capture):
    record = copy.deepcopy(capture_records(capture, "pasta")[3])
    record["price_text"] = ""
    row = normalize_listings(parse_product_records([record], "pasta"))[0]
    assert row["single_item_price"] is None
    assert row["multi_buy_quantity"] == 2
    assert row["multi_buy_unit_price"] == 2.5
    assert row["price_per_standard_unit"] is None
    assert "no explicit single-item price" in row["normalization_error"]


def test_missing_fields_are_preserved(capture):
    row = normalize_listings(parse_product_records([{"product_title": "Mystery grocery"}], "grocery"))[0]
    assert row["product_url"] is None
    assert row["package_size"] is None
    assert row["comparison_group"] == "unsupported"
    assert row["normalization_error"] == "No supported package or listed unit was available."

    broken = copy.deepcopy(capture)
    broken["searches"]["eggs"][0] = broken["searches"]["eggs"][0][:-1]
    with pytest.raises(ValueError, match="expected 8"):
        capture_records(broken, "eggs")


def test_csv_roundtrip_and_capture_cli(capture, tmp_path):
    output = tmp_path / "sobeys.csv"
    rows = collect_capture(capture)
    save_listings(rows, output)
    saved = pd.read_csv(output)
    assert tuple(saved.columns) == SOBEYS_OUTPUT_COLUMNS
    assert len(saved) == 84
    assert saved["product_id"].notna().all()
    assert set(saved["availability"]) == {"in_stock", "out_of_stock"}
    main(["--capture-json", str(FIXTURE), "--delay-seconds", "0", "--output", str(output)])
    pd.testing.assert_frame_equal(saved, pd.read_csv(output))
    with pytest.raises(SystemExit):
        main(["--capture-json", str(FIXTURE), "--observed-date", "2000-01-01"])


@pytest.mark.parametrize("content", ["", "<h1>Search</h1>", "{broken"])
def test_empty_or_changed_pages_fail_without_output(content):
    assert parse_search_results(content, "eggs") == []
    with pytest.raises(RuntimeError, match="No Sobeys search product cards"):
        collect_sobeys_listings(["eggs"], fetcher=lambda _: content, delay_seconds=0)


def test_result_limit_and_invalid_limit(capture):
    with pytest.raises(ValueError, match="greater than zero"):
        collect_sobeys_listings(max_results_per_term=0)
    rows = collect_sobeys_listings(
        ["eggs"],
        max_results_per_term=2,
        delay_seconds=0,
        fetcher=lambda _: json.dumps({"records": capture_records(capture, "eggs")}),
    )
    assert len(rows) == 2


@pytest.mark.parametrize("status", [401, 403, 429])
def test_blocked_requests_do_not_retry_or_bypass(status):
    session = Mock()
    session.get.return_value.status_code = status
    with pytest.raises(RuntimeError, match="do not bypass"):
        fetch_search_results("eggs", session=session)
    session.get.assert_called_once()
