import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.collectors.foodbasics import (
    collect_foodbasics_listings,
    extract_package_details,
    normalize_listing,
    normalize_listings,
    parse_search_results,
    save_listings,
)


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "foodbasics_search_results.html"
)


class TestFoodBasicsCollector(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = FIXTURE_PATH.read_text(encoding="utf-8")

    def test_parses_fixed_variable_sale_multibuy_and_incomplete_rows(self):
        listings = parse_search_results(
            self.html,
            search_term="grocery",
            observed_date="2026-09-19",
        )

        self.assertEqual(len(listings), 7)

        fixed = listings[0]
        self.assertEqual(fixed["store"], "Food Basics Canada")
        self.assertEqual(fixed["package_size_text"], "900 g")
        self.assertEqual(fixed["package_size"], 900.0)
        self.assertEqual(fixed["package_unit"], "g")
        self.assertEqual(fixed["displayed_price"], 14.00)
        self.assertEqual(fixed["listed_unit_price"], 1.56)
        self.assertEqual(fixed["listed_unit"], "100g")
        self.assertFalse(fixed["is_variable_weight"])
        self.assertEqual(
            fixed["product_url"],
            "https://www.foodbasics.ca/aisles/meat-poultry/"
            "chicken-turkey/breasts/boneless-and-skinless-"
            "chicken-breast/p/234960",
        )

        variable = listings[1]
        self.assertIsNone(variable["package_size_text"])
        self.assertTrue(variable["is_variable_weight"])
        self.assertEqual(variable["displayed_price"], 18.16)
        self.assertEqual(variable["listed_unit_price"], 16.51)
        self.assertEqual(variable["listed_unit"], "kg")

        sale = listings[2]
        self.assertEqual(sale["sale_status"], "sale")
        self.assertEqual(sale["sale_price"], 2.98)
        self.assertEqual(sale["regular_price"], 5.99)

        multibuy = listings[4]
        self.assertEqual(multibuy["displayed_price"], 3.69)
        self.assertEqual(multibuy["sale_status"], "multi-buy")
        self.assertEqual(multibuy["sale_price"], 3.00)
        self.assertIsNone(multibuy["regular_price"])
        self.assertEqual(multibuy["multi_buy_quantity"], 2)
        self.assertEqual(multibuy["multi_buy_unit_price"], 3.00)
        self.assertEqual(multibuy["single_item_price"], 3.69)

        incomplete = listings[6]
        self.assertEqual(incomplete["product_title"], "Mystery Grocery Item")
        self.assertIsNone(incomplete["package_size"])
        self.assertIsNone(incomplete["displayed_price"])

    def test_extracts_count_multipack_and_mixed_package_labels(self):
        eggs = extract_package_details("12 un")
        yogurt = extract_package_details("4x100 g")
        muffins = extract_package_details("6 un - 600 g")

        self.assertEqual(eggs["package_size"], 12.0)
        self.assertEqual(eggs["package_unit"], "count")
        self.assertEqual(yogurt["package_size"], 400.0)
        self.assertEqual(yogurt["package_unit"], "g")
        self.assertEqual(muffins["package_size"], 600.0)
        self.assertEqual(muffins["package_unit"], "g")

    def test_normalizes_with_classification_pricing_and_quality_layers(self):
        listings = normalize_listings(
            parse_search_results(
                self.html,
                search_term="grocery",
                observed_date="2026-09-19",
            )
        )

        fixed = listings[0]
        self.assertEqual(
            fixed["comparison_group"],
            "chicken_breast_boneless",
        )
        self.assertAlmostEqual(
            fixed["price_per_standard_unit"],
            14.00 / 900 * 100,
        )

        variable = listings[1]
        self.assertAlmostEqual(
            variable["price_per_standard_unit"],
            1.651,
        )
        self.assertIsNone(variable["normalization_error"])

        apples = listings[2]
        self.assertEqual(apples["comparison_group"], "fresh_apples")
        self.assertEqual(apples["product_form"], "bagged")
        self.assertAlmostEqual(
            apples["price_per_standard_unit"],
            2.98 / (3 * 453.59237) * 100,
        )

        eggs = listings[3]
        self.assertEqual(eggs["comparison_group"], "large_eggs")
        self.assertAlmostEqual(eggs["price_per_standard_unit"], 6.09 / 12)
        self.assertIn("12.00x", eggs["data_quality_warning"])

        bread = listings[4]
        self.assertEqual(bread["comparison_group"], "sliced_bread")
        self.assertAlmostEqual(
            bread["price_per_standard_unit"],
            3.69 / 675 * 100,
        )

        noise = listings[5]
        self.assertEqual(noise["comparison_group"], "unsupported")
        self.assertIsNotNone(noise["price_per_standard_unit"])

        incomplete = listings[6]
        self.assertEqual(incomplete["comparison_group"], "unsupported")
        self.assertIsNone(incomplete["price_per_standard_unit"])
        self.assertEqual(
            incomplete["normalization_error"],
            "No supported package or listed unit was available.",
        )

    def test_excludes_incompatible_group_unit_from_comparison(self):
        listing = normalize_listing(
            {
                "product_title": "Boneless Skinless Chicken Breast",
                "displayed_price": 21.00,
                "package_size": 1.0,
                "package_unit": "count",
                "listed_unit_price": 21.00,
                "listed_unit": "1unit",
                "is_variable_weight": False,
            }
        )

        self.assertIsNone(listing["price_per_standard_unit"])
        self.assertEqual(
            listing["normalization_error"],
            "Incompatible comparison unit for chicken_breast_boneless: "
            "expected 100g, found 1unit.",
        )

    def test_does_not_use_multibuy_price_without_single_item_price(self):
        listings = parse_search_results(
            """
            <div class="default-product-tile" data-product-code="BREAD"
                 data-product-name="White Sliced Bread"
                 data-is-weighted="false">
              <span class="head__title">White Sliced Bread</span>
              <span class="head__unit-details">675 g</span>
              <div class="pricing__sale-price">2 / $6.00</div>
              <div class="pricing__secondary-price">$0.44 /100g</div>
            </div>
            """,
            search_term="bread",
            observed_date="2026-09-19",
        )

        listing = listings[0]
        self.assertIsNone(listing["displayed_price"])
        self.assertEqual(listing["multi_buy_quantity"], 2)
        self.assertEqual(listing["multi_buy_unit_price"], 3.00)
        self.assertIsNone(listing["single_item_price"])
        self.assertIsNone(listing["listed_unit_price"])
        self.assertIsNone(
            normalize_listing(listing)["price_per_standard_unit"]
        )

    def test_does_not_invent_missing_multibuy_quantity(self):
        listings = parse_search_results(
            """
            <div class="default-product-tile" data-product-code="BREAD"
                 data-product-name="White Sliced Bread"
                 data-is-weighted="false">
              <span class="head__title">White Sliced Bread</span>
              <span class="head__unit-details">675 g</span>
              <div class="pricing__sale-price">$3.00 ea.</div>
              <div class="pricing__secondary-price">
                or $3.69 ea. $0.44 /100g
              </div>
            </div>
            """,
            search_term="bread",
            observed_date="2026-09-19",
        )

        listing = listings[0]
        self.assertEqual(listing["displayed_price"], 3.69)
        self.assertIsNone(listing["multi_buy_quantity"])
        self.assertEqual(listing["multi_buy_unit_price"], 3.00)
        self.assertEqual(listing["single_item_price"], 3.69)

    def test_collection_uses_injected_fixture_fetcher(self):
        requested_terms = []

        def fixture_fetcher(search_term):
            requested_terms.append(search_term)
            return self.html

        listings = collect_foodbasics_listings(
            search_terms=("eggs", "bread"),
            max_results_per_term=2,
            observed_date="2026-09-19",
            delay_seconds=0,
            fetcher=fixture_fetcher,
        )

        self.assertEqual(requested_terms, ["eggs", "bread"])
        self.assertEqual(len(listings), 4)
        self.assertEqual(
            {listing["date_observed"] for listing in listings},
            {"2026-09-19"},
        )

    def test_saves_quality_schema(self):
        listings = normalize_listings(
            parse_search_results(
                self.html,
                search_term="grocery",
                observed_date="2026-09-19",
            )[:1]
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            output_path = Path(temp_directory) / "prices.csv"
            save_listings(listings, output_path)
            saved = pd.read_csv(output_path)

        self.assertEqual(saved.loc[0, "store"], "Food Basics Canada")
        self.assertIn("price_per_standard_unit", saved.columns)
        self.assertIn("multi_buy_quantity", saved.columns)
        self.assertIn("multi_buy_unit_price", saved.columns)
        self.assertIn("single_item_price", saved.columns)
        self.assertIn("data_quality_warning", saved.columns)
        self.assertIn("normalization_error", saved.columns)

    def test_returns_no_rows_when_product_tiles_are_missing(self):
        self.assertEqual(
            parse_search_results(
                "<html><body>Access Denied</body></html>",
                search_term="eggs",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
