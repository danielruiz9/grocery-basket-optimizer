# Grocery Basket Optimizer

A quantity-aware Streamlit app that compares grocery baskets across Walmart, No Frills, and Food Basics using collected Canadian store listing data.

## What the App Does

Grocery prices are difficult to compare when stores sell different package sizes, use variable-weight pricing, or attach conditions to promotions. This project turns those listings into practical basket estimates for shoppers deciding whether a second stop is worth it.

Users can:

- Choose grocery items from the currently supported comparison groups.
- Enter a requested quantity in a compatible unit.
- Compare the best complete one-store and two-store plans.
- Set the minimum dollar savings required to justify an extra stop.
- Review the selected products, package counts, fulfilled quantities, estimated item costs, and product links.

## How It Works

1. **Product collection:** store-specific collectors capture fields exposed by Walmart Canada, No Frills Canada, and Food Basics Canada search or product data.
2. **Classification:** deterministic title rules assign supported products to categories, product families, physical forms, attributes, and exact comparison groups. Uncertain listings remain unsupported instead of being forced into a group.
3. **Unit normalization:** package and listed-unit prices are converted into compatible standardized units, with normalization errors and data-quality warnings preserved.
4. **Package-aware basket costing:** fixed packages use whole-package ceiling calculations, variable-weight products scale from their listed unit price, and multi-buy pricing is used only when the known threshold is met.
5. **Store optimization:** the optimizer finds the lowest-cost complete single-store plan and, when enabled, the lowest-cost plan across each store pair. It recommends the second stop only when its dollar savings meet the user's threshold.

```text
store pages
→ collectors
→ normalized product data
→ cross-store integration
→ basket costing
→ optimizer
→ Streamlit app
```

## Technical Highlights

- Python and pandas for collection, validation, transformation, and optimization.
- Streamlit for the interactive basket-building and recommendation interface.
- `requests` and Beautiful Soup for store collection where those tools match the retailer's page structure.
- Deterministic, rule-based product classification with conservative unsupported handling.
- Normalized price comparison across compatible weight, volume, and count units.
- Separate costing rules for fixed-package and variable-weight products.
- Explicit multi-buy threshold and single-item price handling.
- Cross-store schema alignment, deduplication, and standardized-unit validation.
- Automated coverage for pricing, classification, collectors, integration, basket costing, both optimizer layers, and UI-facing result shapes.

## Current Scope

The current collected snapshot covers:

- Walmart Canada
- No Frills Canada
- Food Basics Canada

Supported comparison groups currently present in the optimizer-ready data are:

- **Meat:** chicken breast, boneless chicken breast, diced chicken breast, and chicken breast strips.
- **Eggs:** medium, large, and extra-large eggs.
- **Produce:** fresh apples, fresh bananas, and fresh plantains.
- **Pasta:** dry pasta.
- **Bakery:** sliced bread and artisan bread.
- **Cheese:** cheddar, mozzarella, marble, Swiss, cottage, processed, and cheese with an unspecified type.

Availability varies by store. Comparison-group boundaries are respected: for example, processed cheese does not compete with cheddar, and fresh bananas do not compete with plantains.

## Data Snapshot

The app reads [`data/optimizer_ready_prices.csv`](data/optimizer_ready_prices.csv), the committed optimizer-ready snapshot produced by the cross-store integration layer. The app does not need to run the collectors or access retailer websites at startup.

The interface displays the latest observation date in that file. Prices are estimates from the collected snapshot and may differ from checkout prices.

## Repository Structure

```text
grocery-basket-optimizer/
├── app.py                         # Streamlit application
├── data/
│   ├── *_prices_normalized.csv   # Store-level and combined normalized outputs
│   ├── optimizer_ready_prices.csv
│   └── sample_*.csv              # Original sample-MVP fixtures
├── notebooks/
│   ├── optimizer_v0.ipynb
│   └── optimizer_v1.ipynb
├── src/
│   ├── collectors/
│   │   ├── common.py
│   │   ├── foodbasics.py
│   │   ├── nofrills.py
│   │   └── walmart.py
│   ├── basket_costing.py
│   ├── classification.py
│   ├── integration.py
│   ├── optimizer.py              # Original sample-data optimizer
│   ├── pricing.py
│   └── real_price_optimizer.py   # Quantity-aware real-price optimizer
├── tests/                         # Offline unit and regression tests
├── product_spec.md
├── requirements.txt
└── README.md
```

## Running Locally

From the repository root, create and activate a virtual environment, install the project requirements, and start Streamlit:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

On Windows, activate the environment with `.venv\Scripts\activate` instead.

Run the full automated test suite with:

```bash
python -m pytest -q
```

Collector parsing tests use saved fixtures and do not require live retailer requests.

## Known Limitations

- Prices are collected snapshots and are not guaranteed to be real-time.
- Retailer collection depends on store page structures, including rendered or embedded product data, and live collection can be limited by anti-bot controls.
- Only explicitly supported comparison groups are included in optimization.
- Variable-weight item totals are estimates based on the requested weight and listed unit price.
- Product availability at checkout is not guaranteed by the collected listings.
- Location, travel time, and the real cost of an additional stop are not yet modeled; the user-supplied dollar threshold is the current convenience rule.

## Future Improvements

- Schedule a weekly data refresh with freshness monitoring.
- Expand the supported products and stores after validating comparison rules.
- Add location and travel-time-aware optimization.
- Model promotions and purchase conditions in greater detail.
