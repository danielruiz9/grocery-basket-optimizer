def calculate_price_per_standard_unit(
    price,
    package_size,
    package_unit,
    standardized_unit
):
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
            row["standardized_unit"]
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