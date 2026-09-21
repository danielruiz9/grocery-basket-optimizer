from pathlib import Path

import pandas as pd
import streamlit as st

from src.real_price_optimizer import optimize_real_price_basket


PRICES_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "optimizer_ready_prices.csv"
)

GROUP_LABELS = {
    "artisan_bread": "Artisan bread",
    "cheddar_cheese": "Cheddar cheese",
    "cheese_unspecified_type": "Other cheese",
    "chicken_breast": "Chicken breast",
    "chicken_breast_boneless": "Boneless chicken breast",
    "chicken_breast_diced": "Diced chicken breast",
    "chicken_breast_strips": "Chicken breast strips",
    "cottage_cheese": "Cottage cheese",
    "dry_pasta": "Dry pasta",
    "extra_large_eggs": "Extra-large eggs",
    "fresh_apples": "Fresh apples",
    "fresh_bananas": "Fresh bananas",
    "fresh_plantains": "Fresh plantains",
    "large_eggs": "Large eggs",
    "marble_cheese": "Marble cheese",
    "medium_eggs": "Medium eggs",
    "mozzarella_cheese": "Mozzarella cheese",
    "processed_cheese": "Processed cheese",
    "sliced_bread": "Sliced bread",
    "swiss_cheese": "Swiss cheese",
}

STORE_LABELS = {
    "Food Basics Canada": "Food Basics",
    "Loblaws Canada": "Loblaws",
    "Metro Canada": "Metro",
    "No Frills Canada": "No Frills",
    "Sobeys Canada": "Sobeys",
    "Walmart Canada": "Walmart",
}

LOAF_GROUPS = {
    "artisan_bread",
    "sliced_bread",
}

EGG_GROUPS = {
    "extra_large_eggs",
    "large_eggs",
    "medium_eggs",
}

DEFAULT_GROUPS = (
    "chicken_breast_boneless",
    "large_eggs",
    "fresh_apples",
    "dry_pasta",
    "sliced_bread",
)

DEFAULT_WEIGHT_GRAMS = {
    "chicken_breast": 1000.0,
    "chicken_breast_boneless": 1500.0,
    "chicken_breast_diced": 500.0,
    "chicken_breast_strips": 500.0,
    "fresh_apples": 1000.0,
    "fresh_bananas": 1000.0,
    "fresh_plantains": 1000.0,
    "dry_pasta": 900.0,
    "cheddar_cheese": 400.0,
    "cheese_unspecified_type": 400.0,
    "cottage_cheese": 500.0,
    "marble_cheese": 400.0,
    "mozzarella_cheese": 400.0,
    "processed_cheese": 400.0,
    "swiss_cheese": 400.0,
}


@st.cache_data
def load_prices(path):
    return pd.read_csv(path)


def group_label(comparison_group):
    return GROUP_LABELS.get(
        comparison_group,
        comparison_group.replace("_", " ").capitalize(),
    )


def store_label(store):
    return STORE_LABELS.get(store, store)


def request_kind(comparison_group, prices):
    group_rows = prices.loc[
        prices["comparison_group"].eq(comparison_group)
    ]
    product_families = set(
        group_rows["product_family"].dropna().astype(str)
    )
    standardized_units = set(
        group_rows["standardized_unit"].dropna().astype(str)
    )

    if "bread" in product_families:
        return "packages"

    if product_families == {"eggs"}:
        return "count"

    if standardized_units == {"100g"}:
        return "weight"

    if standardized_units == {"1L"}:
        return "volume"

    if standardized_units == {"1unit"}:
        return "count"

    return None


def default_weight_unit(comparison_group):
    if comparison_group == "dry_pasta" or "cheese" in comparison_group:
        return "g"

    return "kg"


def default_weight_quantity(comparison_group, unit):
    grams = DEFAULT_WEIGHT_GRAMS.get(comparison_group, 1000.0)
    return grams if unit == "g" else grams / 1000.0


def format_quantity(quantity, unit):
    number = float(quantity)
    displayed_number = (
        str(int(number))
        if number.is_integer()
        else f"{number:g}"
    )
    return f"{displayed_number} {unit}"


def format_requested_quantity(comparison_group, quantity, unit):
    number = float(quantity)
    displayed_number = (
        str(int(number))
        if number.is_integer()
        else f"{number:g}"
    )

    if comparison_group in LOAF_GROUPS:
        noun = "loaf" if number == 1 else "loaves"
        return f"{displayed_number} {noun}"

    if comparison_group in EGG_GROUPS:
        noun = "egg" if number == 1 else "eggs"
        return f"{displayed_number} {noun}"

    return format_quantity(quantity, unit)


