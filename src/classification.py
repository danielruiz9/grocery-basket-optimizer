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
)


PASTA_TERMS = {
    "pasta",
    "spaghetti",
    "penne",
    "macaroni",
    "fusilli",
    "linguine",
    "fettuccine",
    "ravioli",
    "tortellini",
    "noodle",
    "noodles",
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

    attributes = _extract_attributes(normalized_name)

    if "breaded" in tokens:
        comparison_group = "breaded_chicken_breast"
        product_form = "breaded"
    elif {"strip", "strips", "tender", "tenders"} & tokens:
        comparison_group = "chicken_breast_strips"
        product_form = "strips"
    elif "bone in" in normalized_name:
        comparison_group = "chicken_breast_bone_in"
        product_form = "whole"
        attributes.append("bone_in")
    elif "boneless" in tokens:
        comparison_group = "chicken_breast_boneless"
        product_form = "whole"
    else:
        comparison_group = "chicken_breast"
        product_form = "whole"

    return _classification(
        "meat",
        "chicken_breast",
        comparison_group,
        product_form,
        attributes
    )


def _classify_bread(normalized_name, tokens):
    attributes = _extract_attributes(normalized_name)

    if {"bagel", "bagels"} & tokens:
        return _classification(
            "bakery", "bread", "bagels", "bagel", attributes
        )

    if {"baguette", "baguettes"} & tokens:
        return _classification(
            "bakery", "bread", "baguettes", "baguette", attributes
        )

    if {"bread", "loaf", "loaves"} & tokens:
        return _classification(
            "bakery", "bread", "sliced_bread", "sliced_loaf", attributes
        )

    return None


def _classify_pasta(normalized_name, tokens):
    if not PASTA_TERMS & tokens:
        return None

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

    attributes = _extract_attributes(normalized_name)

    if "liquid" in tokens or "egg whites" in normalized_name:
        comparison_group = "liquid_egg_product"
        product_form = "liquid"
    elif "extra large" in normalized_name:
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
        excluded_forms = {"cider", "dried", "juice", "pie", "sauce"}

        if excluded_forms & tokens:
            return None

        product_form = "bagged" if {"bag", "bagged"} & tokens else "loose"
        return _classification(
            "produce", "apples", "fresh_apples", product_form, attributes
        )

    if {"banana", "bananas"} & tokens:
        excluded_forms = {"bread", "chips", "dried"}

        if excluded_forms & tokens:
            return None

        product_form = "bagged" if {"bag", "bagged"} & tokens else "loose"
        return _classification(
            "produce", "bananas", "fresh_bananas", product_form, attributes
        )

    return None


def _classify_cheese(normalized_name, tokens):
    cheese_terms = {
        "cheese", "cheddar", "mozzarella", "swiss", "marble"
    }

    if not cheese_terms & tokens:
        return None

    attributes = _extract_attributes(normalized_name)

    if "cream cheese" in normalized_name:
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

    if {"shred", "shreds", "shredded", "grated"} & tokens:
        product_form = "shredded"
    elif {"slice", "slices", "sliced"} & tokens:
        product_form = "sliced"
    elif "block" in tokens:
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
