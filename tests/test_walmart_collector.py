import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.collectors.walmart import (
    collect_walmart_listings,
    extract_package_details,
    normalize_listing,
    normalize_listings,
    parse_search_results,
    save_listings,
)


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "walmart_search_results.html"
)


class TestWalmartCollector(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.html = FIXTURE_PATH.read_text(encoding="utf-8")

    def test_parses_fixed_variable_sale_and_missing_fields(self):
        listings = parse_search_results(
            self.html,
            search_term="chicken breast",
            observed_date="2026-09-18",
        )

        self.assertEqual(len(listings), 7)

        fixed = listings[0]
        self.assertEqual(
            fixed["product_title"],
            "Fresh Boneless Skinless Chicken Breasts 900 g",
        )
        self.assertEqual(fixed["store"], "Walmart Canada")
        self.assertEqual(fixed["package_size"], 900.0)
        self.assertEqual(fixed["package_unit"], "g")
        self.assertEqual(fixed["displayed_price"], 14.00)
        self.assertEqual(fixed["listed_unit_price"], 1.56)
        self.assertEqual(fixed["listed_unit"], "100g")
        self.assertFalse(fixed["is_variable_weight"])

        variable = listings[1]
        self.assertEqual(variable["package_size_text"], "0.70 - 0.94 KG")
        self.assertIsNone(variable["package_size"])
        self.assertEqual(variable["package_unit"], "kg")
        self.assertTrue(variable["is_variable_weight"])
        self.assertIsNone(variable["sale_status"])

        sale = listings[2]
        self.assertEqual(sale["sale_status"], "Rollback")
        self.assertEqual(sale["sale_price"], 8.53)
        self.assertEqual(sale["regular_price"], 9.99)
        self.assertEqual(
            sale["product_url"],
            "https://www.walmart.ca/en/ip/prime-chicken-breasts/PRDSALE",
        )

        missing_price = listings[3]
        self.assertIsNone(missing_price["displayed_price"])
        self.assertIsNone(missing_price["unit_price_text"])
        self.assertIsNone(missing_price["listed_unit_price"])

    def test_extracts_displayed_pound_package_without_converting_it(self):
        package = extract_package_details(
            "Your Fresh Market Organic Gala Apples, 2 lb Bag"
        )

        self.assertEqual(
            package,
            {
                "package_size_text": "2 lb",
                "package_size": 2.0,
                "package_unit": "lb",
            },
        )

    def test_normalizes_with_existing_classification_and_pricing_layers(self):
        raw_listings = parse_search_results(
            self.html,
            search_term="grocery",
            observed_date="2026-09-18",
        )
        listings = normalize_listings(raw_listings)

        fixed = listings[0]
        self.assertEqual(fixed["category"], "meat")
        self.assertEqual(
            fixed["comparison_group"],
            "chicken_breast_boneless",
        )
        self.assertEqual(fixed["standardized_unit"], "100g")
        self.assertAlmostEqual(
            fixed["price_per_standard_unit"],
            14.00 / 900 * 100,
        )

        variable = listings[1]
        self.assertAlmostEqual(
            variable["price_per_standard_unit"],
            1.29,
        )
        self.assertIsNone(variable["normalization_error"])

        sale = listings[2]
        self.assertAlmostEqual(sale["price_per_standard_unit"], 1.40)

        missing_price = listings[3]
        self.assertIsNone(missing_price["price_per_standard_unit"])
        self.assertIn(
            "Fixed-package products require price, package size",
            missing_price["normalization_error"],
        )

        eggs = listings[4]
        self.assertEqual(eggs["package_size"], 12.0)
        self.assertEqual(eggs["package_unit"], "count")
        self.assertEqual(eggs["comparison_group"], "large_eggs")
        self.assertAlmostEqual(eggs["price_per_standard_unit"], 0.40)

        cheese = listings[5]
        self.assertEqual(cheese["package_size_text"], "2 x 300 g")
        self.assertEqual(cheese["package_size"], 600.0)
        self.assertEqual(cheese["product_form"], "shredded")
        self.assertAlmostEqual(cheese["price_per_standard_unit"], 1.50)

        apple = listings[6]
        self.assertEqual(apple["displayed_price"], 0.22)
        self.assertEqual(apple["listed_unit_price"], 0.22)
        self.assertEqual(apple["comparison_group"], "fresh_apples")
        self.assertAlmostEqual(apple["price_per_standard_unit"], 0.22)

    def test_normalizes_representative_live_fixed_package_rows(self):
        bread = normalize_listing(
            {
                "product_title": "Dempster’s® White Sliced Bread",
                "displayed_price": 2.68,
                "package_size": None,
                "package_unit": None,
                "listed_unit_price": 0.40,
                "listed_unit": "100g",
                "is_variable_weight": False,
            }
        )
        apples = normalize_listing(
            {
                "product_title": (
                    "Your Fresh Market Organic Gala Apples, 2 lb Bag"
                ),
                "displayed_price": 6.44,
                "package_size": 2.0,
                "package_unit": "lb",
                "listed_unit_price": None,
                "listed_unit": None,
                "is_variable_weight": False,
            }
        )

        self.assertAlmostEqual(bread["price_per_standard_unit"], 0.40)
        self.assertIsNone(bread["normalization_error"])
        self.assertAlmostEqual(
            apples["price_per_standard_unit"],
            6.44 / (2 * 453.59237) * 100,
        )
        self.assertIsNone(apples["normalization_error"])

    def test_collection_uses_injected_fixture_fetcher(self):
        requested_terms = []

        def fixture_fetcher(search_term):
            requested_terms.append(search_term)
            return self.html

        listings = collect_walmart_listings(
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

    def test_saves_stable_csv_schema(self):
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

        self.assertEqual(saved.loc[0, "store"], "Walmart Canada")
        self.assertEqual(saved.loc[0, "attributes"], "boneless|skinless")
        self.assertIn("price_per_standard_unit", saved.columns)


if __name__ == "__main__":
    unittest.main()