def format_currency(value):
    return "Unavailable" if value is None else f"${value:.2f}"


def format_markdown_currency(value):
    return f"\\${value:.2f}"


def package_size_display(item):
    if item.get("package_size_text"):
        return item["package_size_text"]

    if item.get("package_size") is not None and item.get("package_unit"):
        return format_quantity(
            item["package_size"],
            item["package_unit"],
        )

    if item.get("costing_method") == "variable_weight":
        return "Variable weight"

    return "—"


def sale_status_display(item):
    sale_status = item.get("sale_status")
    status = (
        str(sale_status).replace("_", " ").title()
        if sale_status
        else "—"
    )

    if item.get("multi_buy_applied"):
        return f"{status} (multi-buy applied)"

    return status


def package_fulfillment_display(item):
    package_count = item.get("package_count")
    fulfilled = format_requested_quantity(
        item["comparison_group"],
        item["fulfilled_quantity"],
        item["fulfilled_unit"],
    )

    if package_count is None:
        return f"Variable weight · {fulfilled}"

    if item["comparison_group"] in LOAF_GROUPS:
        noun = "loaf" if package_count == 1 else "loaves"
        return f"{int(package_count)} {noun}"

    package_noun = "package" if package_count == 1 else "packages"
    return (
        f"{int(package_count)} {package_noun} · "
        f"{fulfilled} fulfilled"
    )


def recommendation_message(result, savings_threshold, max_stores):
    best_single = result["best_single"]
    best_pair = result["best_pair"]
    savings = result["savings"]

    if best_single is not None and best_pair is not None:
        single_store = store_label(best_single["stores"][0])
        pair_stores = " and ".join(
            store_label(store) for store in best_pair["stores"]
        )

        if result["worth_it"]:
            return (
                f"Split between {pair_stores} — estimated total "
                f"{format_markdown_currency(best_pair['total_cost'])}, "
                f"saving {format_markdown_currency(savings)} versus the "
                "best single-store option and meeting your "
                f"{format_markdown_currency(savings_threshold)} threshold."
            )

        if savings > 0:
            return (
                f"Shop at {single_store} — estimated basket total "
                f"{format_markdown_currency(best_single['total_cost'])}. "
                f"Splitting between {pair_stores} would cost "
                f"{format_markdown_currency(best_pair['total_cost'])}, "
                f"saving {format_markdown_currency(savings)}, which is "
                f"below your {format_markdown_currency(savings_threshold)} "
                "threshold."
            )

        return (
            f"Shop at {single_store} — estimated basket total "
            f"{format_markdown_currency(best_single['total_cost'])}. "
            f"The two-store option would cost "
            f"{format_markdown_currency(best_pair['total_cost'])} and "
            "would not lower the total."
        )

    if best_pair is not None:
        pair_stores = " and ".join(
            store_label(store) for store in best_pair["stores"]
        )
        return (
            f"Split between {pair_stores} — estimated total "
            f"{format_markdown_currency(best_pair['total_cost'])}. "
            "No single store can fulfill the complete basket."
        )

    single_store = store_label(best_single["stores"][0])
    message = (
        f"Shop at {single_store} — estimated basket total "
        f"{format_markdown_currency(best_single['total_cost'])}."
    )

    if max_stores == 2:
        message += " No valid two-store split is available."

    return message


prices = load_prices(PRICES_PATH)

latest_date = pd.to_datetime(
    prices["date_observed"],
    errors="coerce",
).max()

supported_groups = sorted(
    (
        group
        for group in prices["comparison_group"].dropna().unique()
        if request_kind(group, prices) is not None
    ),
    key=group_label,
)

default_groups = [
    group for group in DEFAULT_GROUPS if group in supported_groups
]

st.set_page_config(
    page_title="Grocery Basket Optimizer",
    page_icon="🛒",
    layout="wide",
)

st.title("🛒 Grocery Basket Optimizer")

st.write(
    "Build a grocery basket and compare estimated checkout costs across "
    "Walmart, No Frills, Food Basics, Metro, Loblaws, and Sobeys."
)

if pd.notna(latest_date):
    st.caption(f"Prices last updated: {latest_date:%Y-%m-%d}")

st.caption(
    "Prices come from the latest collected store snapshot and may differ "
    "from checkout prices. Variable-weight item costs are estimates."
)

st.divider()

st.header("Build Your Basket")

st.caption(
    "Choose groceries, then enter how much you need. Fixed packages are "
    "rounded up to whole packages; variable-weight items are estimated "
    "from the requested weight."
)

