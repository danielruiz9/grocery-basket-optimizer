"""Offline PDP tests. Base capture is public Walmart data; mutations are synthetic."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pandas as pd
import pytest

from src.collectors import walmart_maintenance as maintenance
from src.integration import integrate_price_frames
from refresh_weekly import detect_changes


CYCLE = date(2026, 10, 15)


@pytest.fixture
def capture():
    return json.loads((Path(__file__).parent / "fixtures/walmart_product_page.json").read_text())


def product(capture):
    return capture["next_data"]["props"]["pageProps"]["initialData"]["data"]["product"]


def previous(capture):
    return {"store": "Walmart Canada", "product_url": capture["final_url"], "product_title": product(capture)["name"],
            "search_term": "eggs", "sale_status": "Rollback", "regular_price": 5,
            "date_observed": "2026-09-18", "displayed_price": 4}


def parse(capture, old=None):
    return maintenance.parse_product_capture(capture, old or previous(capture), CYCLE)


def test_verified_fixed_package_and_ended_promotion(capture):
    row = parse(capture)
    assert row["displayed_price"] == 3.93
    assert row["package_size"] == 12
    assert row["package_unit"] == "count"
    assert row["sale_status"] is None and row["regular_price"] is None
    assert row["sale_price"] is None
    assert row["store_id"] == "1004" and row["store_context"] == "Toronto Stockyards"
    assert row["date_observed"] == "2026-10-15"
    assert row["price_per_standard_unit"] == pytest.approx(3.93 / 12)


@pytest.mark.parametrize("field,value", [("sellerId", "123"), ("sellerDisplayName", "Other"),
    ("sellerType", "EXTERNAL"), ("offerType", "3P"), ("usItemId", "wrong"),
    ("availabilityStatus", "UNKNOWN"), ("name", None)])
def test_identity_seller_availability_fail_closed(capture, field, value):
    old = previous(capture)
    product(capture)[field] = value
    with pytest.raises(maintenance.VerificationError):
        parse(capture, old)


@pytest.mark.parametrize("store_ids", [[], ["1005"], ["1004", "1005"], [None]])
def test_wrong_missing_or_mixed_store(capture, store_ids):
    product(capture)["fulfillmentSummary"] = [{"fulfillment": "PICKUP", "storeId": s} for s in store_ids]
    with pytest.raises(maintenance.VerificationError, match="Store context"):
        parse(capture)


def test_wrong_visible_store(capture):
    capture["store_label"] = "Another store"
    with pytest.raises(maintenance.VerificationError, match="Visible"):
        parse(capture)


@pytest.mark.parametrize("url", ["https://www.walmart.ca/blocked", "https://www.walmart.ca/en/ip/other/123",
                                "https://other.test/en/ip/eggs/10052944"])
def test_redirects(capture, url):
    old = previous(capture)
    capture["final_url"] = url
    with pytest.raises(maintenance.VerificationError):
        parse(capture, old)


def test_canonical_redirect_and_blocked_content(capture):
    product(capture)["canonicalUrl"] = "/en/ip/other/123"
    with pytest.raises(maintenance.VerificationError, match="Canonical"):
        parse(capture)
    capture["next_data"] = {}
    with pytest.raises(maintenance.VerificationError, match="primary"):
        parse(capture, {"product_url": capture["final_url"]})


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), "3.93", True])
def test_missing_invalid_price(capture, value):
    product(capture)["priceInfo"]["currentPrice"]["price"] = value
    with pytest.raises(maintenance.VerificationError):
        parse(capture)


def test_unknown_promotion_not_cleared(capture):
    del product(capture)["priceInfo"]["isPriceReduced"]
    with pytest.raises(maintenance.VerificationError, match="Promotion"):
        parse(capture)


def test_rollback(capture):
    p = product(capture)["priceInfo"]
    p["isPriceReduced"] = True
    p["priceDisplayCodes"]["rollback"] = True
    p["wasPrice"] = {"price": 5}
    row = parse(capture)
    assert (row["sale_status"], row["regular_price"], row["sale_price"]) == ("Rollback", 5, 3.93)


def test_conditional_offer_not_unconditional(capture):
    product(capture)["priceInfo"]["volumePriceTiers"] = [{"quantity": 2, "price": 3}]
    with pytest.raises(maintenance.VerificationError, match="Conditional"):
        parse(capture)


def test_out_of_stock_kept_in_audit_excluded_ready(capture):
    product(capture)["availabilityStatus"] = "OUT_OF_STOCK"
    row = parse(capture)
    result = integrate_price_frames([pd.DataFrame([row])])
    assert len(result.combined) == 1 and result.optimizer_ready.empty
    assert row["verification_status"] == "verified"


def test_out_of_stock_explicit_location_fallback(capture):
    p = product(capture)
    p["availabilityStatus"] = "OUT_OF_STOCK"
    p["fulfillmentSummary"] = []
    p["location"] = {"storeIds": ["1004"], "pickupLocation": {"storeId": "1004"}}
    p["fulfillmentLabel"] = [{"fulfillmentMethod": "PICKUP", "message": "Pickup not available at TORONTO STOCKYARDS"}]
    capture["store_label"] = None
    assert parse(capture)["availability"] == "out_of_stock"
    p["location"]["pickupLocation"]["storeId"] = "1005"
    with pytest.raises(maintenance.VerificationError, match="contradicts"):
        parse(capture)
    p["location"]["pickupLocation"]["storeId"] = "1004"
    p["fulfillmentLabel"] = []
    with pytest.raises(maintenance.VerificationError):
        parse(capture)


def test_stocked_page_cannot_use_out_of_stock_fallback(capture):
    p = product(capture)
    p["fulfillmentSummary"] = []
    p["location"] = {"storeIds": ["1004"]}
    p["fulfillmentLabel"] = [{"fulfillmentMethod": "PICKUP", "message": "Pickup not available at TORONTO STOCKYARDS"}]
    with pytest.raises(maintenance.VerificationError):
        parse(capture)


def test_variable_weight_uses_formatted_rate_not_raw_number(capture):
    p = product(capture)
    p["name"] = "Boneless Chicken Breast 1.23-1.99 kg"
    p["priceInfo"]["priceDisplayCodes"]["finalCostByWeight"] = True
    p["priceInfo"]["currentPrice"]["price"] = 20.38
    p["priceInfo"]["unitPrice"] = {"price": 12.66, "priceString": "$1.27/100g"}
    row = parse(capture)
    assert row["package_size"] is None
    assert row["listed_unit_price"] == row["price_per_standard_unit"] == 1.27
    old = dict(row, displayed_price=17.91, package_size_text="1.23-1.60 kg")
    changes = detect_changes(pd.DataFrame([old]), pd.DataFrame([row]))
    assert not changes["price_increases"] and not changes["price_decreases"]
    assert len(changes["average_package_changes"]) == len(changes["unit_average_discrepancies"]) == 1
    assert len(changes["package_changes"]) == 1
    p["priceInfo"]["unitPrice"] = None
    with pytest.raises(maintenance.VerificationError, match="unit price"):
        parse(capture)


def test_missing_package_not_invented_or_carried_forward(capture):
    product(capture)["name"] = "Great Value Macaroni Pasta"
    product(capture)["priceInfo"]["unitPrice"] = {"priceString": "22¢/100g"}
    row = parse(capture, dict(previous(capture), package_size_text="900 g", package_size=900))
    assert row["package_size"] is None and row["listed_unit_price"] == .22
    assert "not carried forward" in row["data_quality_warning"]
    row = parse(capture, dict(previous(capture), package_size_text=float("nan")))
    assert not row["data_quality_warning"]


@pytest.mark.parametrize("timestamp", ["2026-09-18T12:00:00Z", "2026-10-16T12:00:00Z", "invalid", "2026-10-15T12:00:00"])
def test_capture_freshness(capture, timestamp):
    capture["captured_at"] = timestamp
    with pytest.raises(maintenance.VerificationError):
        parse(capture)


def test_failed_rows_have_no_current_price_or_observation_and_refuse_publication(capture, tmp_path):
    rows = maintenance.refresh_rows([previous(capture)], tmp_path, CYCLE)
    assert rows[0]["date_observed"] is None
    assert rows[0].get("displayed_price") is None
    assert rows[0].get("price_per_standard_unit") is None
    assert rows[0]["normalization_error"]
    with pytest.raises(maintenance.VerificationError, match="replacement refused"):
        maintenance.require_verified(rows)


def test_corrupt_and_wrong_store_captures_are_failed_rows(capture, tmp_path):
    path = tmp_path / "10052944.json"
    path.write_text("not json")
    assert maintenance.refresh_rows([previous(capture)], tmp_path, CYCLE)[0]["verification_status"] == "failed"
    product(capture)["fulfillmentSummary"][0]["storeId"] = "9999"
    path.write_text(json.dumps(capture))
    row = maintenance.refresh_rows([previous(capture)], tmp_path, CYCLE)[0]
    assert row["date_observed"] is None and "Store context" in row["verification_error"]


def test_guide_creates_directory_and_capture_script_without_network(capture, tmp_path):
    folder = tmp_path / "walmart"
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        assert folder.is_dir()
        assert "__NEXT_DATA__" in (folder / "capture_product.js").read_text()
        (folder / "10052944.json").write_text(json.dumps(capture))
        return ""
    opened = []
    maintenance.guide_captures([previous(capture)], folder, CYCLE,
                              input_fn=answer, open_browser=True, opener=opened.append)
    assert len(prompts) == 1 and opened == [capture["final_url"]]
    assert maintenance.refresh_rows([previous(capture)], folder, CYCLE)[0]["verification_status"] == "verified"


def test_all_rows_including_unsupported_staged_and_snapshot_unchanged(capture, tmp_path):
    snapshot = tmp_path / "old.csv"
    second = deepcopy(capture)
    p = product(second)
    p["name"] = "Banana Ice Cream 500 g"
    p["usItemId"] = "123"
    p["canonicalUrl"] = "/en/ip/icecream/123"
    second["final_url"] = "https://www.walmart.ca/en/ip/icecream/123"
    pd.DataFrame([previous(capture), previous(second)]).to_csv(snapshot, index=False)
    before = snapshot.read_bytes()
    for item in (capture, second):
        (tmp_path / f"{product(item)['usItemId']}.json").write_text(json.dumps(item))
    report = maintenance.stage_refresh(snapshot, tmp_path, tmp_path / "stage", CYCLE)
    assert report["verified"] == 2 and report["failed"] == 0
    staged = pd.read_csv(tmp_path / "stage/walmart_prices_normalized.csv")
    assert staged.comparison_group.tolist() == ["large_eggs", "unsupported"]
    assert snapshot.read_bytes() == before
    with pytest.raises(FileExistsError):
        maintenance.stage_refresh(snapshot, tmp_path, tmp_path / "stage", CYCLE)
