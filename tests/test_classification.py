import unittest

from src.classification import classify_product, normalize_product_name


class TestProductClassification(unittest.TestCase):

    def test_normalizes_case_accents_and_punctuation(self):
        normalized = normalize_product_name("  Café-Style BAGELS!!!  ")

        self.assertEqual(normalized, "cafe style bagels")

    def test_classifies_boneless_halal_chicken_breast(self):
        result = classify_product(
            "Fresh Halal Boneless Skinless Chicken Breasts"
        )

        self.assertEqual(result["category"], "meat")
        self.assertEqual(result["product_family"], "chicken_breast")
        self.assertEqual(
            result["comparison_group"],
            "chicken_breast_boneless",
        )
        self.assertEqual(result["product_form"], "whole")
        self.assertEqual(
            result["attributes"],
            ["halal", "boneless", "skinless"],
        )

    def test_distinguishes_bone_in_and_breaded_chicken(self):
        bone_in = classify_product("Bone-In Chicken Breast")
        breaded = classify_product("Breaded Chicken Breast Strips")

        self.assertEqual(
            bone_in["comparison_group"],
            "chicken_breast_bone_in",
        )
        self.assertIn("bone_in", bone_in["attributes"])
        self.assertEqual(
            breaded["comparison_group"],
            "breaded_chicken_breast",
        )

    def test_classifies_sliced_and_diced_chicken_forms(self):
        sliced = classify_product(
            "Prime Sliced Boneless Skinless Chicken Breasts "
            "Raised Without Antibiotics"
        )
        diced = classify_product(
            "Prime Raised Without Antibiotics Diced Chicken Breast"
        )

        self.assertEqual(sliced["product_form"], "sliced")
        self.assertEqual(
            sliced["comparison_group"],
            "chicken_breast_boneless",
        )
        self.assertEqual(
            sliced["attributes"],
            ["boneless", "skinless", "raised_without_antibiotics"],
        )
        self.assertEqual(diced["product_form"], "diced")
        self.assertEqual(
            diced["attributes"],
            ["raised_without_antibiotics"],
        )

    def test_classifies_large_free_run_eggs(self):
        result = classify_product("Large Free-Run Brown Eggs, 12 Count")

        self.assertEqual(result["category"], "dairy")
        self.assertEqual(result["product_family"], "eggs")
        self.assertEqual(result["comparison_group"], "large_eggs")
        self.assertEqual(result["product_form"], "shell")
        self.assertEqual(result["attributes"], ["free_run"])

    def test_classifies_xl_and_extra_large_eggs(self):
        xl = classify_product("Great Value XL White 12 Eggs")
        extra_large = classify_product("Extra-Large Brown Eggs, 12 Count")

        self.assertEqual(xl["comparison_group"], "extra_large_eggs")
        self.assertEqual(
            extra_large["comparison_group"],
            "extra_large_eggs",
        )

    def test_extracts_omega_3_attribute(self):
        omega3 = classify_product(
            "Conestoga Farms Free Run Omega3 Large 18 Eggs"
        )
        omega_hyphen = classify_product(
            "Great Value Omega-3 Large White 12 Eggs"
        )

        self.assertEqual(omega3["attributes"], ["free_run", "omega_3"])
        self.assertEqual(omega_hyphen["attributes"], ["omega_3"])

    def test_distinguishes_liquid_egg_whites(self):
        result = classify_product("Liquid Egg Whites 500 mL")

        self.assertEqual(
            result["comparison_group"],
            "liquid_egg_product",
        )
        self.assertEqual(result["product_form"], "liquid")

    def test_preserves_apple_varieties_as_broad_substitutes(self):
        gala = classify_product("Organic Gala Apples 3 lb Bag")
        granny_smith = classify_product("Granny Smith Apples")

        self.assertEqual(gala["comparison_group"], "fresh_apples")
        self.assertEqual(granny_smith["comparison_group"], "fresh_apples")
        self.assertEqual(gala["product_form"], "bagged")
        self.assertEqual(granny_smith["product_form"], "loose")
        self.assertEqual(gala["attributes"], ["organic"])

    def test_classifies_bananas_without_matching_banana_bread(self):
        bananas = classify_product("Organic Bananas")
        banana_bread = classify_product("Banana Bread")

        self.assertEqual(bananas["comparison_group"], "fresh_bananas")
        self.assertEqual(bananas["product_form"], "loose")
        self.assertEqual(banana_bread["comparison_group"], "sliced_bread")

    def test_classifies_plantains_as_distinct_produce(self):
        result = classify_product(
            "Plantain, Sold in Singles, 0.25 - 0.35 KG"
        )

        self.assertEqual(result["category"], "produce")
        self.assertEqual(result["product_family"], "plantains")
        self.assertEqual(result["comparison_group"], "fresh_plantains")
        self.assertEqual(result["product_form"], "loose")

    def test_preserves_dry_pasta_shapes_as_broad_substitutes(self):
        spaghetti = classify_product("Gluten-Free Spaghetti")
        penne = classify_product("Whole Wheat Penne Rigate")

        self.assertEqual(spaghetti["comparison_group"], "dry_pasta")
        self.assertEqual(penne["comparison_group"], "dry_pasta")
        self.assertEqual(spaghetti["attributes"], ["gluten_free"])
        self.assertEqual(penne["attributes"], ["whole_wheat"])

    def test_distinguishes_fresh_and_filled_pasta(self):
        fresh = classify_product("Fresh Linguine Pasta")
        filled = classify_product("Cheese Ravioli")

        self.assertEqual(fresh["comparison_group"], "fresh_pasta")
        self.assertEqual(filled["comparison_group"], "filled_pasta")

    def test_distinguishes_bread_bagels_and_baguettes(self):
        bread = classify_product("Whole Wheat Sandwich Bread")
        bagels = classify_product("Everything Bagels")
        baguette = classify_product("French Baguette")

        self.assertEqual(bread["comparison_group"], "sliced_bread")
        self.assertEqual(bagels["comparison_group"], "bagels")
        self.assertEqual(baguette["comparison_group"], "baguettes")
        self.assertEqual(bread["product_family"], "bread")
        self.assertEqual(bagels["product_family"], "bread")
        self.assertEqual(baguette["product_family"], "bread")

    def test_preserves_cheese_type_across_product_forms(self):
        block = classify_product("Old Cheddar Cheese Block")
        shredded = classify_product("Dairy-Free Cheddar Style Shreds")

        self.assertEqual(block["comparison_group"], "cheddar_cheese")
        self.assertEqual(shredded["comparison_group"], "cheddar_cheese")
        self.assertEqual(block["product_form"], "block")
        self.assertEqual(shredded["product_form"], "shredded")
        self.assertEqual(shredded["attributes"], ["dairy_free"])

    def test_classifies_cheese_bar_as_block(self):
        result = classify_product(
            "Black Diamond Marble Cheddar Cheese Bar, 400g"
        )

        self.assertEqual(result["comparison_group"], "cheddar_cheese")
        self.assertEqual(result["product_form"], "block")

    def test_returns_explicit_unknown_for_unsupported_product(self):
        result = classify_product("Frozen Pepperoni Pizza")

        self.assertEqual(
            result,
            {
                "category": "unknown",
                "product_family": "unknown",
                "comparison_group": "unsupported",
                "product_form": "unknown",
                "attributes": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
