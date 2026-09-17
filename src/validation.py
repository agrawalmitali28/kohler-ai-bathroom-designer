"""
validation.py
---------------
PHASE 3B STATUS: Implemented.

Purpose:
    Sit between src/llm_parser.py and src/recommendation_engine.py:

        LLM parser output (structured requirements)
              -> validate_requirements()               [field-level correctness]
              -> check_requirements_completeness()      [is it enough to run the engine?]
              -> requirements_to_recommendation_args()  [translate to engine kwargs]
              -> recommendation_engine.recommend_products(**args)

    This module NEVER selects products, calculates prices, or touches
    dimensions/specifications — that all remains the exclusive
    responsibility of recommendation_engine.py. This file only validates
    and translates the *customer's requirements*.

------------------------------------------------------------------------
WHY THIS FILE LOOKS DIFFERENT FROM THE PHASE 1 PLACEHOLDER
------------------------------------------------------------------------
The original Phase 1 placeholder sketched `validate_budget()` and
`validate_spatial_fit()` — checking whether a *selected set of products*
fits the budget/room. That responsibility already lives inside
recommendation_engine.py (calculate_budget_efficiency_score,
check_product_space, calculate_spatial_score), which was built and
verified in Phase 2. Re-implementing it here would duplicate logic and
risk the two disagreeing. Phase 3's actual gap — confirmed during the
Phase 3 read-only inspection — is a different one: nothing validates or
adapts the LLM's raw structured output before it reaches the engine.
That's what this file now does instead.
------------------------------------------------------------------------

------------------------------------------------------------------------
WHAT THE ENGINE ACTUALLY REQUIRES (verified empirically, not assumed)
------------------------------------------------------------------------
Before writing check_requirements_completeness(), each case below was
tested directly against the real recommend_products():
    - length_ft=None            -> crashes: TypeError (None * 12.0)
    - width_ft=None             -> crashes: TypeError (None * 12.0)
    - budget=None                -> crashes: TypeError (int <= None)
    - required_categories=[]     -> crashes: ZeroDivisionError
                                     (averaging scores over zero products)
    - required_categories=None   -> crashes: TypeError (not iterable)
    - theme=None                 -> works fine (falls back to a neutral
                                     style ranking)
    - preferences=None           -> works fine (defaults to {})

So length_ft, width_ft, budget_inr, and a non-empty required_categories
are CRITICAL. theme and preferences are optional.
------------------------------------------------------------------------
"""

# The six categories recommendation_engine.py and the real KOHLER catalog
# expect, exactly as defined in src/llm_parser.py's ALLOWED_CATEGORIES.
# Duplicated here (rather than imported from llm_parser.py) on purpose,
# to keep this module runnable standalone (`python3 src/validation.py`)
# without depending on Python's package/import path setup. If you ever
# change the category vocabulary, update it in both files.
ALLOWED_CATEGORIES = [
    "Toilet",
    "Smart Toilet",
    "Faucet",
    "Shower",
    "Vanity",
    "Washbasin",
]

REQUIRED_TOP_LEVEL_KEYS = [
    "length_ft", "width_ft", "budget_inr", "theme",
    "required_categories", "preferences",
]

# Fields the recommendation engine cannot run without (see module
# docstring above for how this was determined).
CRITICAL_NUMERIC_FIELDS = ["length_ft", "width_ft", "budget_inr"]

# Human-friendly labels for missing-field messages shown to the customer.
FIELD_LABELS = {
    "length_ft": "bathroom length",
    "width_ft": "bathroom width",
    "budget_inr": "budget",
    "required_categories": "at least one product type you need (e.g. toilet, faucet, shower)",
}


class RequirementsValidationError(Exception):
    """Raised when a requirements dict has a malformed field (wrong type
    or an invalid value like a negative number). We never silently
    invent or coerce a value here — a genuinely bad input raises this
    instead."""


# ----------------------------------------------------------------------
# Step 1: field-level validation / normalization
# ----------------------------------------------------------------------

