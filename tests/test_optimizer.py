import unittest

import pandas as pd

from src.optimizer import optimize_basket, prepare_basket_data


class PriceValidationTests(unittest.TestCase):
    def setUp(self):
        self.basket = pd.DataFrame({"item": ["milk"], "quantity": [1]})

    def prices_with(self, value):
        return pd.DataFrame({
            "item": ["milk"],
            "store": ["Store A"],
            "price": [value],
        })

    def test_rejects_non_numeric_price(self):
        with self.assertRaisesRegex(ValueError, "must be numeric"):
            prepare_basket_data(self.prices_with("3.99"), self.basket)

    def test_rejects_null_price(self):
        with self.assertRaisesRegex(ValueError, "null price"):
            prepare_basket_data(self.prices_with(None), self.basket)

    def test_rejects_positive_infinity(self):
        with self.assertRaisesRegex(ValueError, "finite price"):
            prepare_basket_data(self.prices_with(float("inf")), self.basket)

    def test_rejects_negative_infinity(self):
        with self.assertRaisesRegex(ValueError, "finite price"):
            prepare_basket_data(self.prices_with(float("-inf")), self.basket)

    def test_rejects_negative_price(self):
        with self.assertRaisesRegex(ValueError, "negative price"):
            prepare_basket_data(self.prices_with(-0.01), self.basket)

    def test_accepts_zero_price(self):
        result = prepare_basket_data(self.prices_with(0.0), self.basket)

        self.assertEqual(result.loc[0, "total_item_cost"], 0.0)


class ExistingBehaviorTests(unittest.TestCase):
    def test_sample_basket_result_is_unchanged(self):
        prices = pd.read_csv("data/sample_prices.csv")
        basket = pd.read_csv("data/sample_basket.csv")

        result = optimize_basket(
            prices=prices,
            basket=basket,
            max_stores=2,
            savings_threshold=5.0,
        )

        self.assertEqual(result["recommended_option"]["stores"], ("Food Basics",))
        self.assertAlmostEqual(result["recommended_option"]["total_cost"], 53.37)
        self.assertAlmostEqual(result["savings"], 2.30)
        self.assertFalse(result["worth_it"])


class StoreCoverageTests(unittest.TestCase):
    def test_uses_two_stores_when_no_single_store_covers_basket(self):
        prices = pd.DataFrame({
            "item": ["milk", "bread"],
            "store": ["Store A", "Store B"],
            "price": [3.0, 2.0],
        })
        basket = pd.DataFrame({
            "item": ["milk", "bread"],
            "quantity": [1, 2],
        })

        result = optimize_basket(prices, basket, max_stores=2)

        self.assertIsNone(result["best_single"])
        self.assertEqual(result["best_multi"]["stores"], ("Store A", "Store B"))
        self.assertEqual(result["recommended_option"], result["best_multi"])
        self.assertAlmostEqual(result["best_multi"]["total_cost"], 7.0)
        self.assertIsNone(result["savings"])
        self.assertTrue(result["worth_it"])

    def test_falls_back_to_single_store_when_no_pair_exists(self):
        prices = pd.DataFrame({
            "item": ["milk", "bread"],
            "store": ["Store A", "Store A"],
            "price": [3.0, 2.0],
        })
        basket = pd.DataFrame({
            "item": ["milk", "bread"],
            "quantity": [1, 1],
        })

        result = optimize_basket(prices, basket, max_stores=2)

        self.assertEqual(result["best_single"]["stores"], ("Store A",))
        self.assertIsNone(result["best_multi"])
        self.assertEqual(result["recommended_option"], result["best_single"])
        self.assertEqual(result["savings"], 0.0)
        self.assertFalse(result["worth_it"])

    def test_raises_when_neither_one_nor_two_stores_cover_basket(self):
        prices = pd.DataFrame({
            "item": ["milk", "bread", "eggs"],
            "store": ["Store A", "Store B", "Store C"],
            "price": [3.0, 2.0, 4.0],
        })
        basket = pd.DataFrame({
            "item": ["milk", "bread", "eggs"],
            "quantity": [1, 1, 1],
        })

        with self.assertRaisesRegex(
            ValueError,
            "No one-store or two-store combination covers the full basket",
        ):
            optimize_basket(prices, basket, max_stores=2)


if __name__ == "__main__":
    unittest.main()
