import re
import unicodedata


ATTRIBUTE_PATTERNS = (
    ("organic", ("organic",)),
    ("halal", ("halal",)),
    ("free_run", ("free run",)),
    ("free_range", ("free range",)),
    ("dairy_free", ("dairy free", "non dairy")),
    ("gluten_free", ("gluten free",)),
    ("whole_wheat", ("whole wheat", "whole grain")),
    ("boneless", ("boneless",)),
    ("skinless", ("skinless",)),
    (
        "raised_without_antibiotics",
        ("raised without antibiotics",),
    ),
    ("omega_3", ("omega 3", "omega3")),
)


PASTA_TERMS = {
    "pasta",
    "spaghetti",
    "penne",
    "macaroni",
    "rotini",
    "cavatappi",
    "farfalle",
    "cellentani",
    "conchiglie",
    "fusilli",
    "linguine",
    "fettuccine",
    "ravioli",
    "tortellini",
    "noodle",
    "noodles",
}


EXPECTED_STANDARDIZED_UNITS = {
    "chicken_breast": "100g",
    "chicken_breast_bone_in": "100g",
    "chicken_breast_boneless": "100g",
    "chicken_breast_diced": "100g",
    "breaded_chicken_breast": "100g",
    "chicken_breast_strips": "100g",
    "bagels": "100g",
    "baguettes": "100g",
    "sliced_bread": "100g",
    "artisan_bread": "100g",
    "dry_pasta": "100g",
    "fresh_pasta": "100g",
    "filled_pasta": "100g",
    "liquid_egg_product": "1L",
    "extra_large_eggs": "1unit",
    "jumbo_eggs": "1unit",
    "large_eggs": "1unit",
    "medium_eggs": "1unit",
    "small_eggs": "1unit",
    "shell_eggs_unspecified_size": "1unit",
    "fresh_apples": "100g",
    "fresh_bananas": "100g",
    "cooking_bananas": "100g",
    "fresh_plantains": "100g",
    "cream_cheese": "100g",
    "cottage_cheese": "100g",
    "cheddar_cheese": "100g",
    "mozzarella_cheese": "100g",
    "swiss_cheese": "100g",
    "marble_cheese": "100g",
    "processed_cheese": "100g",
    "cheese_unspecified_type": "100g",
}


def _unknown_classification():
    return {
        "category": "unknown",
        "product_family": "unknown",
        "comparison_group": "unsupported",
        "product_form": "unknown",
        "attributes": [],
    }


def normalize_product_name(product_name):
    if not isinstance(product_name, str):
        return ""

    normalized = unicodedata.normalize("NFKD", product_name)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower())
    return " ".join(normalized.split())


def _classification(
    category,
    product_family,
    comparison_group,
    product_form,
    attributes
):
    return {
        "category": category,
        "product_family": product_family,
        "comparison_group": comparison_group,
        "product_form": product_form,
        "attributes": attributes,
    }


def _extract_attributes(normalized_name):
    return [
        attribute
        for attribute, patterns in ATTRIBUTE_PATTERNS
        if any(pattern in normalized_name for pattern in patterns)
    ]


def _classify_chicken_breast(normalized_name, tokens):
    if "chicken" not in tokens or not {"breast", "breasts"} & tokens:
        return None

    # Preparation/deli descriptions must not compete with raw meat. A
    # "roast" can be ambiguous, so leave it unsupported rather than guess.
    if {
        "cooked", "coocked", "roast", "roasted", "rotisserie", "grilled",
        "smoked", "prepared", "stuffed", "souvlaki", "sandwich", "salad",
        "soup", "sauce", "meal", "meals",
    } & tokens or PASTA_TERMS & tokens or "ready to eat" in normalized_name:
        return _unknown_classification()

    attributes = _extract_attributes(normalized_name)

    if "breaded" in tokens:
        comparison_group = "breaded_chicken_breast"
        product_form = "breaded"
    elif {"strip", "strips", "tender", "tenders"} & tokens:
        comparison_group = "chicken_breast_strips"
        product_form = "strips"
    elif "diced" in tokens:
        comparison_group = "chicken_breast_diced"
        product_form = "diced"
    else:
        if "bone in" in normalized_name:
            comparison_group = "chicken_breast_bone_in"
            attributes.append("bone_in")
        elif "boneless" in tokens:
            comparison_group = "chicken_breast_boneless"
        else:
            comparison_group = "chicken_breast"

        if {"fillet", "fillets"} & tokens:
            product_form = "fillet"
        elif {"slice", "slices", "sliced"} & tokens:
            product_form = "sliced"
        else:
            product_form = "whole"

    return _classification(
        "meat",
        "chicken_breast",
        comparison_group,
        product_form,
        attributes
    )