selected_groups = st.multiselect(
    "Select the grocery items you want to buy:",
    options=supported_groups,
    default=default_groups,
    format_func=group_label,
)

basket = []

for comparison_group in selected_groups:
    label = group_label(comparison_group)
    kind = request_kind(comparison_group, prices)

    with st.container(border=True):
        st.markdown(f"**{label}**")

        if kind == "weight":
            quantity_column, unit_column = st.columns([2, 1])
            default_unit = default_weight_unit(comparison_group)

            with unit_column:
                unit = st.selectbox(
                    "Unit",
                    options=["g", "kg"],
                    index=0 if default_unit == "g" else 1,
                    key=f"unit_{comparison_group}",
                )

            with quantity_column:
                if unit == "g":
                    quantity = st.number_input(
                        "Quantity",
                        min_value=50.0,
                        value=default_weight_quantity(
                            comparison_group,
                            unit,
                        ),
                        step=50.0,
                        format="%.0f",
                        key=f"quantity_{comparison_group}_{unit}",
                    )
                else:
                    quantity = st.number_input(
                        "Quantity",
                        min_value=0.1,
                        value=default_weight_quantity(
                            comparison_group,
                            unit,
                        ),
                        step=0.1,
                        format="%.1f",
                        key=f"quantity_{comparison_group}_{unit}",
                    )

        elif kind == "volume":
            quantity_column, unit_column = st.columns([2, 1])

            with unit_column:
                unit = st.selectbox(
                    "Unit",
                    options=["mL", "L"],
                    index=1,
                    key=f"unit_{comparison_group}",
                )

            with quantity_column:
                quantity = st.number_input(
                    "Quantity",
                    min_value=50.0 if unit == "mL" else 0.1,
                    value=1000.0 if unit == "mL" else 1.0,
                    step=50.0 if unit == "mL" else 0.1,
                    format="%.0f" if unit == "mL" else "%.1f",
                    key=f"quantity_{comparison_group}_{unit}",
                )

        elif kind == "packages":
            quantity = st.number_input(
                "Number of loaves",
                min_value=1,
                value=1,
                step=1,
                key=f"quantity_{comparison_group}",
            )
            unit = "count"

        else:
            quantity = st.number_input(
                "Number of individual items",
                min_value=1,
                value=18,
                step=1,
                key=f"quantity_{comparison_group}",
            )
            unit = "count"

        basket.append(
            {
                "comparison_group": comparison_group,
                "quantity": quantity,
                "unit": unit,
            }
        )

if basket:
    st.caption(f"{len(basket)} grocery items selected")

    with st.expander("View requested basket"):
        basket_display = pd.DataFrame(
            {
                "Grocery item": [
                    group_label(item["comparison_group"])
                    for item in basket
                ],
                "Requested quantity": [
                    format_requested_quantity(
                        item["comparison_group"],
                        item["quantity"],
                        item["unit"],
                    )
                    for item in basket
                ],
            }
        )
        st.dataframe(
            basket_display,
            width="stretch",
            hide_index=True,
        )

with st.sidebar:
    st.header("Shopping Preferences")

    max_stores = st.radio(
        "Maximum number of stores:",
        options=[1, 2],
        format_func=lambda value: (
            "One store" if value == 1 else "Up to two stores"
        ),
    )

    savings_threshold = 5.0

    if max_stores == 2:
        savings_threshold = st.number_input(
            "Minimum savings needed to justify an extra store:",
            min_value=0.0,
            value=5.0,
            step=0.5,
            format="%.2f",
        )

        st.caption(
            "A second store is recommended only when its estimated dollar "
            "savings meet this threshold."
        )

