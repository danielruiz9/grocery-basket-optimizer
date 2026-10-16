import tempfile
import unittest
import json
from pathlib import Path

import pandas as pd
import pytest

from src.collectors.nofrills import (
    collect_nofrills_listings,
    extract_package_details,
    normalize_listing,
    normalize_listings,
    parse_search_results,
    parse_product_tiles,
    save_listings,
)


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "nofrills_search_results.html"
)


def search_tile(**updates):
    return dict(productId="123_EA", title="Cheddar Cheese", packageSizing="175 g, $1.14/100g",
                pricing={"price": "1.00", "wasPrice": None},
                link="/en/cheddar/p/123_EA", **updates)


@pytest.mark.parametrize("indicator,expected", [
    (None, "unknown"), ({}, "unknown"),
    ({"indicatorId": "OUT", "text": "Out of Stock"}, "out_of_stock"),
    ({"indicatorId": "LOW", "text": "Low Stock"}, "low_in_stock"),
    ({"indicatorId": "IN", "text": "In Stock"}, "in_stock"),
    ({"indicatorId": "UNRECOGNIZED"}, "unknown"),
    ({"indicatorId": "OUT", "text": "In Stock"}, "unknown"),
])
def test_search_availability_preserved_and_filtered(indicator, expected, tmp_path):
    from src.integration import integrate_price_frames
    rows = normalize_listings(parse_product_tiles([search_tile(inventoryIndicator=indicator)], "cheese"))
    assert rows[0]["availability"] == expected
    path = save_listings(rows, tmp_path / "prices.csv")
    result = integrate_price_frames([pd.read_csv(path)])
    assert len(result.combined) == 1
    assert len(result.optimizer_ready) == (0 if expected == "out_of_stock" else 1)


def test_limit_offer_retains_terms_but_is_not_comparable(tmp_path):
    from src.integration import integrate_price_frames
    deal = {"dealPrice": 1, "expiryDate": "2026-10-21T00:00:00Z", "name": "LIMIT",
            "type": "LIMIT", "text": "Limit 6, after limit $2.00", "points": 0}
    rows = normalize_listings(parse_product_tiles([search_tile(deal=deal)], "pasta"))
    row = rows[0]
    assert row["displayed_price"] == 1
    assert row["purchase_limit"] == 6
    assert row["after_limit_price"] == 2
    assert row["single_item_price"] is None
    assert row["sale_expiry"] == deal["expiryDate"]
    assert json.loads(row["promotion_details"])["deal"] == deal
    assert row["price_per_standard_unit"] is None
    assert "Quantity-limited" in row["normalization_error"]
    path = save_listings(rows, tmp_path / "prices.csv")
    result = integrate_price_frames([pd.read_csv(path)])
    assert len(result.combined) == 1 and result.optimizer_ready.empty


def test_limit_without_details_fails_closed_and_regular_sale_still_works():
    rows = normalize_listings(parse_product_tiles([
        search_tile(deal={"type": "LIMIT"}),
        {**search_tile(deal={"type": "SALE", "text": "SAVE $1"}), "productId": "124_EA"},
    ], "cheese"))
    assert rows[0]["normalization_error"]
    assert rows[1]["normalization_error"] is None
    assert rows[1]["price_per_standard_unit"] is not None


def test_other_store_low_level_parser_unchanged():
    row = parse_product_tiles([search_tile(deal={"type": "LIMIT"})], "cheese", store="Loblaws Canada")[0]
    assert "conditional_offer_terms" not in row
    assert "availability" not in row


