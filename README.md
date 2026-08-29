# Grocery Basket Optimizer

## Overview

The Grocery Basket Optimizer is a product/data project that helps users decide where to buy their weekly grocery basket. Instead of only finding individual flyer deals, the tool compares the total cost of a full grocery list across stores and recommends the cheapest single-store or two-store option.

## Problem

Grocery shoppers often spend time manually comparing flyers across multiple stores. Existing flyer apps show item-level deals, but they do not clearly answer which store or store combination is best for an entire grocery basket.

## Minimal Viable Product (MVP)

The first version uses a manually created grocery price dataset with three stores and common grocery items.

The MVP will calculate:

- Cheapest single-store option
- Cheapest two-store combination
- Total basket cost
- Estimated savings
- Item-by-item store recommendation

## Project Structure

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

## MVP Results

Using a sample basket of 10 grocery items across No Frills, Walmart, and Food Basics, the optimizer found:

- Best single-store option: Food Basics — $53.37
- Best two-store option: Walmart + Food Basics — $51.07
- Savings from visiting two stores: $2.30

Although the two-store combination was mathematically cheaper, the savings were small. This suggests that the best practical recommendation may still be Food Basics only, unless the user is already near Walmart or the two stores are close together.

This highlights the main product insight: the cheapest option is not always the best user recommendation once convenience and effort are considered.
