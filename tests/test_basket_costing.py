import unittest

import pandas as pd

from src.basket_costing import (
    cost_candidate_products,
    estimate_product_cost,
    select_lowest_cost_product,
)


def _product(
    *,
    title="Test Product",
    store="Test Store",
    group="test_group",
    package_size=500,
    package_unit="g",
    displayed_price=4.00,
    is_variable_weight=False,
    listed_unit_price=None,
    listed_unit=None,
    multi_buy_quantity=None,
    multi_buy_unit_price=None,
    single_item_price=None,
    normalization_error=None,
):
    return {
        "store": store,
        "comparison_group": group,
        "product_title": title,
        "product_url": f"https://example.test/{title}",
        "package_size_text": f"{package_size} {package_unit}",
        "package_size": package_size,
        "package_unit": package_unit,
        "displayed_price": displayed_price,
        "listed_unit_price": listed_unit_price,
        "listed_unit": listed_unit,
        "is_variable_weight": is_variable_weight,
        "sale_status": "multi-buy" if multi_buy_unit_price else None,
        "multi_buy_quantity": multi_buy_quantity,
        "multi_buy_unit_price": multi_buy_unit_price,
        "single_item_price": single_item_price,
        "standardized_unit": "1unit" if package_unit == "count" else "100g",
        "price_per_standard_unit": 1.0,
        "normalization_error": normalization_error,
    }