def _classify_bread(normalized_name, tokens):
    bread_terms = {"bread", "loaf", "loaves", "bagel", "bagels", "baguette", "baguettes"}
    if bread_terms & tokens:
        if {
            "naan", "pita", "flatbread", "flatbreads", "wrap", "wraps",
            "tortilla", "tortillas", "pizza",
            "chips", "crisps", "crumbs", "pudding",
        } & tokens or (
            {"sandwich", "sandwiches"} & tokens
            and "sandwich bread" not in normalized_name
        ):
            return _unknown_classification()

    if {
        "cake",
        "cakes",
        "cookie",
        "cookies",
        "frozen",
        "garlic",
        "muffin",
        "muffins",
    } & tokens:
        # Do not let prepared bread fall through to an ingredient's group
        # (for example, frozen garlic cheese bread becoming plain cheese).
        return _unknown_classification() if bread_terms & tokens else None

    attributes = _extract_attributes(normalized_name)

    if {"bagel", "bagels"} & tokens:
        return _classification(
            "bakery", "bread", "bagels", "bagel", attributes
        )

    if {"baguette", "baguettes"} & tokens:
        return _classification(
            "bakery", "bread", "baguettes", "baguette", attributes
        )

    # Recognized whole bakery-loaf style, independent of retailer/brand.
    # Do not infer artisan bread from sourdough, grains, or "loaf" alone;
    # these also describe ordinary packaged sliced breads.
    if (
        "asiago cheese bread loaf" in normalized_name
        and not {"sliced", "sandwich"} & tokens
    ):
        return _classification(
            "bakery", "bread", "artisan_bread", "loaf", attributes
        )

    if {"bread", "loaf", "loaves"} & tokens:
        return _classification(
            "bakery", "bread", "sliced_bread", "sliced_loaf", attributes
        )

    artisan_bread_names = {
        "sourdough bistro",
        "classic white bistro",
        "pane rustico",
    }

    if any(name in normalized_name for name in artisan_bread_names):
        return _classification(
            "bakery", "bread", "artisan_bread", "loaf", attributes
        )

    return None


def _classify_pasta(normalized_name, tokens):
    if not PASTA_TERMS & tokens:
        return None

    if (
        {"sauce", "soup", "salad", "meal", "meals", "cooked", "prepared"} & tokens
        or "ready to eat" in normalized_name
        or "macaroni and cheese" in normalized_name
        or "macaroni cheese" in normalized_name
        or "mac and cheese" in normalized_name
        or "mac n cheese" in normalized_name
    ):
        return _unknown_classification()

    attributes = _extract_attributes(normalized_name)

    if {"ravioli", "tortellini"} & tokens:
        comparison_group = "filled_pasta"
        product_form = "filled"
    elif "fresh" in tokens:
        comparison_group = "fresh_pasta"
        product_form = "fresh"
    else:
        comparison_group = "dry_pasta"
        product_form = "dry"

    return _classification(
        "pantry", "pasta", comparison_group, product_form, attributes
    )


def _classify_eggs(normalized_name, tokens):
    if not {"egg", "eggs"} & tokens:
        return None

    if {"boiled", "peeled", "quail"} & tokens:
        return _unknown_classification()

    attributes = _extract_attributes(normalized_name)

    if "liquid" in tokens or "egg whites" in normalized_name:
        comparison_group = "liquid_egg_product"
        product_form = "liquid"
    elif "extra large" in normalized_name or "xl" in tokens:
        comparison_group = "extra_large_eggs"
        product_form = "shell"
    elif "jumbo" in tokens:
        comparison_group = "jumbo_eggs"
        product_form = "shell"
    elif "large" in tokens:
        comparison_group = "large_eggs"
        product_form = "shell"
    elif "medium" in tokens:
        comparison_group = "medium_eggs"
        product_form = "shell"
    elif "small" in tokens:
        comparison_group = "small_eggs"
        product_form = "shell"
    else:
        comparison_group = "shell_eggs_unspecified_size"
        product_form = "shell"

    return _classification(
        "dairy", "eggs", comparison_group, product_form, attributes
    )


