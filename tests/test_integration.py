import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.integration import (
    DEFAULT_INPUT_PATHS,
    SUPERSET_COLUMNS,
    integrate_price_files,
    integrate_price_frames,
    summarize_integration,
)


def _row(
    store,
    product_url,
    title,
    comparison_group,
    standardized_unit="100g",
    price=1.0,
    normalization_error=None,
    **extra,
):
    row = {
        "date_observed": "2026-09-19",
        "store": store,
        "product_url": product_url,
        "product_title": title,
        "comparison_group": comparison_group,
        "standardized_unit": standardized_unit,
        "price_per_standard_unit": price,
        "normalization_error": normalization_error,
    }
    row.update(extra)
    return row


class TestCrossStoreIntegration(unittest.TestCase):

    def test_aligns_deduplicates_and_filters_without_dropping_warnings(self):
        walmart = pd.DataFrame(
            [
                _row(
                    "Walmart Canada",
                    "https://example.test/walmart/apples",
                    "Gala Apples",
                    "fresh_apples",
                    price=0.50,
                ),
                _row(
                    "Walmart Canada",
                    "https://example.test/walmart/noise",
                    "Apple Cereal",
                    "unsupported",
                    price=0.80,
                ),
            ]
        )
        nofrills = pd.DataFrame(
            [
                _row(
                    "No Frills Canada",
                    "https://example.test/nofrills/apples",
                    "McIntosh Apples",
                    "fresh_apples",
                    price=0.45,
                    data_quality_warning="Review listed unit price.",
                ),
                _row(
                    "No Frills Canada",
                    "https://example.test/nofrills/apples",
                    "McIntosh Apples duplicate search result",
                    "fresh_apples",
                    price=0.45,
                    data_quality_warning="Review listed unit price.",
                ),
                _row(
                    "No Frills Canada",
                    "https://example.test/nofrills/bread",
                    "Bread with bad unit",
                    "sliced_bread",
                    price=0.60,
                    normalization_error="Incompatible comparison unit.",
                ),
            ]
        )
        foodbasics = pd.DataFrame(
            [
                _row(
                    "Food Basics Canada",
                    "https://example.test/foodbasics/apples",
                    "Paula Red Apples",
                    "fresh_apples",
                    price=0.44,
                    multi_buy_quantity=2,
                    multi_buy_unit_price=0.40,
                    single_item_price=0.44,
                ),
                _row(
                    "Food Basics Canada",
                    "https://example.test/foodbasics/missing-price",
                    "Apples without normalized price",
                    "fresh_apples",
                    price=None,
                ),
            ]
        )

        metro = pd.DataFrame([
            _row(
                "Metro Canada", "https://example.test/metro/apples",
                "Gala Apples", "fresh_apples", price=0.439,
                regular_price_unit="kg", regular_price_text="$6.59/kg",
                displayed_price_text="$0.68 avg. ea.",
            ),
        ])
        result = integrate_price_frames([walmart, nofrills, foodbasics, metro])

        self.assertEqual(result.duplicates_removed, 1)
        self.assertEqual(len(result.combined), 7)
        self.assertEqual(len(result.optimizer_ready), 4)
        self.assertEqual(
            result.optimizer_ready["store"].tolist(),
            [
                "Food Basics Canada",
                "Metro Canada",
                "No Frills Canada",
                "Walmart Canada",
            ],
        )
        self.assertTrue(
            set(SUPERSET_COLUMNS).issubset(result.combined.columns)
        )

        walmart_row = result.combined.loc[
            result.combined["store"].eq("Walmart Canada")
        ].iloc[0]
        self.assertTrue(pd.isna(walmart_row["data_quality_warning"]))
        self.assertTrue(pd.isna(walmart_row["multi_buy_quantity"]))

        warned = result.optimizer_ready.loc[
            result.optimizer_ready["store"].eq("No Frills Canada")
        ].iloc[0]
        self.assertEqual(
            warned["data_quality_warning"],
            "Review listed unit price.",
        )

        foodbasics_row = result.optimizer_ready.loc[
            result.optimizer_ready["store"].eq("Food Basics Canada")
        ].iloc[0]
        self.assertEqual(foodbasics_row["multi_buy_quantity"], 2)
        self.assertEqual(foodbasics_row["single_item_price"], 0.44)
        metro_row = result.optimizer_ready.loc[
            result.optimizer_ready["store"].eq("Metro Canada")
        ].iloc[0]
        self.assertEqual(metro_row["regular_price_unit"], "kg")
        self.assertEqual(metro_row["displayed_price_text"], "$0.68 avg. ea.")
        self.assertTrue(pd.isna(walmart_row["regular_price_unit"]))

        summary = summarize_integration(result)
        self.assertEqual(
            summary["groups_available_all_stores"],
            ["fresh_apples"],
        )
        self.assertEqual(summary["groups_missing_stores"], {})

    def test_preserves_rows_when_a_deduplication_key_is_incomplete(self):
        rows = pd.DataFrame(
            [
                _row(
                    "Walmart Canada",
                    None,
                    "First listing without URL",
                    "fresh_apples",
                ),
                _row(
                    "Walmart Canada",
                    None,
                    "Second listing without URL",
                    "fresh_apples",
                ),
            ]
        )

        result = integrate_price_frames([rows])

        self.assertEqual(result.duplicates_removed, 0)
        self.assertEqual(len(result.combined), 2)

    def test_raises_for_mixed_units_within_one_comparison_group(self):
        rows = pd.DataFrame(
            [
                _row(
                    "Walmart Canada",
                    "https://example.test/apples-by-weight",
                    "Apples by weight",
                    "fresh_apples",
                    standardized_unit="100g",
                ),
                _row(
                    "No Frills Canada",
                    "https://example.test/apples-by-count",
                    "Apples by count",
                    "fresh_apples",
                    standardized_unit="1unit",
                ),
            ]
        )

        with self.assertRaisesRegex(
            ValueError,
            "Mixed standardized units.*fresh_apples: 100g, 1unit",
        ):
            integrate_price_frames([rows])

    def test_default_inputs_include_all_four_stores(self):
        self.assertEqual([path.name for path in DEFAULT_INPUT_PATHS], [
            "walmart_prices_normalized.csv", "nofrills_prices_normalized.csv",
            "foodbasics_prices_normalized.csv", "metro_prices_normalized.csv",
        ])

    def test_loads_four_files_and_writes_both_outputs(self):
        stores = (
            "Walmart Canada",
            "No Frills Canada",
            "Food Basics Canada",
            "Metro Canada",
        )

        with tempfile.TemporaryDirectory() as temp_directory:
            directory = Path(temp_directory)
            input_paths = []

            for index, store in enumerate(stores):
                path = directory / f"store_{index}.csv"
                pd.DataFrame(
                    [
                        _row(
                            store,
                            f"https://example.test/{index}",
                            f"Store {index} Apples",
                            "fresh_apples",
                            price=0.40 + index / 10,
                        )
                    ]
                ).to_csv(path, index=False)
                input_paths.append(path)

            audit_path = directory / "audit.csv"
            optimizer_path = directory / "optimizer.csv"
            result = integrate_price_files(
                input_paths=input_paths,
                audit_output_path=audit_path,
                optimizer_output_path=optimizer_path,
            )

            audit = pd.read_csv(audit_path)
            optimizer = pd.read_csv(optimizer_path)

        self.assertEqual(len(result.combined), 4)
        self.assertEqual(len(audit), 4)
        self.assertEqual(len(optimizer), 4)
        self.assertEqual(list(audit.columns), list(SUPERSET_COLUMNS))


if __name__ == "__main__":
    unittest.main()
