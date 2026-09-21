import unittest

from src.classification import classify_product, normalize_product_name


class TestProductClassification(unittest.TestCase):

    def test_loblaws_flavoured_snacks_are_not_fresh_fruit_or_cheese(self):
        for title in (
            "Gerber Puffs, Strawberry & Apple Flavour, Baby Snack",
            "Gerber Lil’ Crunchies Apple Sweet Potato Flavour",
            "Gerber Lil’ Crunchies Mild Cheddar Flavour Toddler Snacks, 12 Months & Up",
            "Apple Flavoured Cream Cheese", "Frozen Apple Blend",
            "Apple Puree", "Cheddar Flavour Snacks",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], "unsupported")
        for title, group in (
            ("Gala Apples, 4 lb bag", "fresh_apples"),
            ("Royal Gala Apples", "fresh_apples"),
            ("Original Cheese Snacks 6P", "cheese_unspecified_type"),
            ("Gouda Taste Cheese Snacks 6P", "cheese_unspecified_type"),
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], group)

    def test_prepared_chicken_does_not_compete_with_raw_chicken(self):
        for title in (
            "Oven Roasted Chicken Breast Strips",
            "Cajun Chicken Breast Roast",
            "Roast Chicken Breast",
            "Fully Cooked Chicken Breast",
            "Fully Coocked Chicken Breast Slices",
            "Rotisserie Chicken Breast",
            "Grilled Chicken Breast",
            "Smoked Chicken Breast Slices",
            "Prepared Chicken Breast",
            "Ready-to-Eat Chicken Breast Strips",
            "Cheese Stuffed Chicken Breast",
            "Chicken Breast Pasta Salad",
            "Chicken Breast Soup",
        ):
            with self.subTest(title=title):
                self.assertEqual(
                    classify_product(title)["comparison_group"],
                    "unsupported",
                )

    def test_seasoned_chicken_does_not_compete_with_plain_chicken(self):
        titles = [
            "Honey Garlic Boneless Chicken Breast",
            "Chicken Breast Halves Montreal BBQ",
            "Pinehill Frozen Chicken Breast Seasoned 2 kg",
            "Mumbai Chicken Breast Boneless Skinless",
            "Chicken Breast Stir-Fry Boneless Skinless",
        ]

        for title in titles:
            self.assertEqual(
                classify_product(title)["comparison_group"],
                "unsupported",
            )

    def test_raw_chicken_forms_are_preserved(self):
        for title, group in (
            ("Boneless Skinless Chicken Breasts", "chicken_breast_boneless"),
            ("Bone-In Chicken Breasts", "chicken_breast_bone_in"),
            ("Chicken Breast Fillet", "chicken_breast"),
            ("Uncooked Chicken Breast Strips", "chicken_breast_strips"),
            ("Raw Diced Chicken Breast", "chicken_breast_diced"),
            ("Breaded Chicken Breast Strips", "breaded_chicken_breast"),
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], group)

    def test_banana_desserts_and_processed_plantains_are_unsupported(self):
        for title in (
            "Banana Ice Cream with Fudge Chunks and Walnuts, Chunky Monkey",
            "Banana Ice-Cream", "Banana Dessert", "Banana Pudding",
            "Banana Custard", "Banana Milkshake", "Banana Flavor Snack",
            "Banana Yoghurt", "Banana Flavoured Cream Cheese",
            "Plantain Chips", "Fried Plantains", "Cooked Plantains",
            "Steamed Cooking Bananas",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], "unsupported")

    def test_fresh_banana_varieties_and_cooking_groups_remain_distinct(self):
        for title, group in (
            ("Organic Banana", "fresh_bananas"),
            ("Miniature banana", "fresh_bananas"),
            ("Thai Banana", "fresh_bananas"),
            ("Green banana", "fresh_bananas"),
            ("Green Cooking Bananas", "cooking_bananas"),
            ("Baking Bananas", "cooking_bananas"),
            ("Plantains, Single", "fresh_plantains"),
            ("Plantain Cooking Bananas", "fresh_plantains"),
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], group)

    def test_pasta_bread_and_cheese_search_noise_is_unsupported(self):
        for title in (
            "Pasta Sauce", "Pasta Salad", "Prepared Penne Meal",
            "Cooked Spaghetti", "Konjac Spaghetti",
            "White Cheddar Macaroni and Cheese",
            "Naan Bread", "Pita Bread", "Bread Pudding", "Bagel Chips",
            "Frozen Garlic Cheese Bread",
            "Cheddar Cheese Crackers", "Cheese Sauce", "Cheese Pizza",
            "Cream Cheese Dip", "Cheddar Cheese Sandwich",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], "unsupported")

    def test_legitimate_pasta_bread_and_cheese_products_are_preserved(self):
        for title, group in (
            ("Rotini", "dry_pasta"), ("Cavatappi", "dry_pasta"),
            ("Fresh Pasta", "fresh_pasta"), ("Cheese Tortellini", "filled_pasta"),
            ("White Sandwich Bread", "sliced_bread"),
            ("Asiago Cheese Bread Loaf", "artisan_bread"),
            ("Pizza Mozzarella Cheese", "mozzarella_cheese"),
            ("Shredded Double Cheddar Cheese Blend", "cheddar_cheese"),
            ("Cheddar-Style Processed Cheese Slices", "processed_cheese"),
            ("Thin Sliced Process Cheese Mozzarella", "processed_cheese"),
            ("Cracker Barrel Shredded Old Cheddar", "cheddar_cheese"),
            ("Nibblers Original Natural Cheese Snacks", "cheese_unspecified_type"),
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], group)

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
            diced["comparison_group"],
            "chicken_breast_diced",
        )
        self.assertEqual(
            diced["attributes"],
            ["raised_without_antibiotics"],
        )

    def test_classifies_chicken_breast_fillets_as_fillets(self):
        result = classify_product(
            "Boneless Skinless Chicken Breast Fillets, Prime"
        )

        self.assertEqual(
            result["comparison_group"],
            "chicken_breast_boneless",
        )
        self.assertEqual(result["product_form"], "fillet")

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

    def test_pickled_eggs_are_not_treated_as_shell_eggs(self):
        result = classify_product(
            "Pickled Eggs Seasoned Vinegar Dill Pickled 500 ml"
        )

        self.assertEqual(result["comparison_group"], "unsupported")

    def test_preserves_apple_varieties_as_broad_substitutes(self):
        gala = classify_product("Organic Gala Apples 3 lb Bag")
        granny_smith = classify_product("Granny Smith Apples")

        self.assertEqual(gala["comparison_group"], "fresh_apples")
        self.assertEqual(granny_smith["comparison_group"], "fresh_apples")
        self.assertEqual(gala["product_form"], "bagged")
        self.assertEqual(granny_smith["product_form"], "loose")
        self.assertEqual(gala["attributes"], ["organic"])

    def test_does_not_classify_apple_cereal_as_fresh_apples(self):
        result = classify_product(
            "Cheerios Cereal Apple Cinnamon Family Size"
        )

        self.assertEqual(result["comparison_group"], "unsupported")

    def test_classifies_bananas_without_matching_banana_bread(self):
        bananas = classify_product("Organic Bananas")
        banana_bread = classify_product("Banana Bread")

        self.assertEqual(bananas["comparison_group"], "fresh_bananas")
        self.assertEqual(bananas["product_form"], "loose")
        self.assertEqual(banana_bread["comparison_group"], "sliced_bread")

    def test_does_not_classify_processed_banana_products_as_fresh(self):
        processed_titles = (
            "Banana - Sliced",
            "Gerber Puffs, Banana Flavour, Snack for Babies 8 Mo & Up",
            "Nature's Bananas, Frozen Fresh in Peanut Butter & Dark Chocolate",
            "Golden Saba Steamed Banana",
            "Pink Guava, Mango, and Banana Fruit Blend",
            "Pineapple, Banana, and Mango Fruit Blend",
            "Banana Drink Mix",
        )

        for title in processed_titles:
            with self.subTest(title=title):
                result = classify_product(title)
                self.assertEqual(
                    result["comparison_group"],
                    "unsupported",
                )

    def test_does_not_classify_baby_banana_cereals_as_fresh(self):
        result = classify_product(
            "Baby Cereals, Rice & Banana, Stage 2"
        )

        self.assertEqual(result["comparison_group"], "unsupported")

    def test_classifies_cooking_bananas_separately_from_fresh_bananas(self):
        cooking = classify_product("Green Cooking Bananas")
        fresh = classify_product("Bananas, Bunch")

        self.assertEqual(cooking["product_family"], "bananas")
        self.assertEqual(cooking["comparison_group"], "cooking_bananas")
        self.assertEqual(fresh["comparison_group"], "fresh_bananas")

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

    def test_classifies_standalone_rotini_and_cavatappi_as_dry_pasta(self):
        rotini = classify_product("Rotini")
        cavatappi = classify_product("Cavatappi")

        self.assertEqual(rotini["comparison_group"], "dry_pasta")
        self.assertEqual(cavatappi["comparison_group"], "dry_pasta")

    def test_does_not_classify_macaroni_and_cheese_as_dry_pasta(self):
        result = classify_product("White Cheddar Macaroni & Cheese")

        self.assertEqual(result["comparison_group"], "unsupported")

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

    def test_classifies_obvious_artisan_bread_names(self):
        for title in (
            "Sourdough Bistro",
            "Classic White Bistro",
            "Pane Rustico",
        ):
            with self.subTest(title=title):
                result = classify_product(title)
                self.assertEqual(result["category"], "bakery")
                self.assertEqual(result["product_family"], "bread")
                self.assertEqual(
                    result["comparison_group"],
                    "artisan_bread",
                )
                self.assertEqual(result["product_form"], "loaf")

    def test_recognizes_asiago_bakery_loaf_independent_of_retailer(self):
        for title in (
            "Asiago Cheese Bread Loaf",
            "Front Street Bakery Asiago Cheese Bread Loaf, 454 g",
            "ASIAGO-CHEESE BREAD LOAF",
        ):
            with self.subTest(title=title):
                result = classify_product(title)
                self.assertEqual(result["comparison_group"], "artisan_bread")
                self.assertEqual(result["product_form"], "loaf")

    def test_specialty_names_do_not_automatically_mean_artisan_bread(self):
        # Metro's pages show these three as packaged, pre-sliced loaves,
        # despite their broad "artisan & specialty"/fresh-bread aisle paths.
        for title in (
            "Sourdough Bread, Rustico",
            "Everything Loaf Bread Made with Whole Grains",
            "Flax & Quinoa Loaf Bread With 100% Whole Grain",
            "Sliced Sourdough Bread",
            "Whole Grain 12-Grain Sliced Bread",
            "Sliced Asiago Cheese Bread Loaf",
            "Asiago Cheese Sandwich Bread Loaf",
        ):
            with self.subTest(title=title):
                result = classify_product(title)
                self.assertEqual(result["comparison_group"], "sliced_bread")
                self.assertEqual(result["product_form"], "sliced_loaf")

    def test_preserves_cheese_type_across_product_forms(self):
        block = classify_product("Old Cheddar Cheese Block")
        shredded = classify_product("Cheddar Cheese Shreds")

        self.assertEqual(block["comparison_group"], "cheddar_cheese")
        self.assertEqual(shredded["comparison_group"], "cheddar_cheese")
        self.assertEqual(block["product_form"], "block")
        self.assertEqual(shredded["product_form"], "shredded")
        self.assertEqual(shredded["attributes"], [])

    def test_dairy_alternatives_do_not_compete_with_dairy_cheese(self):
        for title in (
            "Dairy-Free Cheddar Style Shreds", "Dairy Free Feta Cheese",
            "Dairy Free Mozzarella Cheese Shreds", "Non-Dairy Cheddar Slices",
            "Vegan Cheddar Cheese", "Plant-Based Mozzarella Cheese",
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], "unsupported")
        alternative = classify_product("Dairy-Free Cheddar Style Shreds")
        self.assertEqual(alternative["product_form"], "shredded")
        self.assertEqual(alternative["attributes"], ["dairy_free"])
        for title, group in (
            ("Lactose Free Cheddar Cheese", "cheddar_cheese"),
            ("Old Cheddar Cheese Bar", "cheddar_cheese"),
            ("Fresh Mozzarella Slice Ball", "mozzarella_cheese"),
        ):
            with self.subTest(title=title):
                self.assertEqual(classify_product(title)["comparison_group"], group)

    def test_classifies_cheese_bar_as_block(self):
        result = classify_product(
            "Black Diamond Marble Cheddar Cheese Bar, 400g"
        )

        self.assertEqual(result["comparison_group"], "cheddar_cheese")
        self.assertEqual(result["product_form"], "block")

    def test_classifies_mozzarella_ball_as_ball_not_sliced(self):
        result = classify_product("Fresh Mozzarella Slice Ball")

        self.assertEqual(result["comparison_group"], "mozzarella_cheese")
        self.assertEqual(result["product_form"], "ball")

    def test_separates_processed_cheese_from_natural_cheddar(self):
        result = classify_product(
            "Cheddar-Style Processed Cheese Slices"
        )

        self.assertEqual(result["comparison_group"], "processed_cheese")
        self.assertEqual(result["product_form"], "sliced")

    def test_cheese_spreads_are_processed_but_cream_cheese_stays_separate(self):
        self.assertEqual(
            classify_product("The Laughing Cow Cheese Original 400 g")[
                "comparison_group"
            ],
            "processed_cheese",
        )
        self.assertEqual(
            classify_product("Cheese Spread 250 g")["comparison_group"],
            "processed_cheese",
        )
        self.assertEqual(
            classify_product("Cream Cheese Spread 250 g")["comparison_group"],
            "cream_cheese",
        )

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

    def test_rejects_representative_food_basics_search_noise(self):
        noisy_titles = (
            "Fully Coocked Chicken Breast Slices",
            "Chicken Breast Souvlaki, Value Pack",
            "Hard Boiled Peeled Eggs, Eggs2go!",
            "Quail Eggs",
            "2% Banana Greek Yogurt",
            "Chocolate Chip Banana Muffins, Value Pack",
            "Banana Leaf",
            "Frozen Garlic Bread",
        )

        for title in noisy_titles:
            with self.subTest(title=title):
                self.assertEqual(
                    classify_product(title)["comparison_group"],
                    "unsupported",
                )


if __name__ == "__main__":
    unittest.main()