def _classify_produce(normalized_name, tokens):
    attributes = _extract_attributes(normalized_name)

    if {"apple", "apples"} & tokens:
        excluded_forms = {
            "cereal",
            "cider",
            "cookie",
            "cookies",
            "dried",
            "juice",
            "pie",
            "sauce",
        }

        if excluded_forms & tokens:
            return None

        product_form = "bagged" if {"bag", "bagged"} & tokens else "loose"
        return _classification(
            "produce", "apples", "fresh_apples", product_form, attributes
        )

    if {"banana", "bananas", "plantain", "plantains"} & tokens:
        excluded_forms = {
            "blend",
            "bread",
            "cake",
            "cakes",
            "cereal",
            "cereals",
            "chips",
            "chocolate",
            "cookie",
            "cookies",
            "covered",
            "dried",
            "dessert",
            "desserts",
            "custard",
            "pudding",
            "milkshake",
            "fried",
            "cooked",
            "flavor",
            "flavour",
            "flavored",
            "flavoured",
            "frozen",
            "iqf",
            "leaf",
            "muffin",
            "muffins",
            "puff",
            "puffs",
            "puree",
            "slice",
            "sliced",
            "smoothie",
            "steamed",
            "yogurt",
            "yoghurt",
        }

        if excluded_forms & tokens or "ice cream" in normalized_name:
            return _unknown_classification()

        product_form = "bagged" if {"bag", "bagged"} & tokens else "loose"
        if {"plantain", "plantains"} & tokens:
            return _classification(
                "produce", "plantains", "fresh_plantains", product_form, attributes
            )
        comparison_group = (
            "cooking_bananas" if "cooking" in tokens else "fresh_bananas"
        )
        return _classification(
            "produce", "bananas", comparison_group, product_form, attributes
        )

    return None


def _classify_cheese(normalized_name, tokens):
    cheese_terms = {
        "cheese", "cheddar", "mozzarella", "swiss", "marble"
    }

    if not cheese_terms & tokens:
        return None

    if {
        "cracker", "crackers", "chips", "crisps", "puffs", "sauce",
        "soup", "dip", "sandwich", "sandwiches", "dessert", "desserts",
    } & tokens or "cheese pizza" in normalized_name:
        return _unknown_classification()

    attributes = _extract_attributes(normalized_name)

    if "processed" in tokens:
        comparison_group = "processed_cheese"
    elif "cream cheese" in normalized_name:
        comparison_group = "cream_cheese"
    elif "cottage cheese" in normalized_name:
        comparison_group = "cottage_cheese"
    elif "cheddar" in tokens:
        comparison_group = "cheddar_cheese"
    elif "mozzarella" in tokens:
        comparison_group = "mozzarella_cheese"
    elif "swiss" in tokens:
        comparison_group = "swiss_cheese"
    elif "marble" in tokens:
        comparison_group = "marble_cheese"
    else:
        comparison_group = "cheese_unspecified_type"

    if "ball" in tokens and "mozzarella" in tokens:
        product_form = "ball"
    elif {"shred", "shreds", "shredded", "grated"} & tokens:
        product_form = "shredded"
    elif {"slice", "slices", "sliced"} & tokens:
        product_form = "sliced"
    elif {"block", "bar"} & tokens:
        product_form = "block"
    elif {"spread", "tub"} & tokens:
        product_form = "spread"
    elif {"string", "strings"} & tokens:
        product_form = "string"
    else:
        product_form = "unspecified"

    return _classification(
        "dairy", "cheese", comparison_group, product_form, attributes
    )


def classify_product(product_name):
    normalized_name = normalize_product_name(product_name)

    if not normalized_name:
        return _unknown_classification()

    tokens = set(normalized_name.split())
    classifiers = (
        _classify_chicken_breast,
        _classify_bread,
        _classify_pasta,
        _classify_eggs,
        _classify_produce,
        _classify_cheese,
    )

    for classifier in classifiers:
        result = classifier(normalized_name, tokens)

        if result is not None:
            return result

    return _unknown_classification()


def expected_standardized_unit(comparison_group):
    """Return the comparison unit required for a supported product group."""
    return EXPECTED_STANDARDIZED_UNITS.get(comparison_group)
