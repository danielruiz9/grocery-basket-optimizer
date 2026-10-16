# Grocery Basket Optimizer

A quantity-aware Streamlit app that compares estimated grocery basket costs across Walmart, No Frills, Food Basics, Metro, Loblaws, and Sobeys using collected Canadian store data.

## What the App Does

Grocery prices are difficult to compare when stores sell different package sizes, use variable-weight pricing, or attach conditions to promotions. This project turns those listings into practical basket estimates for shoppers deciding whether a second stop is worth it.

Users can:

- Choose grocery items from the currently supported comparison groups.
- Enter a requested quantity in a compatible unit.
- Compare the best complete one-store and two-store plans.
- Set the minimum dollar savings required to justify an extra stop.
- Review the selected products, package counts, fulfilled quantities, estimated item costs, and product links.

## How It Works

1. **Product collection:** six store-specific collectors capture exposed listing fields for seven initial searches: chicken breast, eggs, apples, bananas, pasta, bread, and cheese. Shared helpers handle compatible page structures, classification, and pricing.
2. **Classification and normalization:** deterministic title rules assign categories, families, forms, attributes, and comparison groups. Prices normalize to compatible weight, volume, or count units. Unsupported listings, missing values, and quality warnings remain visible in the audit data.
3. **Cross-store integration:** store schemas are aligned, rows deduplicate by store + product URL + observation date, and eligibility filtering produces the optimizer snapshot. Mixed standardized units within a comparison group raise an error.
4. **Quantity-aware costing:** fixed packages round up to whole packages; variable-weight products scale from their listed unit price. Conditional multi-buy prices apply only when a known package-quantity requirement is met; otherwise costing uses the single-item price. An offer without a usable unconditional price is not assumed to qualify.
5. **Store optimization:** each store or pair selects the cheapest compatible fulfillment for every requested item. When a complete single-store option exists, a split is recommended only for positive dollar savings that meet or exceed the threshold, compared at the same two-decimal precision displayed in the app. A valid pair can also cover a basket that no single store can fulfill; impossible coverage produces a clear error.

```text
retailer product pages / captured product data
→ store collectors + shared parsing helpers
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
- Metro Canada
- Loblaws Canada
- Sobeys Canada

Supported comparison groups currently present in the optimizer-ready data are:

- **Meat:** unspecified chicken breast, bone-in chicken breast, boneless chicken breast, and diced chicken breast.
- **Eggs:** medium, large, extra-large, and shell eggs with unspecified size.
- **Produce:** fresh apples, fresh bananas, cooking bananas, and fresh plantains.
- **Pasta:** dry pasta.
- **Bakery:** sliced bread and artisan bread.
- **Cheese:** cheddar, mozzarella, marble, Swiss, cottage, processed, and cheese with an unspecified type.

Coverage varies by store. Processed cheese is separate from natural cheddar; dairy-free cheese alternatives are unsupported rather than competing with dairy cheese. Prepared or seasoned chicken is excluded from plain raw-chicken groups, and fresh bananas remain separate from cooking bananas and plantains.

## Data Snapshot

The app reads [`data/optimizer_ready_prices.csv`](data/optimizer_ready_prices.csv), the repository's optimizer-ready snapshot. The current dataset contains **363 eligible listings across six stores**, with observation dates from **2026-09-18 to 2026-09-20**. The interface displays **Prices last updated: 2026-09-20**; that is the latest observation, not a claim that every row was refreshed together.

[`data/all_stores_prices_normalized.csv`](data/all_stores_prices_normalized.csv) retains 480 deduplicated audit rows, including unsupported listings and normalization failures. Warnings alone do not exclude an otherwise valid row. Sobeys uses the **Urban Fresh Spadina, Toronto** store context. All 84 captured Sobeys rows remain in the audit data, including five out-of-stock rows. Out-of-stock listings are excluded from optimizer-ready data. Availability, product IDs, and store context are preserved where captured.

Collection currently requires browser-assisted captures for **No Frills, Food Basics, Metro, Loblaws, and Sobeys**, because of rendered data and/or direct-request restrictions. Parsers consume the exposed HTML or product records; browser capture is not an unattended refresh service, and anti-bot protections are not bypassed. Walmart retains a direct `requests`/HTML discovery collector; weekly price maintenance instead uses browser product-page captures. The app itself uses local CSVs and requires no retailer access at startup.

For the current five-item example—1.5 kg boneless chicken, 18 large eggs, 1 kg apples, 900 g dry pasta, and one loaf—the best single store is Metro at **$31.65**. Loblaws + Walmart costs **$27.55**, saving **$4.10**. A **$5** threshold therefore recommends Metro. These are snapshot estimates, not measured real-world savings.

## Repository Structure

```text
grocery-basket-optimizer/
├── app.py                         # Streamlit application
├── refresh_weekly.py              # Guided, staged snapshot refresh
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
│   │   ├── loblaws.py
│   │   ├── metro.py
│   │   ├── metro_tiles.py         # Shared Metro/Food Basics tile parsing
│   │   ├── nofrills.py
│   │   ├── sobeys.py
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

Collector parsing tests use saved fixtures and do not require live retailer requests. The automated test suite covers classification, unit conversion, package overshoot, variable-weight and multi-buy costing, availability filtering, sparse/pair-only coverage, exact-cent savings thresholds, deterministic ties, and Streamlit-facing flows.

## Weekly Refresh

With the virtual environment activated, run from the project root:

```bash
python refresh_weekly.py
```