class TestBasketCosting(unittest.TestCase):

    def test_fixed_weight_packages_use_ceiling_division(self):
        result = estimate_product_cost(
            _product(package_size=500, displayed_price=4.00),
            requested_quantity=900,
            requested_unit="g",
        )

        self.assertEqual(result["package_count"], 2)
        self.assertEqual(result["fulfilled_quantity"], 1000)
        self.assertEqual(result["excess_quantity"], 100)
        self.assertEqual(result["effective_package_price"], 4.00)
        self.assertEqual(result["estimated_item_cost"], 8.00)

    def test_variable_weight_items_use_listed_unit_price_directly(self):
        result = estimate_product_cost(
            _product(
                package_size=None,
                package_unit=None,
                displayed_price=18.00,
                is_variable_weight=True,
                listed_unit_price=15.41,
                listed_unit="kg",
            ),
            requested_quantity=1500,
            requested_unit="g",
        )

        self.assertIsNone(result["package_count"])
        self.assertEqual(result["fulfilled_quantity"], 1500)
        self.assertEqual(result["excess_quantity"], 0)
        self.assertIsNone(result["effective_package_price"])
        self.assertAlmostEqual(result["estimated_item_cost"], 23.115)

    def test_count_items_are_costed_as_whole_packages(self):
        result = estimate_product_cost(
            _product(
                package_size=12,
                package_unit="count",
                displayed_price=3.99,
            ),
            requested_quantity=18,
            requested_unit="eggs",
        )

        self.assertEqual(result["package_count"], 2)
        self.assertEqual(result["fulfilled_quantity"], 24)
        self.assertEqual(result["excess_quantity"], 6)
        self.assertAlmostEqual(result["estimated_item_cost"], 7.98)

    def test_package_overshoot_is_reported_in_requested_units(self):
        result = estimate_product_cost(
            _product(
                package_size=1,
                package_unit="kg",
                displayed_price=8.00,
            ),
            requested_quantity=1100,
            requested_unit="g",
        )

        self.assertEqual(result["package_count"], 2)
        self.assertEqual(result["fulfilled_quantity"], 2000)
        self.assertEqual(result["excess_quantity"], 900)

    def test_multi_buy_price_applies_when_package_threshold_is_met(self):
        result = estimate_product_cost(
            _product(
                package_size=675,
                displayed_price=3.69,
                multi_buy_quantity=2,
                multi_buy_unit_price=3.00,
                single_item_price=3.69,
            ),
            requested_quantity=900,
            requested_unit="g",
        )

        self.assertEqual(result["package_count"], 2)
        self.assertTrue(result["multi_buy_applied"])
        self.assertEqual(result["effective_package_price"], 3.00)
        self.assertEqual(result["estimated_item_cost"], 6.00)

    def test_single_item_price_applies_below_multi_buy_threshold(self):
        result = estimate_product_cost(
            _product(
                package_size=675,
                displayed_price=3.69,
                multi_buy_quantity=2,
                multi_buy_unit_price=3.00,
                single_item_price=3.69,
            ),
            requested_quantity=600,
            requested_unit="g",
        )

        self.assertEqual(result["package_count"], 1)
        self.assertFalse(result["multi_buy_applied"])
        self.assertEqual(result["effective_package_price"], 3.69)
        self.assertEqual(result["estimated_item_cost"], 3.69)

    def test_unknown_multi_buy_quantity_does_not_apply_promotion(self):
        result = estimate_product_cost(
            _product(
                package_size=675,
                displayed_price=3.69,
                multi_buy_quantity=None,
                multi_buy_unit_price=3.00,
                single_item_price=3.69,
            ),
            requested_quantity=1350,
            requested_unit="g",
        )

        self.assertFalse(result["multi_buy_applied"])
        self.assertEqual(result["effective_package_price"], 3.69)
        self.assertEqual(result["estimated_item_cost"], 7.38)

    def test_weight_and_volume_units_convert_safely(self):
        weight = estimate_product_cost(
            _product(package_size=500, displayed_price=3.00),
            requested_quantity=1,
            requested_unit="kg",
        )
        volume = estimate_product_cost(
            _product(
                package_size=1,
                package_unit="L",
                displayed_price=2.50,
            ),
            requested_quantity=1500,
            requested_unit="mL",
        )

        self.assertEqual(weight["package_count"], 2)
        self.assertEqual(weight["fulfilled_quantity"], 1)
        self.assertEqual(volume["package_count"], 2)
        self.assertEqual(volume["fulfilled_quantity"], 2000)
        self.assertEqual(volume["excess_quantity"], 500)
        self.assertEqual(volume["estimated_item_cost"], 5.00)

    def test_selects_lowest_actual_cost_not_lowest_normalized_price(self):
        larger_package = _product(
            title="Larger Package",
            package_size=1000,
            displayed_price=6.00,
        )
        larger_package["price_per_standard_unit"] = 0.60
        smaller_package = _product(
            title="Smaller Package",
            package_size=500,
            displayed_price=4.00,
        )
        smaller_package["price_per_standard_unit"] = 0.80

        result = select_lowest_cost_product(
            store="Test Store",
            comparison_group="test_group",
            requested_quantity=400,
            requested_unit="g",
            prices=pd.DataFrame([larger_package, smaller_package]),
        )

        self.assertEqual(result["product_title"], "Smaller Package")
        self.assertEqual(result["estimated_item_cost"], 4.00)

    def test_returns_all_compatible_costed_candidates(self):
        products = [
            _product(title="One Kilogram", package_size=1000),
            _product(title="Half Kilogram", package_size=500),
            _product(
                title="Wrong Unit",
                package_size=1,
                package_unit="L",
            ),
        ]

        results = cost_candidate_products(
            store="Test Store",
            comparison_group="test_group",
            requested_quantity=900,
            requested_unit="g",
            prices=pd.DataFrame(products),
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(
            {result["requested_quantity"] for result in results},
            {900.0},
        )
        self.assertTrue(
            all("estimated_item_cost" in result for result in results)
        )

    def test_unavailable_and_incompatible_products_raise_clear_error(self):
        prices = pd.DataFrame(
            [
                _product(
                    title="Missing Package Size",
                    package_size=None,
                ),
                _product(
                    title="Invalid Normalization",
                    normalization_error="Invalid unit.",
                ),
            ]
        )

        with self.assertRaisesRegex(ValueError, "No compatible products"):
            select_lowest_cost_product(
                store="Test Store",
                comparison_group="test_group",
                requested_quantity=1,
                requested_unit="L",
                prices=prices,
            )


if __name__ == "__main__":
    unittest.main()
