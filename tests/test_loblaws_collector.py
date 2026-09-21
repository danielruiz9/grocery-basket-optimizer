"""Offline checks on captured listings; mutations are synthetic edge cases."""

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from src.classification import expected_standardized_unit
from src.collectors.common import DEFAULT_SEARCH_TERMS
from src.collectors.loblaws import (
    LOBLAWS_OUTPUT_COLUMNS, collect_loblaws_listings, fetch_search_results,
    main, normalize_listings, parse_product_tiles, parse_search_results, save_listings,
)
from src.collectors.nofrills import parse_product_tiles as parse_nofrills_tiles

FIXTURE = Path(__file__).parent / "fixtures" / "loblaws_search_results.json"


@pytest.fixture
def capture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def collect_capture(capture):
    return collect_loblaws_listings(
        observed_date=capture["observed_date"], delay_seconds=0,
        fetcher=lambda term: json.dumps({"productTiles": capture["searches"][term]}),
    )


def test_all_seven_searches_fixed_variable_sale_fields(capture):
    assert tuple(capture["searches"]) == DEFAULT_SEARCH_TERMS
    rows = collect_capture(capture)
    assert len(rows) == 84
    assert {r["store"] for r in rows} == {"Loblaws Canada"}
    assert {r["date_observed"] for r in rows} == {"2026-09-20"}
    assert all(r["product_url"].startswith("https://www.loblaws.ca/en/") for r in rows)
    fixed = rows[3]
    assert fixed["package_size"] == 1.1
    assert fixed["package_unit"] == "kg"
    assert fixed["price_per_standard_unit"] == pytest.approx(21 / 11)
    variable = rows[4]
    assert variable["is_variable_weight"] is True
    assert variable["displayed_price"] == variable["sale_price"] == 12.66
    assert variable["regular_price"] == 24.62
    assert variable["sale_status"] == "SALE"
    assert variable["listed_unit_price"] == 10.76
    assert variable["listed_unit"] == "kg"
    assert variable["price_per_standard_unit"] == pytest.approx(1.076)
    assert variable["package_size"] is None
    assert variable["package_unit"] is None
    assert rows[12]["price_per_standard_unit"] == pytest.approx(3.93 / 12)


def test_html_search_scope_deduplication_and_nofrills_defaults(capture):
    tiles = capture["searches"]["chicken breast"]
    data = {"props": {"pageProps": {"initialSearchData": {"layout": {
        "sections": {"mainContentCollection": {"components": [
            {"data": {"productTiles": tiles + [tiles[0]]}},
        ]}},
    }}, "recommendations": {"productTiles": [{"title": "Not a search result"}]}}}}
    html = '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
    parsed = parse_search_results(html, "chicken breast", "2026-09-20")
    assert len(parsed) == 12
    assert parsed[0]["product_title"] == "Spicy Chicken Burgers"
    old = parse_nofrills_tiles(tiles[:1], "chicken breast", "2026-09-20")[0]
    assert old["store"] == "No Frills Canada"
    assert old["product_url"].startswith("https://www.nofrills.ca/")


def test_multi_buy_unconditional_price_and_discrepancy_warning(capture):
    rows = collect_capture(capture)
    pasta = next(r for r in rows if r["product_title"] == "Cellentani Pasta")
    assert pasta["displayed_price"] == pasta["single_item_price"] == 2.99
    assert pasta["multi_buy_quantity"] == 2
    assert pasta["multi_buy_unit_price"] == pasta["sale_price"] == 2.50
    assert pasta["sale_status"] == "multi-buy"
    assert pasta["promotion_text"] == "$2.50 MIN 2"
    assert pasta["price_per_standard_unit"] == pytest.approx(2.99 / 340 * 100)
    assert "10.00x" in pasta["data_quality_warning"]
    assert sum(r["sale_status"] == "multi-buy" for r in rows) == 19


@pytest.mark.parametrize("pricing", [
    {}, {"price": "2.50", "displayPrice": "$2.50"},
    {"price": "2.50", "displayPrice": "$2.50 MIN 2"}, {"price": "2.99"},
])
def test_conditional_price_never_becomes_unconditional(capture, pricing):
    tile = copy.deepcopy(capture["searches"]["pasta"][2])
    tile["pricing"] = pricing
    row = normalize_listings(parse_product_tiles([tile], "pasta"))[0]
    assert row["single_item_price"] is None
    assert row["multi_buy_unit_price"] == 2.5
    assert row["price_per_standard_unit"] is None
    assert "no explicit single-item price" in row["normalization_error"]


def test_unknown_multi_buy_requirement_is_not_invented(capture):
    tile = copy.deepcopy(capture["searches"]["pasta"][2])
    tile["deal"]["text"] = "Multi-buy offer"
    row = normalize_listings(parse_product_tiles([tile], "pasta"))[0]
    assert row["multi_buy_quantity"] is None
    assert row["multi_buy_unit_price"] is None
    assert row["single_item_price"] == 2.99
    assert row["price_per_standard_unit"] == pytest.approx(2.99 / 410 * 100)


def test_missing_fields_and_incompatible_units_are_preserved(capture):
    row = normalize_listings(parse_product_tiles([{"title": "Mystery grocery"}], "grocery"))[0]
    assert row["product_url"] is None
    assert row["package_size"] is None
    assert row["comparison_group"] == "unsupported"
    assert row["normalization_error"] == "No supported package or listed unit was available."
    failures = [r for r in collect_capture(capture) if r["normalization_error"]]
    assert len(failures) == 2
    assert all("expected 100g, found 1unit" in r["normalization_error"] for r in failures)
    assert all(r["package_size_text"] == "1 ea" for r in failures)