The script archives all eight CSVs in `data/history/YYYY-MM-DD/`. **Walmart maintenance revisits every existing product URL**, including unsupported audit rows, using browser product-page captures at **Toronto Stockyards (1004)**. It does not run discovery searches or direct product-page HTTP requests. The other five stores still use the seven discovery searches with their existing result limits.

For Walmart, the guide prints each product URL and its destination JSON filename, and creates `capture_product.js` in the dated capture folder. Select Stockyards in Walmart, open the product, run the supplied read-only expression in your browser's DevTools Console, and paste the copied JSON into the indicated file. The parser verifies the primary `__NEXT_DATA__` product, URL identity, Walmart seller, store, availability, price and promotion status. Never bypass challenges. A missing or unverifiable product stops publication of the entire Walmart replacement; failed staged rows have no current price or fresh observation date. Verified out-of-stock products remain auditable but are excluded from optimizer-ready output.

To inspect saved Walmart captures without publishing (the stage directory must not already exist):

```bash
python -m src.collectors.walmart_maintenance --captures data/captures/YYYY-MM-DD/walmart --stage data/staging/YYYY-MM-DD-walmart
```

For **No Frills, Food Basics, Metro, Loblaws, and Sobeys**, the guide prints all seven exact search URLs and offers to open each in your default browser.

Capture folders and `metadata.json` are created automatically. For each search:

1. Open the printed URL, select the same GTA store, and wait for product names and prices. Keep unsupported/out-of-stock results; never bypass blocks.
2. In Chrome/Edge: right-click the page → **Inspect** → select the top `<html>` node in **Elements** → right-click → **Copy → Copy outerHTML**.
3. Paste into a plain-text editor (TextEdit: **Format → Make Plain Text**) and save as UTF-8 at the exact printed path, such as `data/captures/YYYY-MM-DD/sobeys/chicken_breast.html`. Do not use View Source, PDF, screenshots, or an `.html.txt` extension.
4. Press Enter in the terminal. The existing retailer parser checks the file immediately; an invalid capture stays on that search for correction. Enter `o` to open its URL or `q` to cancel.

All five stores support this rendered-HTML route; assembling JSON manually is unnecessary. Existing Loblaws/Sobeys JSON captures remain supported. Sobeys' exposed selected-store header is used when available; otherwise the guide asks once for the store location. Keep captures local because full HTML can contain session details. Capture dates remain operator attestations, not independent proof of retailer freshness.

The default **three-calendar-day window includes today and the preceding two dates** (e.g. October 5–7 for an October 7 run). All observations must fall inside it; future, stale, empty, and invalid data fail validation. Actual observation dates are shown and retained when reusing verified captures. Use `--freshness-window 1` for today only, or another positive day count. A date spread exceeding three days still warns. To resume an earlier capture directory within the window, use `--capture-root data/captures/YYYY-MM-DD`.

```bash
python refresh_weekly.py --dry-run
python refresh_weekly.py --open-browser --freshness-window 3
python refresh_weekly.py --store walmart
python refresh_weekly.py --store sobeys
# Only when all six store snapshots are within the refresh window:
python refresh_weekly.py --store sobeys --rebuild --archive-suffix final
```

Dry run checks snapshots, archive collisions, URLs, and captures offline without prompts, browser launches, or writes. Missing captures are expected **Not ready** items, with exit status 1 until ready. Outputs are staged: failed collection or validation leaves current CSVs unchanged. Single-store mode only replaces that store's CSV; production stays unchanged unless `--rebuild` is explicit and all six stores pass freshness checks. Existing eligibility rules still retain out-of-stock rows in audit data but exclude them from optimizer-ready output.

Archives are never overwritten: use a unique `--archive-suffix retry-1` for same-day retries. `refresh_summary.json` beside the archived CSVs records quality counts, totals, duplicates, dates, and product/price/promotion/availability changes keyed by store + URL (product ID fallback), printed before publication. Variable-weight unit-rate changes are reported separately from average-package price changes; an average-only change is not a price-per-weight increase. Walmart staged rows and verification results are also retained in the archive. Comparisons against snapshots lacking store context are differences, not proven same-store price changes.

Nothing commits, pushes, or deploys. Handled publication failures restore archived files. After a forced interruption during replacement, check the archive, restore its eight CSVs if needed, and remove `data/.refresh.lock` only after confirming no refresh is running.

## Known Limitations

- Prices are collected snapshots and are not guaranteed to be real-time.
- Retailer collection depends on store page structures, including rendered or embedded product data, and live collection can be limited by anti-bot controls.
- Only explicitly supported comparison groups are included in optimization.
- Search snapshots cover a limited selection, not complete store catalogues. Title-based classification and broad groups such as “Other cheese” still need human review; brand, taste, and dietary equivalence are not personalized.
- Variable-weight item totals are estimates based on the requested weight and listed unit price.
- Product availability at checkout is not guaranteed by the collected listings.
- Prices depend on the captured store/region. The app does not select nearby stores or guarantee geographically identical pricing across retailers.
- Conflicting source metadata is preserved with warnings where detected; the app does not resolve which source value is correct.
- Wide shopping-result tables may require horizontal scrolling on smaller screens.
- Location, travel time, and the real cost of an additional stop are not yet modeled; the user-supplied dollar threshold is the current convenience rule.

## Future Improvements

- Automate refresh scheduling where reliable retailer collection permits it; browser captures currently remain manual.
- Expand the supported products and stores after validating comparison rules.
- Add location and travel-time-aware optimization.
- Model promotions and purchase conditions in greater detail.
