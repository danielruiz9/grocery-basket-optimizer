import pytest

from src.classification import classify_product


@pytest.mark.parametrize("title", [
    "Super! Mac , Shells & Real Aged Cheddar",
    "Super! Mac , Shells & White Cheddar",
    "Mac with Aged Cheddar", "Macaroni with Cheese",
    "Shallot & Chives Spreadable Cheese", "Garlic & Fine Herbs Spreadable Cheese",
    "Cheese Dippers Original 4P", "Original Natural Cheese Snacks",
    "Apple and Cinnamon Instant Oatmeal, High Protein", "Apple Oatmeal",
    "Banana Brownie", "Banana Teething Wafers, Baby Mum-Mum",
    "Banana Snacks", "Protein Shake Protein Banana Protein Shake",
    "Synergy The Real Kombucha Honeycrisp Apple",
    "Great Value Frozen Buttermilk Chicken Breast Pieces, 1.2 kg",
    "Italian-Style Chicken Breast Strips", "Duck Eggs", "Milk Chocolate Eggs",
])
def test_search_noise_does_not_compete_with_plain_groceries(title):
    assert classify_product(title)["comparison_group"] == "unsupported"


@pytest.mark.parametrize("title,group", [
    ("Honeycrisp Apples", "fresh_apples"),
    ("Fresh Bananas", "fresh_bananas"),
    ("Green Cooking Bananas", "cooking_bananas"),
    ("Plantains", "fresh_plantains"),
    ("Old Cheddar Cheese Block", "cheddar_cheese"),
    ("Spreadable Cream Cheese", "cream_cheese"),
    ("Dry Macaroni", "dry_pasta"), ("Rotini", "dry_pasta"),
    ("Uncooked Boneless Chicken Breast", "chicken_breast_boneless"),
    ("Large Eggs", "large_eggs"),
])
def test_legitimate_products_remain_supported(title, group):
    assert classify_product(title)["comparison_group"] == group


def test_explicit_prepared_food_path_disambiguates_chicken_strips():
    from src.collectors.common import normalize_listing
    row = {"product_title": "Chicken Breast Strips", "displayed_price": 4.99,
           "package_size": 200, "package_unit": "g"}
    assert normalize_listing(row)["comparison_group"] == "chicken_breast_strips"
    row["product_url"] = "https://example.com/aisles/deli-prepared-meals/ready-meals-sides/chicken/p/123"
    assert normalize_listing(row)["comparison_group"] == "unsupported"
    row["product_url"] = "https://example.com/meat/chicken/p/123?ref=prepared-meals"
    assert normalize_listing(row)["comparison_group"] == "chicken_breast_strips"
