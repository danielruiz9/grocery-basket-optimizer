# Grocery Basket Optimizer — Product Spec v0

## Problem

Weekly grocery shoppers often waste time comparing flyers across multiple stores. Existing flyer apps help users find individual deals, but they do not easily answer the more practical question: given my full grocery list, which store or combination of stores should I visit to minimize cost without wasting too much time?

## Target User

The initial target user is a price-conscious grocery shopper in the GTA who buys a weekly basket of items and is willing to visit one or two stores if the savings are worth it.

## Minimial Viable Product (MVP) Goal

Build a simple prototype where a user enters a weekly grocery list and a maximum number of stores they are willing to visit. The tool recommends the cheapest store or store combination for that basket, showing estimated total cost, savings, and item-level recommendations.

## MVP Inputs

- Grocery list entered by user
- Available stores
- Current weekly prices for selected items
- Maximum number of stores allowed
- Optional later: user location or travel-time estimate

## MVP Outputs

- Best single-store option
- Best two-store combination
- Estimated basket cost
- Estimated savings versus default store
- Explanation of which items should be bought where

## Initial Data Plan

Start with a manually collected sample dataset from three stores and 10–15 common grocery items. This avoids getting stuck on web scraping before the core product logic works.

## Recommendation Logic

The tool will calculate the total basket cost at each store for the single-store option.

For the two-store option, the tool will test every pair of stores and assign each item to the cheaper store within that pair. It will then compare total basket costs and recommend the cheapest option.

## Stretch Features

- Historical flyer scraping
- Sale-cycle prediction
- Travel-time adjustment
- “Worth it?” score
- Store preference settings
- Substitute item suggestions
