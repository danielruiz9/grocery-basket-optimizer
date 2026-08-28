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


if __name__ == "__main__":
    unittest.main()
