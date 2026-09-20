import unittest
from pathlib import Path

import pandas as pd

from src.classification import classify_product


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_PATHS = (
    PROJECT_ROOT / "data" / "walmart_prices_normalized.csv",
    PROJECT_ROOT / "data" / "nofrills_prices_normalized.csv",
    PROJECT_ROOT / "data" / "foodbasics_prices_normalized.csv",
    PROJECT_ROOT / "data" / "metro_prices_normalized.csv",
    PROJECT_ROOT / "data" / "all_stores_prices_normalized.csv",
    PROJECT_ROOT / "data" / "optimizer_ready_prices.csv",
)


class TestSnapshotConsistency(unittest.TestCase):

    def test_committed_groups_match_the_current_classifier(self):
        mismatches = []

        for path in NORMALIZED_PATHS:
            prices = pd.read_csv(path)

            for row in prices.itertuples(index=False):
                expected_group = classify_product(
                    row.product_title
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
