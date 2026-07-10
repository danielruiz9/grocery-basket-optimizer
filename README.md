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
├── data/
│   ├── sample_prices.csv
│   └── sample_basket.csv
├── notebooks/
│   └── optimizer_v0.ipynb
├── README.md
└── product_spec.md