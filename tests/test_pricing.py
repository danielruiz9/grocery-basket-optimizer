import unittest
import pandas as pd

from src.pricing import (
    calculate_price_per_standard_unit,
    add_standardized_prices,
    get_best_value_by_group,
)


class TestPricing(unittest.TestCase):

    def test_grams_to_100g(self):
        result = calculate_price_per_standard_unit(
            price=5.00,
            package_size=400,
            package_unit="g",
            standardized_unit="100g",
        )

        self.assertAlmostEqual(result, 1.25)

    def test_kilograms_to_100g(self):
        result = calculate_price_per_standard_unit(
            price=10.00,
            package_size=2,
            package_unit="kg",
            standardized_unit="100g",
        )

        self.assertAlmostEqual(result, 0.50)

    def test_millilitres_to_1l(self):
        result = calculate_price_per_standard_unit(
            price=4.00,
            package_size=500,
            package_unit="mL",
            standardized_unit="1L",
        )

        self.assertAlmostEqual(result, 8.00)

    def test_litres_to_1l(self):
        result = calculate_price_per_standard_unit(
            price=6.00,
            package_size=2,
            package_unit="L",
            standardized_unit="1L",
        )

        self.assertAlmostEqual(result, 3.00)

    def test_count_to_unit(self):
        result = calculate_price_per_standard_unit(
            price=4.80,
            package_size=12,
            package_unit="count",
            standardized_unit="1unit",
        )

        self.assertAlmostEqual(result, 0.40)

    def test_unsupported_unit_combination_raises_error(self):
        with self.assertRaises(ValueError):
            calculate_price_per_standard_unit(
                price=5.00,
                package_size=400,
                package_unit="g",
                standardized_unit="1L",
            )

    def test_best_value_by_group(self):
        prices = pd.DataFrame(
            {
                "product_name": [
                    "Cheddar Cheese",
                    "Cheddar Cheese",
                    "Orange Juice",
                    "Orange Juice",
                ],
                "comparison_group": [
                    "cheddar_cheese",
                    "cheddar_cheese",
                    "orange_juice",
                    "orange_juice",
                ],
                "brand": [
                    "Brand A",
                    "Brand B",
                    "Brand C",
                    "Brand D",
                ],
                "store": [
                    "Walmart",
                    "No Frills",
                    "Walmart",
                    "Food Basics",
                ],
                "package_size": [
                    400,
                    450,
                    1.54,
                    1.75,
                ],
                "package_unit": [
                    "g",
                    "g",
                    "L",
                    "L",
                ],
                "price": [
                    5.47,
                    5.99,
                    5.97,
                    5.99,
                ],
                "standardized_unit": [
                    "100g",
                    "100g",
                    "1L",
                    "1L",
                ],
            }
        )

        prices = add_standardized_prices(prices)

        result = get_best_value_by_group(prices)

        cheddar_store = result.loc[
            result["comparison_group"] == "cheddar_cheese",
            "store",
        ].iloc[0]

        orange_juice_store = result.loc[
            result["comparison_group"] == "orange_juice",
            "store",
        ].iloc[0]

        self.assertEqual(cheddar_store, "No Frills")
        self.assertEqual(orange_juice_store, "Food Basics")


if __name__ == "__main__":
    unittest.main()