import unittest
from pathlib import Path

import pandas as pd

from src.classification import classify_product
from src.basket_costing import estimate_product_cost
from src.integration import integrate_price_frames, load_normalized_datasets
import math


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_PATHS = (
    PROJECT_ROOT / "data" / "walmart_prices_normalized.csv",
    PROJECT_ROOT / "data" / "nofrills_prices_normalized.csv",
    PROJECT_ROOT / "data" / "foodbasics_prices_normalized.csv",
    PROJECT_ROOT / "data" / "metro_prices_normalized.csv",
    PROJECT_ROOT / "data" / "loblaws_prices_normalized.csv",
    PROJECT_ROOT / "data" / "sobeys_prices_normalized.csv",
    PROJECT_ROOT / "data" / "all_stores_prices_normalized.csv",
    PROJECT_ROOT / "data" / "optimizer_ready_prices.csv",
)


class TestSnapshotConsistency(unittest.TestCase):

    def test_production_outputs_match_store_inputs_and_are_costable(self):
        result = integrate_price_frames(load_normalized_datasets())
        audit = pd.read_csv(PROJECT_ROOT / "data/all_stores_prices_normalized.csv")
        ready = pd.read_csv(PROJECT_ROOT / "data/optimizer_ready_prices.csv")
        # Compare serialized values too, not only matching row counts.
        from io import StringIO
        pd.testing.assert_frame_equal(audit, pd.read_csv(StringIO(result.combined.to_csv(index=False))))
        pd.testing.assert_frame_equal(ready, pd.read_csv(StringIO(result.optimizer_ready.to_csv(index=False))))
        self.assertEqual(set(audit.date_observed), {"2026-10-26"})
        self.assertEqual(len(audit), 484)
        self.assertEqual(len(ready), 328)
        self.assertFalse(ready.availability.eq("out_of_stock").any())
        for _, row in ready.iterrows():
            for quantity in (0.1, 1, 10):
                plan = estimate_product_cost(row, quantity, row.standardized_unit)
                self.assertTrue(math.isfinite(plan["estimated_item_cost"]))
                self.assertGreaterEqual(plan["estimated_item_cost"], 0)
                if row.is_variable_weight:
                    doubled = estimate_product_cost(row, quantity * 2, row.standardized_unit)
                    self.assertAlmostEqual(doubled["estimated_item_cost"], 2 * plan["estimated_item_cost"])

    def test_walmart_supported_but_unfulfillable_rows_are_auditable(self):
        audit = pd.read_csv(PROJECT_ROOT / "data/all_stores_prices_normalized.csv")
        ready = pd.read_csv(PROJECT_ROOT / "data/optimizer_ready_prices.csv")
        excluded = audit.loc[audit.store.eq("Walmart Canada") & audit.costing_error.notna()]
        self.assertEqual(len(excluded), 9)
        self.assertTrue(excluded.comparison_group.ne("unsupported").all())
        self.assertTrue(excluded.costing_error.eq("package_size is required.").all())
        self.assertFalse(ready.product_url.isin(excluded.product_url).any())
        for _, row in excluded.iterrows():
            with self.assertRaisesRegex(ValueError, "package_size"):
                estimate_product_cost(row, 1, row.standardized_unit)

    def test_committed_groups_match_the_current_classifier(self):
        mismatches = []

        for path in NORMALIZED_PATHS:
            prices = pd.read_csv(path)

            for row in prices.itertuples(index=False):
                expected_group = classify_product(
                    row.product_title, row.product_url
                )["comparison_group"]

                if row.comparison_group != expected_group:
                    mismatches.append(
                        (
                            path.name,
                            row.product_title,
                            row.comparison_group,
                            expected_group,
                        )
                    )

        self.assertEqual(mismatches, [])

    def test_optimizer_snapshot_excludes_unsupported_rows(self):
        prices = pd.read_csv(
            PROJECT_ROOT / "data" / "optimizer_ready_prices.csv"
        )

        self.assertNotIn(
            "unsupported",
            set(prices["comparison_group"]),
        )


if __name__ == "__main__":
    unittest.main()