def validate_requirements(requirements: dict) -> dict:
    """
    Validate and normalize a structured requirements dict — typically
    the output of llm_parser.parse_requirements(), but this function
    makes no assumptions about where it came from and re-validates
    everything itself.

    This ONLY checks field-level correctness (right type, right value
    range). It does NOT decide whether there's enough information to
    call the recommendation engine — see check_requirements_completeness()
    for that.

    Args:
        requirements: A dict expected to have the shape:
            {
                "length_ft": number or None,
                "width_ft": number or None,
                "budget_inr": number or None,
                "theme": string or None,
                "required_categories": list,
                "preferences": {
                    "color": string or None,
                    "water_efficiency_keyword": string or None,
                    "feature_keywords": list,
                },
            }

    Returns:
        A clean, normalized dict with the same shape.

    Raises:
        RequirementsValidationError: if a field has the wrong type or an
            invalid value (e.g. a negative budget). Never invents a
            replacement value — always raises instead.
    """
    if not isinstance(requirements, dict):
        raise RequirementsValidationError("requirements must be a dict.")

    missing_keys = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in requirements]
    if missing_keys:
        raise RequirementsValidationError(
            f"requirements is missing required field(s): {', '.join(missing_keys)}."
        )

    # Room dimensions must be > 0 if given — a bathroom can't be 0 or
    # negative feet across. (Budget uses a separate >= 0 check below,
    # since a budget of exactly 0 is a valid, if unhelpful, input.)
    length_ft = _validate_positive_number_or_none(requirements["length_ft"], "length_ft")
    width_ft = _validate_positive_number_or_none(requirements["width_ft"], "width_ft")
    budget_inr = _validate_nonnegative_number_or_none(requirements["budget_inr"], "budget_inr")

    theme = requirements["theme"]
    if theme is not None and not isinstance(theme, str):
        raise RequirementsValidationError("theme must be a string or null.")

    required_categories = requirements["required_categories"]
    if not isinstance(required_categories, list):
        raise RequirementsValidationError("required_categories must be a list.")

    # Keep only the six allowed categories, drop anything else, and
    # de-duplicate while preserving the order they were given in.
    seen = set()
    normalized_categories = []
    for category in required_categories:
        if isinstance(category, str) and category in ALLOWED_CATEGORIES and category not in seen:
            normalized_categories.append(category)
            seen.add(category)

    preferences = requirements["preferences"]
    if preferences is None:
        preferences = {}
    if not isinstance(preferences, dict):
        raise RequirementsValidationError("preferences must be an object or null.")

    color = preferences.get("color")
    if color is not None and not isinstance(color, str):
        raise RequirementsValidationError("preferences.color must be a string or null.")

    water_efficiency_keyword = preferences.get("water_efficiency_keyword")
    if water_efficiency_keyword is not None and not isinstance(water_efficiency_keyword, str):
        raise RequirementsValidationError(
            "preferences.water_efficiency_keyword must be a string or null."
        )

    feature_keywords = preferences.get("feature_keywords", [])
    if feature_keywords is None:
        feature_keywords = []
    if not isinstance(feature_keywords, list) or not all(isinstance(f, str) for f in feature_keywords):
        raise RequirementsValidationError(
            "preferences.feature_keywords must be a list of strings."
        )

    return {
        "length_ft": length_ft,
        "width_ft": width_ft,
        "budget_inr": budget_inr,
        "theme": theme,
        "required_categories": normalized_categories,
        "preferences": {
            "color": color,
            "water_efficiency_keyword": water_efficiency_keyword,
            "feature_keywords": feature_keywords,
        },
    }


def _validate_positive_number_or_none(value, field_name: str):
    """Confirm value is None, or a number strictly greater than 0."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequirementsValidationError(f"{field_name} must be a number or null.")
    if value <= 0:
        raise RequirementsValidationError(f"{field_name} must be greater than 0.")
    return value


def _validate_nonnegative_number_or_none(value, field_name: str):
    """Confirm value is None, or a number >= 0."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequirementsValidationError(f"{field_name} must be a number or null.")
    if value < 0:
        raise RequirementsValidationError(f"{field_name} must not be negative.")
    return value


# ----------------------------------------------------------------------
# Step 2: is there enough information to call the engine?
# ----------------------------------------------------------------------