def test_every_captured_title_has_reviewed_classification(capture):
    expected = {
        "chicken breast": ["unsupported", "chicken_breast_boneless", "unsupported",
            "chicken_breast_boneless", "chicken_breast_boneless", "chicken_breast_boneless",
            "chicken_breast", "chicken_breast_boneless", "chicken_breast_boneless",
            "unsupported", "chicken_breast_boneless", "unsupported"],
        "eggs": ["large_eggs", "medium_eggs", "large_eggs", "large_eggs", "large_eggs",
            "large_eggs", "large_eggs", "large_eggs", "extra_large_eggs", "large_eggs",
            "shell_eggs_unspecified_size", "medium_eggs"],
        "apples": ["unsupported", "fresh_apples", "unsupported", "unsupported",
            "fresh_apples", "unsupported", "fresh_apples", "unsupported",
            "fresh_apples", "unsupported", "fresh_apples", "unsupported"],
        "bananas": ["unsupported", "fresh_bananas", "unsupported", "unsupported",
            "fresh_bananas", "unsupported", "fresh_bananas", "unsupported",
            "fresh_plantains", "unsupported", "fresh_bananas", "unsupported"],
        "pasta": ["unsupported"] + ["dry_pasta"] * 10 + ["unsupported"],
        "bread": ["artisan_bread", "sliced_bread", "cheese_unspecified_type",
            "artisan_bread", "sliced_bread", "unsupported", "sliced_bread",
            "sliced_bread", "sliced_bread", "unsupported", "sliced_bread", "unsupported"],
        "cheese": ["unsupported", "marble_cheese", "unsupported",
            "unsupported", "cheddar_cheese", "unsupported", "cheddar_cheese",
            "unsupported", "marble_cheese", "cheese_unspecified_type",
            "cheddar_cheese", "mozzarella_cheese"],
    }
    rows = collect_capture(capture)
    for term, groups in expected.items():
        assert [r["comparison_group"] for r in rows if r["search_term"] == term] == groups
    comparable = [r for r in rows if r["comparison_group"] != "unsupported" and not r["normalization_error"]]
    for row in comparable:
        assert row["standardized_unit"] == expected_standardized_unit(row["comparison_group"])
    assert pd.DataFrame(comparable).groupby("comparison_group").standardized_unit.nunique().eq(1).all()


def test_ambrosia_fixed_package_is_not_loose(capture):
    rows = collect_capture(capture)
    apples = {r["product_title"]: r for r in rows if r["comparison_group"] == "fresh_apples"}
    ambrosia = apples["Ambrosia Apples"]
    assert ambrosia["product_form"] == "packaged"
    assert ambrosia["package_size"] == 1.814
    assert ambrosia["package_unit"] == "kg"
    assert ambrosia["displayed_price"] == 9.50
    assert ambrosia["price_per_standard_unit"] == pytest.approx(9.50 / 18.14)
    assert apples["Gala Apples, 4 lb bag"]["product_form"] == "bagged"
    assert apples["Royal Gala Apples"]["product_form"] == "loose"


@pytest.mark.parametrize("unit,size,variable,form", [
    ("kg", 1.814, False, "packaged"), ("g", 1000, False, "packaged"),
    ("lb", 3, False, "packaged"), ("kg", 1.814, True, "loose"),
    ("kg", None, False, "loose"), ("kg", 0, False, "loose"),
    ("kg", float("nan"), False, "loose"), ("count", 1, False, "loose"),
    ("kg", 1.814, None, "loose"),
])
def test_fixed_apple_form_is_general_and_requires_package_evidence(unit, size, variable, form):
    from src.collectors.common import normalize_listing
    row = normalize_listing({
        "product_title": "Apples", "package_size": size, "package_unit": unit,
        "is_variable_weight": variable, "displayed_price": 9.50,
        "listed_unit_price": 5.00, "listed_unit": "kg",
    })
    assert row["product_form"] == form


def test_csv_roundtrip_and_saved_capture_cli(capture, tmp_path):
    output = tmp_path / "prices.csv"
    save_listings(collect_capture(capture), output)
    saved = pd.read_csv(output)
    assert tuple(saved.columns) == LOBLAWS_OUTPUT_COLUMNS
    assert len(saved) == 84
    assert "omega_3" in saved.loc[saved.product_title.str.contains("Omega-3"), "attributes"].iloc[0]
    main(["--capture-json", str(FIXTURE), "--delay-seconds", "0", "--output", str(output)])
    pd.testing.assert_frame_equal(saved, pd.read_csv(output))
    with pytest.raises(SystemExit):
        main(["--capture-json", str(FIXTURE), "--observed-date", "2000-01-01"])


@pytest.mark.parametrize("content", ["", "<h1>Access denied</h1>", "{broken", '<script id="__NEXT_DATA__">broken</script>'])
def test_empty_or_changed_pages_fail_without_output(content):
    assert parse_search_results(content, "eggs") == []
    with pytest.raises(RuntimeError, match="No Loblaws search product tiles"):
        collect_loblaws_listings(["eggs"], fetcher=lambda _: content, delay_seconds=0)


def test_result_limit_and_invalid_limit(capture):
    with pytest.raises(ValueError, match="greater than zero"):
        collect_loblaws_listings(max_results_per_term=0)
    rows = collect_loblaws_listings(
        ["eggs"], max_results_per_term=2, delay_seconds=0,
        fetcher=lambda _: json.dumps({"productTiles": capture["searches"]["eggs"]}),
    )
    assert len(rows) == 2


@pytest.mark.parametrize("status", [401, 403, 429])
def test_blocked_requests_do_not_retry_or_bypass(status):
    session = Mock()
    session.get.return_value.status_code = status
    with pytest.raises(RuntimeError, match="do not bypass"):
        fetch_search_results("eggs", session=session)
    session.get.assert_called_once()
