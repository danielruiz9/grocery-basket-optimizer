import unittest

import pandas as pd

from src.real_price_optimizer import optimize_real_price_basket


def _price_row(
    store,
    group,
    price,
    title=None,
    normalization_error=None,
):
    title = title or f"{store} {group}"
    return {
        "comparison_group": group,
        "store": store,
        "product_title": title,
        "product_url": f"https://example.test/{store}/{title}",
        "package_size_text": "1 package",
        "displayed_price": price * 10 if price is not None else None,
        "standardized_unit": "100g",
        "price_per_standard_unit": price,
        "sale_status": None,
        "attributes": "",
        "normalization_error": normalization_error,
    }


class TestRealPriceOptimizer(unittest.TestCase):

    def test_complete_single_store_coverage(self):
        prices = pd.DataFrame(
            [
                _price_row("Store A", "fresh_apples", 1.0),
                _price_row("Store A", "sliced_bread", 2.0),
                _price_row("Store B", "fresh_apples", 0.8),
            ]
        )

        result = optimize_real_price_basket(
            ["fresh_apples", "sliced_bread"],
            prices=prices,
        )

        self.assertEqual(result["best_single"]["stores"], ("Store A",))
        self.assertEqual(
            result["best_single"]["total_normalized_price"],
            3.0,
        )
        self.assertEqual(len(result["best_single"]["shopping_plan"]), 2)

    def test_reports_missing_group_at_one_store(self):
        prices = pd.DataFrame(
            [
                _price_row("Store A", "fresh_apples", 1.0),
                _price_row("Store A", "sliced_bread", 2.0),
                _price_row("Store B", "fresh_apples", 0.8),
            ]
        )

        result = optimize_real_price_basket(
            ["fresh_apples", "sliced_bread"],
            prices=prices,
        )

        coverage = result["store_coverage"]["Store B"]
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["missing_groups"], ["sliced_bread"])
        self.assertIsNone(coverage["total_normalized_price"])

    def test_uses_valid_two_store_combination(self):
        prices = pd.DataFrame(
            [
                _price_row("Store A", "fresh_apples", 1.0),
                _price_row("Store B", "sliced_bread", 2.0),
            ]
        )

        result = optimize_real_price_basket(
            ["fresh_apples", "sliced_bread"],
            prices=prices,
        )

        self.assertIsNone(result["best_single"])
        self.assertEqual(result["best_pair"]["stores"], ("Store A", "Store B"))
        self.assertEqual(result["best_pair"]["total_normalized_price"], 3.0)
        self.assertEqual(result["recommended_option"], result["best_pair"])
        self.assertIsNone(result["savings"])

    def test_two_store_option_unavailable_falls_back_to_single_store(self):
        prices = pd.DataFrame(
            [
                _price_row("Store A", "fresh_apples", 1.0),
                _price_row("Store A", "sliced_bread", 2.0),
                _price_row("Store B", "fresh_apples", 3.0),
                _price_row("Store B", "sliced_bread", 4.0),
            ]
        )

        result = optimize_real_price_basket(
            ["fresh_apples", "sliced_bread"],
            prices=prices,
        )

        self.assertIsNone(result["best_pair"])
        self.assertEqual(result["recommended_option"], result["best_single"])
        self.assertFalse(result["worth_it"])

    def test_threshold_matches_existing_meet_or_exceed_behavior(self):
        prices = pd.DataFrame(
            [
                _price_row("Store A", "fresh_apples", 5.0),
                _price_row("Store A", "sliced_bread", 5.0),
                _price_row("Store B", "fresh_apples", 1.0),
                _price_row("Store B", "sliced_bread", 10.0),
            ]
        )

        meets = optimize_real_price_basket(
            ["fresh_apples", "sliced_bread"],
            prices=prices,
            savings_threshold=4.0,
        )
        misses = optimize_real_price_basket(
            ["fresh_apples", "sliced_bread"],
            prices=prices,
            savings_threshold=4.01,
        )

        self.assertEqual(meets["savings"], 4.0)
        self.assertTrue(meets["worth_it"])
        self.assertEqual(meets["recommended_option"], meets["best_pair"])
        self.assertFalse(misses["worth_it"])
        self.assertEqual(misses["recommended_option"], misses["best_single"])

    def test_deterministic_tie_breaking(self):
        prices = pd.DataFrame(
            [
                _price_row(
                    "Store A",
                    "fresh_apples",
                    1.0,
                    title="Zebra Apples",
                ),
                _price_row(
                    "Store A",
                    "fresh_apples",
                    1.0,
                    title="Alpha Apples",
                ),
                _price_row("Store A", "sliced_bread", 2.0),
                _price_row("Store B", "fresh_apples", 1.0),
                _price_row("Store B", "sliced_bread", 2.0),
            ]
        )

        result = optimize_real_price_basket(
            ["fresh_apples", "sliced_bread"],
            prices=prices,
        )

        self.assertEqual(result["best_single"]["stores"], ("Store A",))
        self.assertEqual(
            result["best_single"]["shopping_plan"][0]["product_title"],
            "Alpha Apples",
        )

    def test_filters_invalid_rows_and_respects_exact_group_boundaries(self):
        prices = pd.DataFrame(
            [
                _price_row("Store A", "fresh_bananas", 1.0),
                _price_row("Store A", "cooking_bananas", 0.1),
                _price_row(
                    "Store A",
                    "fresh_bananas",
                    0.2,
                    title="Invalid normalized row",
                    normalization_error="Invalid unit.",
                ),
                _price_row("Store A", "unsupported", 0.05),
                _price_row("Store A", "fresh_bananas", None),
            ]
        )

        result = optimize_real_price_basket(
            ["fresh_bananas"],
            prices=prices,
            max_stores=1,
        )

        selected = result["best_single"]["shopping_plan"][0]
        self.assertEqual(selected["comparison_group"], "fresh_bananas")
        self.assertEqual(selected["price_per_standard_unit"], 1.0)


if __name__ == "__main__":
    unittest.main()
