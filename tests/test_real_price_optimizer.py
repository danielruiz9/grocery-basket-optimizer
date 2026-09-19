import unittest

import pandas as pd

from src.real_price_optimizer import optimize_real_price_basket


def _request(group, quantity=1, unit="count"):
    return {
        "comparison_group": group,
        "quantity": quantity,
        "unit": unit,
    }


def _fixed_price_row(
    store,
    group,
    price,
    *,
    package_size=1,
    package_unit="count",
    title=None,
    normalization_error=None,
    multi_buy_quantity=None,
    multi_buy_unit_price=None,
    single_item_price=None,
):
    title = title or f"{store} {group}"
    standardized_unit = (
        "1unit" if package_unit == "count" else "100g"
    )
    return {
        "comparison_group": group,
        "store": store,
        "product_title": title,
        "product_url": f"https://example.test/{store}/{title}",
        "package_size_text": f"{package_size} {package_unit}",
        "package_size": package_size,
        "package_unit": package_unit,
        "displayed_price": price,
        "is_variable_weight": False,
        "listed_unit_price": None,
        "listed_unit": None,
        "standardized_unit": standardized_unit,
        "price_per_standard_unit": price / package_size,
        "sale_status": "multi-buy" if multi_buy_unit_price else None,
        "multi_buy_quantity": multi_buy_quantity,
        "multi_buy_unit_price": multi_buy_unit_price,
        "single_item_price": single_item_price,
        "attributes": "",
        "data_quality_warning": None,
        "normalization_error": normalization_error,
    }


def _variable_price_row(
    store,
    group,
    listed_unit_price,
    *,
    listed_unit="kg",
    title=None,
):
    title = title or f"{store} {group} variable"
    return {
        "comparison_group": group,
        "store": store,
        "product_title": title,
        "product_url": f"https://example.test/{store}/{title}",
        "package_size_text": None,
        "package_size": None,
        "package_unit": None,
        "displayed_price": 12.00,
        "is_variable_weight": True,
        "listed_unit_price": listed_unit_price,
        "listed_unit": listed_unit,
        "standardized_unit": "100g",
        "price_per_standard_unit": listed_unit_price / 10,
        "sale_status": None,
        "multi_buy_quantity": None,
        "multi_buy_unit_price": None,
        "single_item_price": None,
        "attributes": "",
        "data_quality_warning": None,
        "normalization_error": None,
    }