class TestNoFrillsCollector(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = FIXTURE_PATH.read_text(encoding="utf-8")

    def test_parses_fixed_variable_sale_and_incomplete_rows(self):
        listings = parse_search_results(
            self.html,
            search_term="chicken breast",
            observed_date="2026-09-18",
        )

        self.assertEqual(len(listings), 6)

        fixed = listings[0]
        self.assertEqual(fixed["store"], "No Frills Canada")
        self.assertEqual(fixed["package_size_text"], "508 g")
        self.assertEqual(fixed["package_size"], 508.0)
        self.assertEqual(fixed["package_unit"], "g")
        self.assertEqual(fixed["displayed_price"], 14.00)
        self.assertEqual(fixed["listed_unit_price"], 2.76)
        self.assertEqual(fixed["listed_unit"], "100g")
        self.assertFalse(fixed["is_variable_weight"])
        self.assertEqual(
            fixed["product_url"],
            "https://www.nofrills.ca/en/boneless-skinless-"
            "chicken-breasts/p/20569087_EA",
        )

        variable = listings[1]
        self.assertIsNone(variable["package_size_text"])
        self.assertIsNone(variable["package_size"])
        self.assertTrue(variable["is_variable_weight"])
        self.assertEqual(
            variable["unit_price_text"],
            "$13.21/1kg $5.99/1lb",
        )
        self.assertEqual(variable["listed_unit_price"], 13.21)
        self.assertEqual(variable["listed_unit"], "kg")
        self.assertEqual(variable["sale_status"], "SALE")
        self.assertEqual(variable["sale_price"], 16.20)
        self.assertEqual(variable["regular_price"], 18.89)

        incomplete = listings[5]
        self.assertEqual(incomplete["product_title"], "Mystery Grocery Item")
        self.assertIsNone(incomplete["package_size"])
        self.assertIsNone(incomplete["listed_unit_price"])

    def test_extracts_fixed_count_and_multipack_packages(self):
        eggs = extract_package_details("12 ea, $0.47/1ea")
        pasta = extract_package_details("2 x 500 g, $0.40/100g")

        self.assertEqual(
            eggs,
            {
                "package_size_text": "12 ea",
                "package_size": 12.0,
                "package_unit": "count",
            },
        )
        self.assertEqual(pasta["package_size"], 1000.0)
        self.assertEqual(pasta["package_unit"], "g")

    def test_normalizes_with_existing_classification_and_pricing_layers(self):
        listings = normalize_listings(
            parse_search_results(
                self.html,
                search_term="grocery",
                observed_date="2026-09-18",
            )
        )

        fixed = listings[0]
        self.assertEqual(fixed["category"], "meat")
        self.assertEqual(
            fixed["comparison_group"],
            "chicken_breast_boneless",
        )
        self.assertAlmostEqual(
            fixed["price_per_standard_unit"],
            14.00 / 508 * 100,
        )

        variable = listings[1]
        self.assertEqual(variable["attributes"], ["halal", "boneless", "skinless"])
        self.assertAlmostEqual(
            variable["price_per_standard_unit"],
            1.321,
        )
        self.assertIsNone(variable["normalization_error"])

        eggs = listings[2]
        self.assertEqual(eggs["comparison_group"], "large_eggs")
        self.assertAlmostEqual(eggs["price_per_standard_unit"], 0.47)

        bread = listings[3]
        self.assertEqual(bread["comparison_group"], "sliced_bread")
        self.assertAlmostEqual(bread["price_per_standard_unit"], 0.40)

        incomplete = listings[5]
        self.assertEqual(incomplete["comparison_group"], "unsupported")
        self.assertIsNone(incomplete["price_per_standard_unit"])
        self.assertEqual(
            incomplete["normalization_error"],
            "No supported package or listed unit was available.",
        )

    def test_collection_uses_injected_fixture_fetcher(self):
        requested_terms = []

        def fixture_fetcher(search_term):
            requested_terms.append(search_term)
            return self.html

        listings = collect_nofrills_listings(
            search_terms=("eggs", "bread"),
            max_results_per_term=2,
            observed_date="2026-09-18",
            delay_seconds=0,
            fetcher=fixture_fetcher,
        )

        self.assertEqual(requested_terms, ["eggs", "bread"])
        self.assertEqual(len(listings), 4)
        self.assertEqual(
            {listing["date_observed"] for listing in listings},
            {"2026-09-18"},
        )

    def test_excludes_group_rows_with_incompatible_comparison_units(self):
        listing = normalize_listing(
            {
                "product_title": "Boneless Skinless Chicken Breasts",
                "displayed_price": 21.00,
                "package_size": 1.0,
                "package_unit": "count",
                "listed_unit_price": 21.00,
                "listed_unit": "1unit",
                "is_variable_weight": False,
            }
        )

        self.assertEqual(
            listing["comparison_group"],
            "chicken_breast_boneless",
        )
        self.assertEqual(listing["standardized_unit"], "1unit")
        self.assertIsNone(listing["price_per_standard_unit"])
        self.assertEqual(
            listing["normalization_error"],
            "Incompatible comparison unit for chicken_breast_boneless: "
            "expected 100g, found 1unit.",
        )

    def test_warns_when_listed_and_package_unit_prices_disagree(self):
        listing = normalize_listing(
            {
                "product_title": "Cellentani Pasta",
                "displayed_price": 2.50,
                "package_size": 340.0,
                "package_unit": "g",
                "listed_unit_price": 7.35,
                "listed_unit": "100g",
                "is_variable_weight": False,
            }
        )

        self.assertAlmostEqual(
            listing["price_per_standard_unit"],
            2.50 / 340 * 100,
        )
        self.assertIsNone(listing["normalization_error"])
        self.assertEqual(
            listing["data_quality_warning"],
            "Listed unit price differs from the package-derived price by "
            "10.00x; package-derived normalization was retained.",
        )

    def test_saves_walmart_compatible_csv_schema(self):
        listings = normalize_listings(
            parse_search_results(
                self.html,
                search_term="grocery",
                observed_date="2026-09-18",
            )[:1]
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            output_path = Path(temp_directory) / "prices.csv"
            save_listings(listings, output_path)
            saved = pd.read_csv(output_path)

        self.assertEqual(saved.loc[0, "store"], "No Frills Canada")
        self.assertEqual(saved.loc[0, "attributes"], "boneless|skinless")
        self.assertIn("price_per_standard_unit", saved.columns)
        self.assertIn("data_quality_warning", saved.columns)

    def test_returns_no_rows_when_embedded_search_data_is_missing(self):
        self.assertEqual(
            parse_search_results(
                "<html><body>Access Denied</body></html>",
                search_term="eggs",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
