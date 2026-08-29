from pathlib import Path

import pandas as pd
import streamlit as st

from src.optimizer import optimize_basket


prices_path = Path(__file__).resolve().parent / "data" / "sample_prices.csv"
prices = pd.read_csv(prices_path)

st.set_page_config(
    page_title="Grocery Basket Optimizer",
    page_icon="🛒",
    layout="centered"
)

st.title("🛒 Grocery Basket Optimizer")

st.write(
    "Build your grocery basket, set your shopping preferences, "
    "and compare whether one or two stores gives you the better deal."
)

st.caption(
    "Demo using static, illustrative sample data for 15 items across "
    "three stores. Prices are not live."
)

st.divider()

st.header("Build Your Basket")

st.caption(
    "Quantity represents the number of packages or units of the listed "
    "sample product and multiplies its listed price."
)

available_items = sorted(prices["item"].unique())

selected_items = st.multiselect(
    "Select the grocery items you want to buy:",
    options=available_items
)

basket_rows = []

for item in selected_items:
    quantity = st.number_input(
        f"Quantity for {item.title()}:",
        min_value=1,
        value=1,
        step=1,
        key=f"quantity_{item}"
    )

    basket_rows.append({
        "item": item,
        "quantity": quantity
    })

basket = pd.DataFrame(basket_rows)

if not basket.empty:
    total_units = int(basket["quantity"].sum())

    st.caption(
        f"{len(basket)} different items selected • "
        f"{total_units} total units"
    )

    with st.expander("View basket details"):
        basket_display = basket.copy()
        basket_display["item"] = basket_display["item"].str.title()

        st.dataframe(
            basket_display,
            use_container_width=True,
            hide_index=True
        )

with st.sidebar:
    st.header("Shopping Preferences")

    max_stores = st.radio(
        "Maximum number of stores:",
        options=[1, 2],
        format_func=lambda value: (
            "One store" if value == 1 else "Up to two stores"
        )
    )

    savings_threshold = 5.0

    if max_stores == 2:
        savings_threshold = st.number_input(
            "Minimum savings needed to justify an extra store:",
            min_value=0.0,
            value=5.0,
            step=0.5,
            format="%.2f"
        )

        st.caption(
            "The optimizer will only recommend another store if "
            "the extra savings meet your threshold."
        )

if st.button(
    "Optimize Basket",
    type="primary",
    use_container_width=True
):
    if basket.empty:
        st.warning("Please select at least one grocery item.")
    else:
        try:
            result = optimize_basket(
                prices=prices,
                basket=basket,
                max_stores=max_stores,
                savings_threshold=savings_threshold
            )

            st.divider()
            st.subheader("Recommendation")

            recommended = result["recommended_option"]

            recommended_stores = " + ".join(recommended["stores"])
            recommended_cost = recommended["total_cost"]

            col1, col2, col3 = st.columns([2, 1, 1])

            col1.metric(
                "Recommended Store Plan",
                recommended_stores
            )

            col2.metric(
                "Estimated Basket Cost",
                f"${recommended_cost:.2f}"
            )

            if max_stores == 1:
                if result["second_best_single"] is not None:
                    second_best = result["second_best_single"]

                    single_store_savings = (
                        second_best["total_cost"]
                        - result["best_single"]["total_cost"]
                    )

                    next_best_store = second_best["stores"][0]

                    col3.metric(
                        f"Savings vs {next_best_store}",
                        f"${single_store_savings:.2f}"
                    )
                else:
                    col3.metric(
                        "Alternative Single Store",
                        "Unavailable"
                    )
            elif result["best_single"] is None:
                col3.metric(
                    "Single-Store Option",
                    "Unavailable"
                )
            elif result["best_multi"] is None:
                col3.metric(
                    "Two-Store Option",
                    "Unavailable"
                )
            else:
                col3.metric(
                    "Savings vs Best Single Store",
                     f"${result['savings']:.2f}"
                )

            if (
                result["best_single"] is not None
                and result["best_multi"] is not None
            ):
                savings = result["savings"]

                if result["worth_it"]:
                    decision_message = (
                        f"The two-store option saves \\${savings:.2f} versus "
                        f"the best single store, meeting your "
                        f"\\${savings_threshold:.2f} threshold. "
                        f"Recommendation: shop at {recommended_stores}."
                    )
                elif savings <= 0:
                    decision_message = (
                        f"The two-store option saves \\${savings:.2f} versus "
                        f"the best single store. Your threshold is "
                        f"\\${savings_threshold:.2f}, but the optimizer "
                        "requires positive savings. "
                        f"Recommendation: shop at {recommended_stores}."
                    )
                else:
                    decision_message = (
                        f"The two-store option saves \\${savings:.2f} versus "
                        f"the best single store, below your "
                        f"\\${savings_threshold:.2f} threshold. "
                        f"Recommendation: shop at {recommended_stores}."
                    )

                st.info(decision_message)
            else:
                st.info(result["recommendation"])

            if (
                result["best_single"] is not None
                and result["best_multi"] is not None
            ):
                st.subheader("Option Comparison")

                comparison = pd.DataFrame({
                    "Option": [
                        "Best single store",
                        "Best two-store combination"
                    ],
                    "Store(s)": [
                        result["best_single"]["stores"][0],
                        " + ".join(result["best_multi"]["stores"])
                    ],
                    "Total Cost": [
                        result["best_single"]["total_cost"],
                        result["best_multi"]["total_cost"]
                    ]
                })

                comparison["Total Cost"] = comparison["Total Cost"].map(
                    "${:.2f}".format
                )

                st.dataframe(
                    comparison,
                    use_container_width=True,
                    hide_index=True
                )

            st.subheader("Shopping Plan")

            shopping_plan = recommended["shopping_plan"].copy()

            shopping_plan = shopping_plan[
                [
                    "item",
                    "quantity",
                    "store",
                    "price",
                    "total_item_cost"
                ]
            ].sort_values(["store", "item"])

            shopping_plan = shopping_plan.rename(columns={
                "item": "Item",
                "quantity": "Quantity",
                "store": "Store",
                "price": "Unit Price",
                "total_item_cost": "Item Total"
            })

            shopping_plan["Item"] = shopping_plan["Item"].str.title()

            shopping_plan["Unit Price"] = shopping_plan["Unit Price"].map(
                "${:.2f}".format
            )

            shopping_plan["Item Total"] = shopping_plan["Item Total"].map(
                "${:.2f}".format
            )

            st.dataframe(
                shopping_plan,
                use_container_width=True,
                hide_index=True
            )

        except ValueError as error:
            st.error(str(error))