class TestRealPriceOptimizer(unittest.TestCase):

    def test_actual_single_store_total_includes_overshoot_and_counts(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row(
                    "Store A",
                    "dry_pasta",
                    3.00,
                    package_size=500,
                    package_unit="g",
                ),
                _fixed_price_row(
                    "Store A",
                    "large_eggs",
                    4.00,
                    package_size=12,
                ),
            ]
        )

        result = optimize_real_price_basket(
            [
                _request("dry_pasta", 900, "g"),
                _request("large_eggs", 18, "count"),
            ],
            prices=prices,
            max_stores=1,
        )

        self.assertEqual(result["best_single"]["total_cost"], 14.00)
        pasta, eggs = result["best_single"]["shopping_plan"]
        self.assertEqual(pasta["package_count"], 2)
        self.assertEqual(pasta["fulfilled_quantity"], 1000)
        self.assertEqual(pasta["excess_quantity"], 100)
        self.assertEqual(pasta["estimated_item_cost"], 6.00)
        self.assertEqual(eggs["package_count"], 2)
        self.assertEqual(eggs["fulfilled_quantity"], 24)
        self.assertEqual(eggs["estimated_item_cost"], 8.00)

    def test_actual_two_store_total_and_dollar_savings(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row("Store A", "fresh_apples", 6.00),
                _fixed_price_row("Store A", "sliced_bread", 6.00),
                _fixed_price_row("Store B", "fresh_apples", 2.00),
            ]
        )

        result = optimize_real_price_basket(
            [_request("fresh_apples"), _request("sliced_bread")],
            prices=prices,
            savings_threshold=4.00,
        )

        self.assertEqual(result["best_single"]["total_cost"], 12.00)
        self.assertEqual(result["best_pair"]["total_cost"], 8.00)
        self.assertEqual(result["savings"], 4.00)
        self.assertTrue(result["worth_it"])
        self.assertEqual(result["recommended_option"], result["best_pair"])
        self.assertEqual(
            {item["store"] for item in result["best_pair"]["shopping_plan"]},
            {"Store A", "Store B"},
        )

    def test_threshold_requires_two_store_plan_to_qualify(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row("Store A", "fresh_apples", 6.00),
                _fixed_price_row("Store A", "sliced_bread", 6.00),
                _fixed_price_row("Store B", "fresh_apples", 2.00),
            ]
        )
        basket = [_request("fresh_apples"), _request("sliced_bread")]

        meets = optimize_real_price_basket(
            basket,
            prices=prices,
            savings_threshold=4.00,
        )
        misses = optimize_real_price_basket(
            basket,
            prices=prices,
            savings_threshold=4.01,
        )

        self.assertTrue(meets["worth_it"])
        self.assertEqual(meets["recommended_option"], meets["best_pair"])
        self.assertFalse(misses["worth_it"])
        self.assertEqual(
            misses["recommended_option"],
            misses["best_single"],
        )

    def test_equal_cost_two_store_plan_is_not_recommended(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row("Store A", "fresh_apples", 2.00),
                _fixed_price_row("Store B", "fresh_apples", 2.00),
                _fixed_price_row("Store B", "sliced_bread", 3.00),
            ]
        )

        result = optimize_real_price_basket(
            [_request("fresh_apples"), _request("sliced_bread")],
            prices=prices,
            savings_threshold=0.00,
        )

        self.assertEqual(result["best_single"]["total_cost"], 5.00)
        self.assertEqual(result["best_pair"]["total_cost"], 5.00)
        self.assertEqual(result["savings"], 0.00)
        self.assertFalse(result["worth_it"])
        self.assertEqual(result["recommended_option"], result["best_single"])

    def test_variable_weight_item_uses_requested_weight(self):
        prices = pd.DataFrame(
            [
                _variable_price_row(
                    "Store A",
                    "chicken_breast_boneless",
                    15.00,
                )
            ]
        )

        result = optimize_real_price_basket(
            [_request("chicken_breast_boneless", 1.5, "kg")],
            prices=prices,
            max_stores=1,
        )

        item = result["best_single"]["shopping_plan"][0]
        self.assertEqual(result["best_single"]["total_cost"], 22.50)
        self.assertIsNone(item["package_count"])
        self.assertEqual(item["effective_price"], 15.00)
        self.assertEqual(item["effective_price_unit"], "kg")

    def test_multi_buy_price_uses_calculated_package_count(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row(
                    "Store A",
                    "sliced_bread",
                    3.69,
                    package_size=675,
                    package_unit="g",
                    multi_buy_quantity=2,
                    multi_buy_unit_price=3.00,
                    single_item_price=3.69,
                )
            ]
        )

        result = optimize_real_price_basket(
            [_request("sliced_bread", 900, "g")],
            prices=prices,
            max_stores=1,
        )

        item = result["best_single"]["shopping_plan"][0]
        self.assertEqual(item["package_count"], 2)
        self.assertTrue(item["multi_buy_applied"])
        self.assertEqual(item["effective_package_price"], 3.00)
        self.assertEqual(item["estimated_item_cost"], 6.00)

    def test_count_request_can_mean_fixed_package_count(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row(
                    "Store A",
                    "sliced_bread",
                    2.48,
                    package_size=675,
                    package_unit="g",
                )
            ]
        )

        result = optimize_real_price_basket(
            [_request("sliced_bread", 1, "count")],
            prices=prices,
            max_stores=1,
        )

        item = result["best_single"]["shopping_plan"][0]
        self.assertEqual(item["package_count"], 1)
        self.assertEqual(item["fulfilled_quantity"], 1)
        self.assertEqual(item["effective_price_unit"], "package")
        self.assertEqual(item["estimated_item_cost"], 2.48)

    def test_sparse_coverage_allows_pair_only_plan(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row("Store A", "fresh_apples", 2.00),
                _fixed_price_row("Store B", "sliced_bread", 3.00),
            ]
        )

        result = optimize_real_price_basket(
            [_request("fresh_apples"), _request("sliced_bread")],
            prices=prices,
        )

        self.assertIsNone(result["best_single"])
        self.assertEqual(result["best_pair"]["total_cost"], 5.00)
        self.assertIsNone(result["savings"])
        self.assertEqual(result["recommended_option"], result["best_pair"])
        self.assertEqual(
            result["store_coverage"]["Store A"]["missing_groups"],
            ["sliced_bread"],
        )

    def test_no_genuine_two_store_plan_falls_back_to_single_store(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row("Store A", "fresh_apples", 2.00),
                _fixed_price_row("Store A", "sliced_bread", 3.00),
                _fixed_price_row("Store B", "fresh_apples", 4.00),
            ]
        )

        result = optimize_real_price_basket(
            [_request("fresh_apples"), _request("sliced_bread")],
            prices=prices,
        )

        self.assertIsNone(result["best_pair"])
        self.assertEqual(result["best_single"]["total_cost"], 5.00)
        self.assertEqual(result["recommended_option"], result["best_single"])

    def test_deterministic_ties_use_store_title_and_url(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row(
                    "Store A",
                    "fresh_apples",
                    2.00,
                    title="Zebra Apples",
                ),
                _fixed_price_row(
                    "Store A",
                    "fresh_apples",
                    2.00,
                    title="Alpha Apples",
                ),
                _fixed_price_row("Store A", "sliced_bread", 3.00),
                _fixed_price_row("Store B", "fresh_apples", 2.00),
                _fixed_price_row("Store B", "sliced_bread", 3.00),
            ]
        )

        result = optimize_real_price_basket(
            [_request("fresh_apples"), _request("sliced_bread")],
            prices=prices,
        )

        self.assertEqual(result["best_single"]["stores"], ("Store A",))
        self.assertEqual(
            result["best_single"]["shopping_plan"][0]["product_title"],
            "Alpha Apples",
        )

    def test_invalid_rows_and_other_groups_do_not_fill_coverage(self):
        prices = pd.DataFrame(
            [
                _fixed_price_row("Store A", "fresh_bananas", 2.00),
                _fixed_price_row("Store A", "cooking_bananas", 0.50),
                _fixed_price_row(
                    "Store A",
                    "fresh_bananas",
                    0.25,
                    title="Invalid row",
                    normalization_error="Invalid unit.",
                ),
            ]
        )

        result = optimize_real_price_basket(
            [_request("fresh_bananas")],
            prices=prices,
            max_stores=1,
        )

        item = result["best_single"]["shopping_plan"][0]
        self.assertEqual(item["comparison_group"], "fresh_bananas")
        self.assertEqual(item["estimated_item_cost"], 2.00)


if __name__ == "__main__":
    unittest.main()