def check_requirements_completeness(clean_requirements: dict) -> dict:
    """
    Decide whether `clean_requirements` (already passed through
    validate_requirements()) has enough information for
    recommendation_engine.recommend_products() to run without crashing.

    This is deliberately separate from validate_requirements(): an
    incomplete request (e.g. "I want a smart toilet" with no budget or
    dimensions) is not malformed data — it's a normal, expected customer
    input that a future UI should politely ask follow-up questions
    about, not treat as an error.

    Args:
        clean_requirements: Output of validate_requirements().

    Returns:
        {"valid": True, "missing_fields": [], "message": "..."} if the
        engine can be called, or
        {"valid": False, "missing_fields": [...], "message": "..."} with
        a customer-facing explanation of what's still needed.
    """
    missing_fields = [
        field for field in CRITICAL_NUMERIC_FIELDS
        if clean_requirements.get(field) is None
    ]

    if not clean_requirements.get("required_categories"):
        missing_fields.append("required_categories")

    if missing_fields:
        friendly_names = [FIELD_LABELS.get(field, field) for field in missing_fields]
        return {
            "valid": False,
            "missing_fields": missing_fields,
            "message": "Please provide: " + "; ".join(friendly_names) + ".",
        }

    return {
        "valid": True,
        "missing_fields": [],
        "message": "All required information is present.",
    }


# ----------------------------------------------------------------------
# Step 3: adapter — translate requirements into recommend_products() kwargs
# ----------------------------------------------------------------------

def requirements_to_recommendation_args(clean_requirements: dict) -> dict:
    """
    Translate a validated, complete requirements dict into the exact
    keyword arguments recommendation_engine.recommend_products() expects.

    This function does ONLY translation — field renaming and reshaping.
    It does not select products, does not calculate prices or scores,
    and does not touch product dimensions. All of that remains inside
    recommendation_engine.py.

    Note on budget: the parser's `budget_inr` is passed straight through
    as `budget` with no unit conversion, no currency formatting, and no
    invented exchange rate — the recommendation engine already treats
    price/budget as a plain number in the catalog's native currency (INR).

    Note on dimensions: `length_ft`/`width_ft` are passed straight
    through unchanged. The engine does its own feet-to-inches conversion
    internally (see recommendation_engine.feet_to_inches) — this adapter
    does not duplicate that conversion.

    Args:
        clean_requirements: Output of validate_requirements(). Should
            already have passed check_requirements_completeness() with
            valid=True — this function re-checks that as a safety net
            and raises rather than silently calling the engine with
            missing data.

    Returns:
        A dict of kwargs ready to pass as
        recommend_products(**requirements_to_recommendation_args(...)).

    Raises:
        RequirementsValidationError: if the given requirements are not
            actually complete enough to call the engine.
    """
    completeness = check_requirements_completeness(clean_requirements)
    if not completeness["valid"]:
        raise RequirementsValidationError(
            "Cannot build recommendation engine arguments — " + completeness["message"]
        )

    preferences = clean_requirements["preferences"]

    return {
        "length_ft": clean_requirements["length_ft"],
        "width_ft": clean_requirements["width_ft"],
        "budget": clean_requirements["budget_inr"],  # name change only: budget_inr -> budget
        "theme": clean_requirements["theme"],
        "required_categories": clean_requirements["required_categories"],
        "preferences": {
            "color": preferences["color"],
            "water_efficiency_keyword": preferences["water_efficiency_keyword"],
            "feature_keywords": preferences["feature_keywords"],
        },
    }


# ----------------------------------------------------------------------
# Simple demonstration / test section
# ----------------------------------------------------------------------
# Run this file directly to see example output:
#     python3 src/validation.py
# No Streamlit and no LLM API call required.
# ----------------------------------------------------------------------

def _print_section(title: str) -> None:
    print("=" * 70)
    print(title)
    print("=" * 70)


