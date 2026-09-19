import pandas as pd


def _is_variable_weight(value):
    if pd.isna(value):
        return False

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {"true", "yes", "1"}:
            return True

        if normalized in {"false", "no", "0", ""}:
            return False

        raise ValueError(
            "is_variable_weight must be a boolean value."
        )

    return bool(value)


def _calculate_from_listed_unit_price(
    listed_unit_price,
    listed_unit,
    standardized_unit
):
    if pd.isna(listed_unit_price):
        raise ValueError(
            "Variable-weight products require a listed unit price."
        )

    if listed_unit_price < 0:
        raise ValueError("Listed unit price cannot be negative.")

    if pd.isna(listed_unit) or not str(listed_unit).strip():
        raise ValueError(
            "Variable-weight products require a listed unit."
        )

    listed_unit = str(listed_unit).lower().strip()
    standardized_unit = standardized_unit.lower().strip()

    # Accept unit labels captured either as "kg" or as displayed "$ / kg".
    if listed_unit.startswith("$"):
        listed_unit = listed_unit[1:].strip()

    if listed_unit.startswith("/"):
        listed_unit = listed_unit[1:].strip()

    if listed_unit == standardized_unit:
        return listed_unit_price

    return calculate_price_per_standard_unit(
        price=listed_unit_price,
        package_size=1,
        package_unit=listed_unit,
        standardized_unit=standardized_unit
    )


def calculate_price_per_standard_unit(
    price,
    package_size,
    package_unit,
    standardized_unit,
    is_variable_weight=False,
    listed_unit_price=None,
    listed_unit=None
):
    if _is_variable_weight(is_variable_weight):
        return _calculate_from_listed_unit_price(
            listed_unit_price,
            listed_unit,
            standardized_unit
        )

    has_package_data = (
        not pd.isna(price)
        and not pd.isna(package_size)
        and not pd.isna(package_unit)
        and bool(str(package_unit).strip())
    )

    if not has_package_data:
        has_listed_unit_price = (
            not pd.isna(listed_unit_price)
            and not pd.isna(listed_unit)
            and bool(str(listed_unit).strip())
        )

        if has_listed_unit_price:
            return _calculate_from_listed_unit_price(
                listed_unit_price,
                listed_unit,
                standardized_unit
            )

        raise ValueError(
            "Fixed-package products require price, package size, and "
            "package unit, or a listed unit price and unit."
        )

    if package_size <= 0:
        raise ValueError("Package size must be greater than zero.")

    if price < 0:
        raise ValueError("Price cannot be negative.")

    package_unit = package_unit.lower().strip()
    standardized_unit = standardized_unit.lower().strip()

    if package_unit == "g" and standardized_unit == "100g":
        return price / package_size * 100

    if package_unit == "kg" and standardized_unit == "100g":
        package_size_in_g = package_size * 1000
        return price / package_size_in_g * 100

    if package_unit in {"lb", "lbs"} and standardized_unit == "100g":
        package_size_in_g = package_size * 453.59237
        return price / package_size_in_g * 100

    if package_unit == "ml" and standardized_unit == "1l":
        package_size_in_l = package_size / 1000
        return price / package_size_in_l

    if package_unit == "l" and standardized_unit == "1l":
        return price / package_size

    if package_unit == "count" and standardized_unit == "1unit":
        return price / package_size

    raise ValueError(
        f"Unsupported unit combination: {package_unit} -> {standardized_unit}"
    )


def add_standardized_prices(prices):
    prices = prices.copy()

    prices["price_per_standard_unit"] = prices.apply(
        lambda row: calculate_price_per_standard_unit(
            row["price"],
            row["package_size"],
            row["package_unit"],
            row["standardized_unit"],
            row.get("is_variable_weight", False),
            row.get("listed_unit_price"),
            row.get("listed_unit")
        ),
        axis=1
    )

    return prices


def get_best_value_by_group(prices):
    required_columns = {
        "comparison_group",
        "price_per_standard_unit"
    }

    missing = required_columns - set(prices.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}"
        )

    best_value = (
        prices
        .sort_values(
            ["comparison_group", "price_per_standard_unit", "store"]
        )
        .groupby("comparison_group", as_index=False)
        .first()
    )

    return best_value
