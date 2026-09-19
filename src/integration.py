"""Combine normalized grocery listings into audit and optimizer datasets."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_PATHS = (
    PROJECT_ROOT / "data" / "walmart_prices_normalized.csv",
    PROJECT_ROOT / "data" / "nofrills_prices_normalized.csv",
    PROJECT_ROOT / "data" / "foodbasics_prices_normalized.csv",
)
DEFAULT_AUDIT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "all_stores_prices_normalized.csv"
)
DEFAULT_OPTIMIZER_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "optimizer_ready_prices.csv"
)

SUPERSET_COLUMNS = (
    "date_observed",
    "search_term",
    "store",
    "product_title",
    "product_url",
    "package_size_text",
    "package_size",
    "package_unit",
    "displayed_price",
    "unit_price_text",
    "listed_unit_price",
    "listed_unit",
    "is_variable_weight",
    "sale_status",
    "sale_price",
    "regular_price",
    "multi_buy_quantity",
    "multi_buy_unit_price",
    "single_item_price",
    "category",
    "product_family",
    "comparison_group",
    "product_form",
    "attributes",
    "standardized_unit",
    "price_per_standard_unit",
    "data_quality_warning",
    "normalization_error",
)
DEDUPLICATION_COLUMNS = (
    "store",
    "product_url",
    "date_observed",
)
SORT_COLUMNS = (
    "comparison_group",
    "store",
    "price_per_standard_unit",
    "product_title",
)
REQUIRED_INPUT_COLUMNS = set(DEDUPLICATION_COLUMNS) | set(SORT_COLUMNS) | {
    "normalization_error",
}


@dataclass(frozen=True)
class IntegrationResult:
    combined: pd.DataFrame
    optimizer_ready: pd.DataFrame
    duplicates_removed: int


def load_normalized_datasets(input_paths=DEFAULT_INPUT_PATHS):
    """Load the configured normalized store CSV files."""
    datasets = []

    for input_path in input_paths:
        path = Path(input_path)

        if not path.exists():
            raise FileNotFoundError(f"Normalized price file not found: {path}")

        datasets.append(pd.read_csv(path))

    return datasets


def _validate_input_columns(datasets):
    for index, dataset in enumerate(datasets):
        missing = REQUIRED_INPUT_COLUMNS - set(dataset.columns)

        if missing:
            raise ValueError(
                f"Dataset {index + 1} is missing required columns: "
                f"{sorted(missing)}"
            )


def _superset_column_order(datasets):
    columns = list(SUPERSET_COLUMNS)

    for dataset in datasets:
        for column in dataset.columns:
            if column not in columns:
                columns.append(column)

    return columns


def align_store_columns(datasets):
    """Return copies of store datasets aligned to one superset schema."""
    datasets = list(datasets)

    if not datasets:
        raise ValueError("At least one normalized dataset is required.")

    _validate_input_columns(datasets)
    columns = _superset_column_order(datasets)
    return [dataset.reindex(columns=columns) for dataset in datasets]


def _nonempty_key_mask(frame):
    key_values = frame.loc[:, DEDUPLICATION_COLUMNS]
    return key_values.apply(
        lambda column: (
            column.notna()
            & column.astype("string").str.strip().ne("")
        )
    ).all(axis=1)


def _deduplicate(frame):
    complete_keys = _nonempty_key_mask(frame)
    duplicate_rows = complete_keys & frame.duplicated(
        subset=list(DEDUPLICATION_COLUMNS),
        keep="first",
    )
    return frame.loc[~duplicate_rows].copy(), int(duplicate_rows.sum())


def _sort_prices(frame):
    return frame.sort_values(
        list(SORT_COLUMNS),
        kind="mergesort",
        na_position="last",
    ).reset_index(drop=True)


def validate_comparison_group_units(optimizer_ready):
    """Raise when a comparison group contains mixed standardized units."""
    mixed_groups = {}

    for comparison_group, rows in optimizer_ready.groupby(
        "comparison_group",
        dropna=False,
    ):
        units = {
            "<missing>"
            if pd.isna(unit) or not str(unit).strip()
            else str(unit).strip()
            for unit in rows["standardized_unit"]
        }

        if len(units) > 1:
            group_name = (
                "<missing>"
                if pd.isna(comparison_group)
                else str(comparison_group)
            )
            mixed_groups[group_name] = sorted(units)

    if mixed_groups:
        details = "; ".join(
            f"{group}: {', '.join(units)}"
            for group, units in sorted(mixed_groups.items())
        )
        raise ValueError(
            "Mixed standardized units found in optimizer-ready "
            f"comparison groups: {details}."
        )


def build_optimizer_ready(combined):
    """Filter the audit dataset to rows safe for price comparison."""
    empty_error = (
        combined["normalization_error"]
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("")
    )
    optimizer_ready = combined.loc[
        combined["comparison_group"].ne("unsupported")
        & empty_error
        & combined["price_per_standard_unit"].notna()
    ].copy()

    validate_comparison_group_units(optimizer_ready)
    return _sort_prices(optimizer_ready)


def integrate_price_frames(datasets):
    """Align, combine, deduplicate, validate, and filter store datasets."""
    aligned = align_store_columns(datasets)
    combined = pd.concat(aligned, ignore_index=True)
    combined, duplicates_removed = _deduplicate(combined)
    combined = _sort_prices(combined)
    optimizer_ready = build_optimizer_ready(combined)

    return IntegrationResult(
        combined=combined,
        optimizer_ready=optimizer_ready,
        duplicates_removed=duplicates_removed,
    )


def save_integration_outputs(
    result,
    audit_output_path=DEFAULT_AUDIT_OUTPUT_PATH,
    optimizer_output_path=DEFAULT_OPTIMIZER_OUTPUT_PATH,
):
    """Save the complete audit data and optimizer-ready subset."""
    audit_path = Path(audit_output_path)
    optimizer_path = Path(optimizer_output_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    optimizer_path.parent.mkdir(parents=True, exist_ok=True)
    result.combined.to_csv(audit_path, index=False)
    result.optimizer_ready.to_csv(optimizer_path, index=False)
    return audit_path, optimizer_path


def integrate_price_files(
    input_paths=DEFAULT_INPUT_PATHS,
    audit_output_path=DEFAULT_AUDIT_OUTPUT_PATH,
    optimizer_output_path=DEFAULT_OPTIMIZER_OUTPUT_PATH,
):
    """Load normalized files and write both integration outputs."""
    result = integrate_price_frames(load_normalized_datasets(input_paths))
    save_integration_outputs(
        result,
        audit_output_path=audit_output_path,
        optimizer_output_path=optimizer_output_path,
    )
    return result


def summarize_integration(result):
    """Return row counts and comparison-group coverage metrics."""
    combined = result.combined
    optimizer_ready = result.optimizer_ready
    stores = sorted(combined["store"].dropna().unique().tolist())
    group_stores = (
        optimizer_ready.groupby("comparison_group")["store"]
        .agg(lambda values: set(values.dropna()))
        .to_dict()
    )
    groups_all_stores = sorted(
        group
        for group, present_stores in group_stores.items()
        if present_stores == set(stores)
    )
    groups_missing_stores = {
        group: sorted(set(stores) - present_stores)
        for group, present_stores in sorted(group_stores.items())
        if present_stores != set(stores)
    }

    return {
        "total_rows_combined": len(combined),
        "rows_by_store": combined["store"].value_counts().sort_index().to_dict(),
        "duplicates_removed": result.duplicates_removed,
        "optimizer_ready_rows": len(optimizer_ready),
        "optimizer_ready_rows_by_store": (
            optimizer_ready["store"].value_counts().sort_index().to_dict()
        ),
        "groups_available_all_stores": groups_all_stores,
        "groups_missing_stores": groups_missing_stores,
    }


def main():
    result = integrate_price_files()
    summary = summarize_integration(result)

    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