if __name__ == "__main__":
    import json

    # --- TEST 1: complete, valid requirements -> valid adapter args ---
    _print_section("TEST 1: complete requirements -> recommendation engine args")
    test_1_input = {
        "length_ft": 8,
        "width_ft": 6,
        "budget_inr": 200000,
        "theme": "Minimalist Modern",
        "required_categories": ["Smart Toilet", "Faucet", "Shower", "Vanity"],
        "preferences": {"color": "White", "water_efficiency_keyword": "water saving", "feature_keywords": []},
    }
    clean_1 = validate_requirements(test_1_input)
    completeness_1 = check_requirements_completeness(clean_1)
    args_1 = requirements_to_recommendation_args(clean_1)
    print("Validated:", json.dumps(clean_1, indent=2))
    print("Completeness:", completeness_1)
    print("Engine args:", json.dumps(args_1, indent=2))
    print()

    # --- TEST 2: complete requirements, run through the REAL engine end-to-end ---
    _print_section("TEST 2: full pipeline -> validation -> adapter -> REAL recommendation_engine.py")
    test_2_input = {
        "length_ft": 5,
        "width_ft": 4,
        "budget_inr": 80000,
        "theme": "Modern",
        "required_categories": ["Toilet", "Washbasin", "Faucet"],
        "preferences": {"color": None, "water_efficiency_keyword": None, "feature_keywords": []},
    }
    clean_2 = validate_requirements(test_2_input)
    completeness_2 = check_requirements_completeness(clean_2)
    print("Completeness:", completeness_2)
    if completeness_2["valid"]:
        args_2 = requirements_to_recommendation_args(clean_2)
        print("Engine args:", json.dumps(args_2, indent=2))

        # Import here (not at module top) so this file has zero hard
        # dependency on recommendation_engine.py just to be imported —
        # only this end-to-end demo needs it.
        from recommendation_engine import recommend_products, load_products
        products_df = load_products()
        engine_result = recommend_products(products_df=products_df, **args_2)
        print(f"\nEngine status: {engine_result['status']}")
        print(f"Engine message: {engine_result['message']}")
        if engine_result["bundles"]:
            top = engine_result["bundles"][0]
            print(f"Top bundle compatibility score: {top['compatibility_score']}")
            for product in top["products"]:
                print(f"  [{product['category']}] {product['product_name']} (price: {product['price']})")
    print()

    # --- TEST 3: incomplete request (no dimensions, no budget) ---
    _print_section("TEST 3: incomplete request -> should report missing fields, not crash")
    test_3_input = {
        "length_ft": None,
        "width_ft": None,
        "budget_inr": None,
        "theme": "Modern",
        "required_categories": ["Smart Toilet"],
        "preferences": {"color": None, "water_efficiency_keyword": None, "feature_keywords": []},
    }
    clean_3 = validate_requirements(test_3_input)
    completeness_3 = check_requirements_completeness(clean_3)
    print("Completeness result:", json.dumps(completeness_3, indent=2))
    try:
        requirements_to_recommendation_args(clean_3)
        print("UNEXPECTED: adapter did not raise for incomplete requirements")
    except RequirementsValidationError as exc:
        print(f"Adapter correctly refused incomplete requirements: {exc}")
    print()

    # --- TEST 4: invalid category should be dropped, not crash ---
    _print_section("TEST 4: invalid category ('Bathtub') should be removed")
    test_4_input = {
        "length_ft": 8,
        "width_ft": 6,
        "budget_inr": 150000,
        "theme": "Modern",
        "required_categories": ["Toilet", "Bathtub"],
        "preferences": {"color": None, "water_efficiency_keyword": None, "feature_keywords": []},
    }
    clean_4 = validate_requirements(test_4_input)
    print("Normalized required_categories:", clean_4["required_categories"])
    assert "Bathtub" not in clean_4["required_categories"], "Bathtub should have been removed!"
    assert clean_4["required_categories"] == ["Toilet"], "Only Toilet should remain!"
    print("Confirmed: 'Bathtub' was removed, 'Toilet' was kept.")
    print()

    # --- TEST 5: negative budget should be rejected ---
    _print_section("TEST 5: negative budget should raise RequirementsValidationError")
    test_5_input = {
        "length_ft": 8,
        "width_ft": 6,
        "budget_inr": -5000,
        "theme": "Modern",
        "required_categories": ["Toilet"],
        "preferences": {"color": None, "water_efficiency_keyword": None, "feature_keywords": []},
    }
    try:
        validate_requirements(test_5_input)
        print("UNEXPECTED: negative budget was NOT rejected")
    except RequirementsValidationError as exc:
        print(f"Correctly rejected negative budget: {exc}")