if st.button(
    "Optimize Basket",
    type="primary",
    width="stretch",
):
    if not basket:
        st.warning("Please select at least one grocery item.")
    else:
        try:
            result = optimize_real_price_basket(
                basket=basket,
                prices=prices,
                max_stores=max_stores,
                savings_threshold=savings_threshold,
            )

            st.divider()
            st.subheader("Recommendation")

            best_single = result["best_single"]
            best_pair = result["best_pair"]
            savings = result["savings"]

            if best_single is not None and best_pair is not None:
                single_column, pair_column, savings_column = st.columns(3)

                single_column.metric(
                    "Best single-store estimated total",
                    format_currency(best_single["total_cost"]),
                )
                single_column.caption(
                    store_label(best_single["stores"][0])
                )

                pair_column.metric(
                    "Best two-store estimated total",
                    format_currency(best_pair["total_cost"]),
                )
                pair_column.caption(
                    " + ".join(
                        store_label(store) for store in best_pair["stores"]
                    )
                )

                savings_column.metric(
                    "Dollar savings",
                    format_currency(savings),
                )
                savings_column.caption("Compared with the best single store")

            elif best_pair is not None:
                st.metric(
                    "Best two-store estimated total",
                    format_currency(best_pair["total_cost"]),
                )
                st.caption(
                    " + ".join(
                        store_label(store) for store in best_pair["stores"]
                    )
                )

            else:
                st.metric(
                    "Best single-store estimated total",
                    format_currency(best_single["total_cost"]),
                )
                st.caption(store_label(best_single["stores"][0]))

            st.success(
                recommendation_message(
                    result,
                    savings_threshold,
                    max_stores,
                )
            )

            if best_single is not None and best_pair is not None:
                st.subheader("Option Comparison")

                comparison = pd.DataFrame(
                    {
                        "Option": [
                            "Best single store",
                            "Best two-store combination",
                        ],
                        "Store(s)": [
                            store_label(best_single["stores"][0]),
                            " + ".join(
                                store_label(store)
                                for store in best_pair["stores"]
                            ),
                        ],
                        "Estimated total": [
                            best_single["total_cost"],
                            best_pair["total_cost"],
                        ],
                    }
                )

                st.dataframe(
                    comparison,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Estimated total": st.column_config.NumberColumn(
                            format="$%.2f"
                        )
                    },
                )

            st.subheader("Shopping Plan")

            recommended = result["recommended_option"]
            shopping_plan_rows = []
            shopping_detail_rows = []

            for item in recommended["shopping_plan"]:
                shopping_plan_rows.append(
                    {
                        "Grocery item": group_label(
                            item["comparison_group"]
                        ),
                        "Store": store_label(item["store"]),
                        "Product": item.get("product_title") or "—",
                        "Quantity": format_requested_quantity(
                            item["comparison_group"],
                            item["requested_quantity"],
                            item["requested_unit"],
                        ),
                        "Packages / fulfillment": (
                            package_fulfillment_display(item)
                        ),
                        "Estimated cost": item["estimated_item_cost"],
                    }
                )
                shopping_detail_rows.append(
                    {
                        "Grocery item": group_label(
                            item["comparison_group"]
                        ),
                        "Package size": package_size_display(item),
                        "Sale status": sale_status_display(item),
                        "Product link": item.get("product_url"),
                    }
                )

            shopping_plan = pd.DataFrame(shopping_plan_rows).sort_values(
                ["Store", "Grocery item"]
            )

            st.dataframe(
                shopping_plan,
                width="stretch",
                hide_index=True,
                column_config={
                    "Grocery item": st.column_config.TextColumn(
                        width=180
                    ),
                    "Store": st.column_config.TextColumn(width=100),
                    "Product": st.column_config.TextColumn(width=280),
                    "Quantity": st.column_config.TextColumn(width=90),
                    "Packages / fulfillment": st.column_config.TextColumn(
                        width=200
                    ),
                    "Estimated cost": st.column_config.NumberColumn(
                        format="$%.2f",
                        width=110,
                    ),
                },
            )

            with st.expander("Product details and links"):
                shopping_details = pd.DataFrame(shopping_detail_rows)
                st.dataframe(
                    shopping_details,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Grocery item": st.column_config.TextColumn(
                            width="medium"
                        ),
                        "Package size": st.column_config.TextColumn(
                            width="small"
                        ),
                        "Sale status": st.column_config.TextColumn(
                            width="small"
                        ),
                        "Product link": st.column_config.LinkColumn(
                            "Product link",
                            display_text="View product",
                            width="small",
                        ),
                    },
                )

            incomplete_stores = {
                store: option["missing_groups"]
                for store, option in result["store_coverage"].items()
                if not option["complete"]
            }

            if incomplete_stores:
                with st.expander("Store coverage"):
                    st.caption(
                        "These stores could not fulfill every requested "
                        "item with a compatible listing."
                    )
                    coverage = pd.DataFrame(
                        {
                            "Store": [
                                store_label(store)
                                for store in incomplete_stores
                            ],
                            "Missing grocery items": [
                                ", ".join(
                                    group_label(group)
                                    for group in missing_groups
                                )
                                for missing_groups in incomplete_stores.values()
                            ],
                        }
                    )
                    st.dataframe(
                        coverage,
                        width="stretch",
                        hide_index=True,
                    )

        except ValueError as error:
            st.error(str(error))
