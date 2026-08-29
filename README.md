# Grocery Basket Optimizer

A Streamlit decision-support MVP that compares a grocery basket across stores and recommends whether shopping at one or two locations is worthwhile.

## Problem and Target User

Grocery shoppers can compare individual flyer deals, but that does not answer a more practical question: which store or combination of stores is best for the full basket?

The initial target user is a price-conscious grocery shopper in the Greater Toronto Area who buys a weekly basket and may visit a second store when the savings justify the extra stop.

## Current MVP

The app currently lets a user:

- Select grocery items and integer quantities from the sample dataset.
- Choose a maximum of one or two stores.
- Set a minimum savings threshold for visiting an additional store.
- Compare the cheapest complete single-store and two-store plans when both exist.
- View the recommended stores, estimated basket cost, option comparison, and item-level shopping plan.

The optimizer also handles incomplete store coverage. It can recommend a two-store plan when no single store covers the basket, fall back to a complete single store when no two-store combination is available, and report an error when neither option can cover every item.

## How the Optimization Works

1. Validate the required columns, basket quantities, and price values.
2. Normalize item names, combine duplicate basket entries, and calculate quantity-adjusted item costs.
3. Evaluate every store individually and retain complete single-store plans.
4. Evaluate every pair of stores, assigning each item to its cheaper store within that pair, and retain pairs that cover the full basket.
5. If both options exist, recommend two stores only when the savings are positive and meet the user's threshold. Otherwise, recommend the best complete plan available.

## Benchmark Result

The included ten-item sample basket produces the following result:

| Option | Store plan | Total cost |
| --- | --- | ---: |
| Best single store | Food Basics | $53.37 |
| Cheapest two-store plan | Food Basics + Walmart | $51.07 |

The two-store plan saves **$2.30**. With the app's default **$5.00 minimum savings threshold**, the recommendation is to shop only at Food Basics because the additional savings do not justify another stop under that rule.

This illustrates the product decision behind the MVP: the lowest mathematical cost is not always the recommended plan once the effort of an additional store is considered.

## Sample Data and Assumptions

The repository contains a manually created, static sample dataset:

- `sample_prices.csv`: 45 price rows covering 15 grocery items across Food Basics, No Frills, and Walmart.
- `sample_basket.csv`: a ten-item basket used for the benchmark and regression test.
- The current dataset represents a single illustrative pricing period.

Each basket quantity is used as a multiplier of the listed price, so total item cost is calculated as `quantity × price`. Items with the same normalized name are assumed to be comparable across stores. The dataset includes unit and package-size fields, but the current optimizer does not normalize prices by size or match brands and equivalent products.

The data is illustrative and is not live grocery pricing.

## Technology

- Python
- pandas
- NumPy
- Streamlit
- Python `unittest`
- Jupyter notebooks
- CSV data files

## Repository Structure

```text
grocery-basket-optimizer/
├── app.py
├── data/
│   ├── sample_basket.csv
│   └── sample_prices.csv
├── notebooks/
│   ├── optimizer_v0.ipynb
│   └── optimizer_v1.ipynb
├── src/
│   ├── __init__.py
│   └── optimizer.py
├── tests/
│   └── test_optimizer.py
├── README.md
├── requirements.txt
└── product_spec.md
```

## Run Locally

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

Run the automated tests with:

```bash
python -m unittest discover -s tests -v
```

## Automated Test Coverage

The current test suite covers:

- Rejection of non-numeric, null, infinite, and negative prices.
- Acceptance of a zero price.
- Regression coverage for the benchmark recommendation and totals.
- A basket that requires two stores for complete coverage.
- Fallback to a single store when no two-store combination exists.
- An error when neither one nor two stores can cover the basket.

## Current Limitations

- Prices are static sample data and are not refreshed automatically.
- Product matching relies on generic item names rather than normalized products or brands.
- Package-size and unit-price equivalence are not used in the optimization.
- Missing products, substitutions, and sale conditions are not modeled beyond basic store coverage.
- The convenience tradeoff is represented by a user-entered dollar threshold rather than location or travel information.
- The optimizer supports at most two stores.
- The benchmark is an illustrative sample result; real-world savings performance and user validation have not been measured.

## Roadmap

1. **Validate a real-world grocery data model:** build a small manually collected dataset that supports package sizes, unit prices, brand or equivalent-product matching, missing products, and sale-price representation.
2. **Automate price ingestion and refresh:** after validating that model, explore automated collection and updating, with possible freshness tracking and historical prices.
3. **Add location and convenience-aware optimization:** incorporate store locations, user location, travel time or distance, and a more realistic cost for making an additional stop.
