"""
recommendation_engine.py
--------------------------
PHASE 2 STATUS: Implemented.

This module is 100% deterministic Python logic. It does NOT call any LLM.

Responsibilities (all handled here, never by the LLM):
    - Choosing which products go into a bundle
    - Calculating prices and remaining budget
    - Deciding whether a product/bundle is physically feasible for the room
    - Calculating the transparent "Compatibility Score" for each bundle

The LLM (added in a later phase) will only be responsible for turning a
customer's free-text sentence into structured inputs like `length_ft`,
`width_ft`, `budget`, `theme`, `required_categories`, and `preferences`.
Everything in this file works purely from those structured inputs.

------------------------------------------------------------------------
HOW SCORING WORKS ("Compatibility Score", 0-100)
------------------------------------------------------------------------
A bundle's Compatibility Score is a weighted average of four sub-scores,
each on a 0.0-1.0 scale before weighting:

    40% - Style match       (does each product's style suit the theme?)
    25% - Budget efficiency (how well is the budget used, without going over?)
    20% - Spatial fit       (does the bundle physically fit the room?)
    15% - Feature match     (does the bundle match optional preferences?)

This is intentionally simple and explainable — every number in the score
can be traced back to a rule in this file, which is important for the
"explain why this was recommended" requirement in a later phase.
------------------------------------------------------------------------
"""

import itertools
import os

import pandas as pd

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

# Path to the product catalog, relative to this file, so it works no
# matter where the app is launched from.
DEFAULT_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "products.csv"
)

# Scoring weights (must add up to 1.0)
STYLE_WEIGHT = 0.40
BUDGET_WEIGHT = 0.25
SPATIAL_WEIGHT = 0.20
FEATURE_WEIGHT = 0.15

# How many top style-matched candidates to keep per category before
# generating bundle combinations. Keeps combinations fast and manageable.
MAX_CANDIDATES_PER_CATEGORY = 3

# A very simple theme -> style lookup table.
# Styles come from the "style" column in data/products.csv:
#   Modern, Traditional, Contemporary, Industrial
# Each theme maps to a list of styles, best match first.
THEME_STYLE_MAP = {
    "minimalist modern": ["Modern", "Contemporary"],
    "modern": ["Modern", "Contemporary"],
    "modern minimalist": ["Modern", "Contemporary"],
    "contemporary": ["Contemporary", "Modern"],
    "contemporary chic": ["Contemporary", "Modern"],
    "classic luxury": ["Traditional", "Contemporary"],
    "classic": ["Traditional"],
    "traditional": ["Traditional"],
    "luxury": ["Contemporary", "Traditional"],
    "industrial": ["Industrial"],
    "industrial loft": ["Industrial"],
}

# If the room is tightly packed with product footprints, it becomes hard
# to move around. These thresholds define the "comfortable" vs "crowded"
# range of floor space used, as a fraction of total room area.
SPATIAL_COMFORTABLE_RATIO = 0.35   # at or below this -> full spatial score
SPATIAL_TOO_CROWDED_RATIO = 0.75   # at or above this -> spatial score of 0


# ----------------------------------------------------------------------
# Step 1: Loading data
# ----------------------------------------------------------------------

def load_products(csv_path: str = DEFAULT_CSV_PATH) -> pd.DataFrame:
    """
    Load the product catalog from a CSV file.

    Args:
        csv_path: Path to products.csv. Defaults to data/products.csv.

    Returns:
        A pandas DataFrame with the product catalog.
    """
    df = pd.read_csv(csv_path)
    return df


# ----------------------------------------------------------------------
# Step 2: Unit conversion
# ----------------------------------------------------------------------

def feet_to_inches(value_ft: float) -> float:
    """Convert a measurement in feet to inches."""
    return value_ft * 12.0


# ----------------------------------------------------------------------
# Step 3 & 4: Category + basic size filtering
# ----------------------------------------------------------------------

def get_candidates_by_category(
    products_df: pd.DataFrame,
    required_categories: list,
    room_length_in: float,
    room_width_in: float,
) -> dict:
    """
    For each required category, find the products that:
      (a) belong to that category, and
      (b) are not CONFIRMED to be too big for the room (see check_product_space).

    Note on missing dimensions: check_product_space() returns None (not
    True/False) when a product's width_in/depth_in is unknown. We only
    want to drop products that are a CONFIRMED spatial conflict (False).
    A product with unknown dimensions (None) is kept — we have no basis
    to reject it, and rejecting it would wrongly eliminate every faucet
    and showerhead in the real KOHLER catalog, which don't have a
    floor-footprint spec the way a toilet or vanity does.

    Args:
        products_df: Full product catalog.
        required_categories: List of category names, e.g. ["Faucet", "Shower"].
        room_length_in: Room length in inches.
        room_width_in: Room width in inches.

    Returns:
        A dict like {"Faucet": DataFrame, "Shower": DataFrame, ...}.
        A category with no matching products, or where every matching
        product is a CONFIRMED spatial conflict, maps to an empty DataFrame.
    """
    candidates_by_category = {}

    for category in required_categories:
        # Case-insensitive match against the "category" column.
        category_df = products_df[
            products_df["category"].str.lower() == category.lower()
        ].copy()

        # Only drop products that are a CONFIRMED conflict (result is
        # exactly False). Keep both True (confirmed fit) and None
        # (unknown/not evaluable, e.g. a faucet with no recorded
        # width_in/depth_in) — "unknown" must never be treated the same
        # as "doesn't fit".
        fits_mask = category_df.apply(
            lambda row: check_product_space(row, room_length_in, room_width_in) is not False,
            axis=1,
        )
        category_df = category_df[fits_mask]

        candidates_by_category[category] = category_df

    return candidates_by_category


def check_product_space(product: pd.Series, room_length_in: float, room_width_in: float) -> bool | None:
    """
    Check whether a single product's footprint can physically fit inside
    the room's rectangular boundary (ignoring where other products go —
    that level of detail belongs to the layout engine in a later phase).

    The product is allowed to be placed in either orientation (rotated
    90 degrees), since a faucet or vanity could be placed along either wall.

    --------------------------------------------------------------------
    WHY THIS RETURNS THREE POSSIBLE VALUES (True / False / None)
    --------------------------------------------------------------------
    Our real KOHLER catalog intentionally leaves width_in/depth_in blank
    for products where a floor footprint doesn't apply or wasn't verified
    (e.g. a faucet or showerhead mounted on a wall). Blank values load as
    NaN (Not a Number) in pandas, and in Python/pandas *any* comparison
    against NaN (e.g. `NaN <= 60`) evaluates to False — never True. If we
    treated that False as "does not fit", every faucet and showerhead
    would be silently rejected from every room, no matter how large the
    room is. That's a false negative, not a real spatial conflict — we
    simply don't have a floor footprint to check for these products.

    So this function distinguishes "doesn't fit" from "we don't have
    enough information to know":
        True  -> both dimensions are known, and the product fits the
                 room in at least one orientation.
        False -> both dimensions are known, and the product does NOT
                 fit the room in either orientation. This is a real,
                 confirmed spatial conflict.
        None  -> width_in and/or depth_in is missing (NaN). We cannot
                 evaluate spatial fit at all, so we deliberately do NOT
                 say "doesn't fit". Callers should treat None as "don't
                 reject this product for space reasons" rather than as
                 a failure.
    --------------------------------------------------------------------

    Args:
        product: A row from the product catalog (must have width_in, depth_in).
        room_length_in: Room length in inches.
        room_width_in: Room width in inches.

    Returns:
        True if both dimensions are known and it fits in at least one
        orientation; False if both dimensions are known and it fits in
        neither orientation; None if a dimension is missing and fit
        cannot be evaluated.
    """
    width = product["width_in"]
    depth = product["depth_in"]

    # Missing dimension -> we simply don't know. Don't guess, don't reject.
    if pd.isna(width) or pd.isna(depth):
        return None

    fits_as_is = (width <= room_length_in) and (depth <= room_width_in)
    fits_rotated = (depth <= room_length_in) and (width <= room_width_in)

    return bool(fits_as_is or fits_rotated)


# ----------------------------------------------------------------------
# Step 5: Theme / style matching
# ----------------------------------------------------------------------

def get_theme_style_ranking(theme: str, known_styles: list) -> list:
    """
    Translate a free-text theme (e.g. "Minimalist Modern") into a ranked
    list of catalog "style" values, best match first.

    Falls back to a simple substring match against the theme text, and
    finally to a neutral ranking (all known styles) if nothing matches,
    so the engine never crashes on an unrecognized theme.

    Args:
        theme: Free-text theme name from the customer.
        known_styles: List of style values actually present in the catalog.

    Returns:
        Ordered list of style strings, best match first.
    """
    theme_key = (theme or "").strip().lower()

    if theme_key in THEME_STYLE_MAP:
        return THEME_STYLE_MAP[theme_key]

    # Fall back to substring matching, e.g. theme contains "modern".
    matched_styles = [
        style for style in known_styles if style.lower() in theme_key
    ]
    if matched_styles:
        return matched_styles

    # No match at all -> neutral ranking, every style scores the same.
    return list(known_styles)


def calculate_style_score(product: pd.Series, theme_style_ranking: list) -> float:
    """
    Score how well one product's style matches the requested theme.

    Args:
        product: A row from the product catalog (must have "style").
        theme_style_ranking: Output of get_theme_style_ranking().

    Returns:
        A score from 0.0 to 1.0:
            1.0 -> best style match for the theme
            0.7 -> a secondary/acceptable style match
            0.3 -> no real match, but still usable
    """
    product_style = product["style"]

    if not theme_style_ranking:
        return 0.5  # no theme info at all -> neutral score

    if product_style == theme_style_ranking[0]:
        return 1.0
    elif product_style in theme_style_ranking:
        return 0.7
    else:
        return 0.3


# ----------------------------------------------------------------------
# Step 6: Preference / feature matching
# ----------------------------------------------------------------------

def calculate_feature_score(product: pd.Series, preferences: dict) -> float:
    """
    Score how well one product matches optional user preferences.

    Supported preference keys (all optional):
        preferences["color"]: preferred color, e.g. "White"
        preferences["water_efficiency_keyword"]: substring to look for in
            the water_efficiency column, e.g. "Ultra"
        preferences["feature_keywords"]: list of substrings to look for in
            the smart_features column, e.g. ["heated seat", "app control"]

    Args:
        product: A row from the product catalog.
        preferences: Dict of optional preferences. Can be empty or None.

    Returns:
        A score from 0.0 to 1.0. Returns 1.0 (neutral/full score) if the
        user gave no preferences at all, since there's nothing to fail.
    """
    if not preferences:
        return 1.0

    checks = []

    if preferences.get("color"):
        wanted_color = preferences["color"].strip().lower()
        actual_color = str(product.get("color", "")).strip().lower()
        checks.append(wanted_color == actual_color)

    if preferences.get("water_efficiency_keyword"):
        keyword = preferences["water_efficiency_keyword"].strip().lower()
        actual_value = str(product.get("water_efficiency", "")).lower()
        checks.append(keyword in actual_value)

    if preferences.get("feature_keywords"):
        actual_features = str(product.get("smart_features", "")).lower()
        for keyword in preferences["feature_keywords"]:
            checks.append(keyword.strip().lower() in actual_features)

    if not checks:
        return 1.0  # preferences dict had no usable keys -> neutral score

    matches = sum(1 for passed in checks if passed)
    return matches / len(checks)


# ----------------------------------------------------------------------
# Step 8/9/10: Budget math
# ----------------------------------------------------------------------

def calculate_budget_efficiency_score(total_price: float, budget: float) -> float:
    """
    Score how efficiently a bundle uses the available budget, assuming it
    is already confirmed to be within budget.

    A bundle that uses more of the budget (without exceeding it) scores
    higher than one that leaves a lot of the budget unused, on the theory
    that the customer's stated budget reflects what they're comfortable
    spending on a complete bathroom.

    Args:
        total_price: Sum of prices for the bundle.
        budget: Customer's stated budget.

    Returns:
        A score from 0.0 to 1.0.
    """
    if budget <= 0:
        return 0.0

    utilization = total_price / budget
    return min(utilization, 1.0)


# ----------------------------------------------------------------------
# Step 4 (bundle-level) / spatial scoring
# ----------------------------------------------------------------------

def calculate_spatial_score(bundle_products: list, room_length_in: float, room_width_in: float) -> float:
    """
    Score how comfortably an entire bundle fits the room, based on total
    floor footprint used vs. total room area.

    This is a simple, deterministic approximation for Phase 2. The real
    2D layout engine (a later phase) will do actual placement/collision
    checks; this just prevents recommending bundles that would obviously
    overcrowd the room.

    --------------------------------------------------------------------
    HANDLING PRODUCTS WITH UNKNOWN (NaN) DIMENSIONS
    --------------------------------------------------------------------
    Some real KOHLER products (faucets, showerheads) have no recorded
    width_in/depth_in, because they don't occupy meaningful floor space
    the way a toilet or vanity does. Those products are EXCLUDED from
    the footprint total below — not counted as 0x0 (which would just
    happen to be the same number, but for the wrong conceptual reason)
    and not counted as an oversized product either. They simply don't
    contribute a floor footprint to sum, and the bundle is not penalized
    for including them. Only products with both dimensions known are
    added to total_footprint.
    --------------------------------------------------------------------

    Args:
        bundle_products: List of product dicts/rows in the bundle.
        room_length_in: Room length in inches.
        room_width_in: Room width in inches.

    Returns:
        A score from 0.0 to 1.0.
    """
    room_area = room_length_in * room_width_in
    if room_area <= 0:
        return 0.0

    # Only include products whose footprint is actually known. A product
    # with a missing width_in or depth_in is skipped here entirely (see
    # docstring above) rather than guessed at.
    total_footprint = sum(
        p["width_in"] * p["depth_in"]
        for p in bundle_products
        if not (pd.isna(p.get("width_in")) or pd.isna(p.get("depth_in")))
    )
    occupied_ratio = total_footprint / room_area

    if occupied_ratio <= SPATIAL_COMFORTABLE_RATIO:
        return 1.0
    if occupied_ratio >= SPATIAL_TOO_CROWDED_RATIO:
        return 0.0

    # Linearly scale between "comfortable" and "too crowded".
    span = SPATIAL_TOO_CROWDED_RATIO - SPATIAL_COMFORTABLE_RATIO
    over_by = occupied_ratio - SPATIAL_COMFORTABLE_RATIO
    return 1.0 - (over_by / span)


# ----------------------------------------------------------------------
# Step 11: Overall bundle scoring
# ----------------------------------------------------------------------

def calculate_bundle_score(
    bundle_products: list,
    budget: float,
    room_length_in: float,
    room_width_in: float,
    theme_style_ranking: list,
    preferences: dict,
) -> dict:
    """
    Calculate the full "Compatibility Score" breakdown for one bundle.

    Args:
        bundle_products: List of product dicts/rows making up the bundle.
        budget: Customer's stated budget.
        room_length_in: Room length in inches.
        room_width_in: Room width in inches.
        theme_style_ranking: Output of get_theme_style_ranking().
        preferences: Optional preferences dict (see calculate_feature_score).

    Returns:
        A dict with the total price, remaining budget, each sub-score,
        and the final weighted "compatibility_score" (0-100).
    """
    total_price = sum(p["price"] for p in bundle_products)
    remaining_budget = budget - total_price

    # Average the style and feature scores across all products in the bundle.
    style_scores = [calculate_style_score(p, theme_style_ranking) for p in bundle_products]
    avg_style_score = sum(style_scores) / len(style_scores)

    feature_scores = [calculate_feature_score(p, preferences) for p in bundle_products]
    avg_feature_score = sum(feature_scores) / len(feature_scores)

    budget_score = calculate_budget_efficiency_score(total_price, budget)
    spatial_score = calculate_spatial_score(bundle_products, room_length_in, room_width_in)

    weighted_total = (
        (avg_style_score * STYLE_WEIGHT)
        + (budget_score * BUDGET_WEIGHT)
        + (spatial_score * SPATIAL_WEIGHT)
        + (avg_feature_score * FEATURE_WEIGHT)
    )

    return {
        "total_price": round(total_price, 2),
        "remaining_budget": round(remaining_budget, 2),
        "style_score": round(avg_style_score, 3),
        "budget_score": round(budget_score, 3),
        "spatial_score": round(spatial_score, 3),
        "feature_score": round(avg_feature_score, 3),
        "compatibility_score": round(weighted_total * 100, 1),  # 0-100 scale
    }


# ----------------------------------------------------------------------
# Step 7: Generating candidate bundles
# ----------------------------------------------------------------------

def generate_bundles(
    candidates_by_category: dict,
    budget: float,
    max_candidates_per_category: int = MAX_CANDIDATES_PER_CATEGORY,
) -> list:
    """
    Generate candidate bundles (one product per required category) that
    stay within budget.

    To keep this simple and fast, only the top N candidates per category
    (as already sorted by the caller) are combined. With ~4 categories and
    3 candidates each, that's at most 3^4 = 81 combinations to check —
    fast, and easy to reason about.

    Args:
        candidates_by_category: Dict of {category: DataFrame}, already
            filtered and sorted best-first by the caller.
        budget: Customer's stated budget.
        max_candidates_per_category: How many top candidates per category
            to consider when building combinations.

    Returns:
        A list of bundles, where each bundle is a list of product dicts
        (one per required category), limited to those within budget.
    """
    category_names = list(candidates_by_category.keys())

    # Convert each category's DataFrame (top N rows) into a list of dicts.
    per_category_lists = []
    for category in category_names:
        top_rows = candidates_by_category[category].head(max_candidates_per_category)
        per_category_lists.append(top_rows.to_dict("records"))

    # If any required category has zero candidates, no bundle can be built.
    if any(len(lst) == 0 for lst in per_category_lists):
        return []

    valid_bundles = []
    for combination in itertools.product(*per_category_lists):
        total_price = sum(p["price"] for p in combination)
        if total_price <= budget:
            valid_bundles.append(list(combination))

    return valid_bundles


# ----------------------------------------------------------------------
# Step 12: Public entry point
# ----------------------------------------------------------------------

def recommend_products(
    length_ft: float,
    width_ft: float,
    budget: float,
    theme: str,
    required_categories: list,
    preferences: dict = None,
    products_df: pd.DataFrame = None,
    top_n: int = 3,
) -> dict:
    """
    Main entry point for Phase 2: given structured customer requirements,
    deterministically generate up to `top_n` valid product bundles.

    Args:
        length_ft: Bathroom length in feet.
        width_ft: Bathroom width in feet.
        budget: Customer's total budget (same currency/unit as the CSV
            "price" column — this prototype does not convert currencies).
        theme: Free-text aesthetic theme, e.g. "Minimalist Modern".
        required_categories: List of category names that must each be
            represented once in the bundle, e.g. ["Smart Toilet", "Faucet"].
        preferences: Optional dict of soft preferences (see
            calculate_feature_score for supported keys).
        products_df: Optional pre-loaded catalog DataFrame. If not given,
            it's loaded from data/products.csv.
        top_n: Maximum number of bundles to return.

    Returns:
        A dict:
        {
            "status": "ok" | "no_valid_bundles" | "missing_categories",
            "message": str,                     # human-readable summary
            "room_dims_in": {"length": .., "width": ..},
            "bundles": [                         # up to top_n, best first
                {
                    "products": [ {product row dict}, ... ],
                    "total_price": ...,
                    "remaining_budget": ...,
                    "compatibility_score": ...,
                    "score_breakdown": {...},
                },
                ...
            ],
        }
    """
    if preferences is None:
        preferences = {}

    if products_df is None:
        products_df = load_products()

    room_length_in = feet_to_inches(length_ft)
    room_width_in = feet_to_inches(width_ft)

    # --- Step A: find candidates per category, filtered by basic size fit ---
    candidates_by_category = get_candidates_by_category(
        products_df, required_categories, room_length_in, room_width_in
    )

    missing_categories = [
        category for category, df in candidates_by_category.items() if df.empty
    ]
    if missing_categories:
        return {
            "status": "missing_categories",
            "message": (
                "No products fit the room for these required categories: "
                f"{', '.join(missing_categories)}. Try a larger room, or "
                "remove/replace these categories."
            ),
            "room_dims_in": {"length": room_length_in, "width": room_width_in},
            "bundles": [],
        }

    # --- Step B: rank candidates within each category by style match, ---
    # --- then keep only the top few so bundle generation stays fast.  ---
    known_styles = sorted(products_df["style"].dropna().unique().tolist())
    theme_style_ranking = get_theme_style_ranking(theme, known_styles)

    ranked_candidates_by_category = {}
    for category, df in candidates_by_category.items():
        df = df.copy()
        df["_style_score"] = df.apply(
            lambda row: calculate_style_score(row, theme_style_ranking), axis=1
        )
        df = df.sort_values("_style_score", ascending=False)
        ranked_candidates_by_category[category] = df

    # --- Step C: generate candidate bundles that fit the budget ---
    candidate_bundles = generate_bundles(ranked_candidates_by_category, budget)

    if not candidate_bundles:
        cheapest_total = _cheapest_possible_total(ranked_candidates_by_category)
        shortfall = cheapest_total - budget
        return {
            "status": "no_valid_bundles",
            "message": (
                "No bundle fits within the given budget. The cheapest possible "
                f"combination of the requested categories costs approximately "
                f"{cheapest_total:,.2f}, which is {shortfall:,.2f} over the "
                f"budget of {budget:,.2f}. Try increasing the budget or "
                "reducing the number of required categories."
            ),
            "room_dims_in": {"length": room_length_in, "width": room_width_in},
            "bundles": [],
        }

    # --- Step D: score every valid bundle and keep the top N ---
    scored_bundles = []
    for bundle in candidate_bundles:
        score_breakdown = calculate_bundle_score(
            bundle, budget, room_length_in, room_width_in, theme_style_ranking, preferences
        )
        scored_bundles.append({
            "products": bundle,
            "total_price": score_breakdown["total_price"],
            "remaining_budget": score_breakdown["remaining_budget"],
            "compatibility_score": score_breakdown["compatibility_score"],
            "score_breakdown": score_breakdown,
        })

    scored_bundles.sort(key=lambda b: b["compatibility_score"], reverse=True)
    top_bundles = scored_bundles[:top_n]

    return {
        "status": "ok",
        "message": f"Found {len(scored_bundles)} valid bundle(s); returning the top {len(top_bundles)}.",
        "room_dims_in": {"length": room_length_in, "width": room_width_in},
        "bundles": top_bundles,
    }


def _cheapest_possible_total(ranked_candidates_by_category: dict) -> float:
    """
    Helper: find the cheapest possible bundle total (one cheapest product
    per category), used only to give a helpful message when nothing fits
    the budget.
    """
    total = 0.0
    for category, df in ranked_candidates_by_category.items():
        if df.empty:
            continue
        total += df["price"].min()
    return total


# ----------------------------------------------------------------------
# Simple demonstration / test section
# ----------------------------------------------------------------------
# Run this file directly to see example output:
#     python3 src/recommendation_engine.py
# ----------------------------------------------------------------------

def _print_bundle_result(test_name: str, result: dict) -> None:
    print("=" * 70)
    print(test_name)
    print("=" * 70)
    print(f"Status: {result['status']}")
    print(f"Message: {result['message']}")

    if not result["bundles"]:
        print("No bundles to show.\n")
        return

    for i, bundle in enumerate(result["bundles"], start=1):
        print(f"\n--- Bundle #{i} | Compatibility Score: {bundle['compatibility_score']} / 100 ---")
        for product in bundle["products"]:
            print(f"  [{product['category']}] {product['product_name']} "
                  f"(price: {product['price']})")
        print(f"  Total price: {bundle['total_price']}")
        print(f"  Remaining budget: {bundle['remaining_budget']}")
        breakdown = bundle["score_breakdown"]
        print(
            "  Score breakdown -> "
            f"style: {breakdown['style_score']}, "
            f"budget: {breakdown['budget_score']}, "
            f"spatial: {breakdown['spatial_score']}, "
            f"feature: {breakdown['feature_score']}"
        )
    print()


if __name__ == "__main__":
    products_df = load_products()

    # Test 1: the example scenario from the spec.
    result_1 = recommend_products(
        length_ft=8,
        width_ft=6,
        budget=200000,
        theme="Minimalist Modern",
        required_categories=["Smart Toilet", "Faucet", "Shower", "Vanity"],
        products_df=products_df,
    )
    _print_bundle_result("TEST 1: 8x6 ft | Budget 200,000 | Minimalist Modern", result_1)

    # Test 2: a small bathroom, which should still work since our catalog
    # includes compact products, but exercises the spatial scoring more.
    result_2 = recommend_products(
        length_ft=5,
        width_ft=4,
        budget=200000,
        theme="Minimalist Modern",
        required_categories=["Toilet", "Faucet", "Washbasin"],
        products_df=products_df,
    )
    _print_bundle_result("TEST 2: Small bathroom (5x4 ft)", result_2)

    # Test 3: a very low budget, which should trigger "no_valid_bundles".
    result_3 = recommend_products(
        length_ft=8,
        width_ft=6,
        budget=300,
        theme="Minimalist Modern",
        required_categories=["Smart Toilet", "Faucet", "Shower", "Vanity"],
        products_df=products_df,
    )
    _print_bundle_result("TEST 3: Very low budget (300)", result_3)

    # Test 4: a different theme, to show style scoring changes the ranking.
    result_4 = recommend_products(
        length_ft=8,
        width_ft=6,
        budget=200000,
        theme="Classic Luxury",
        required_categories=["Toilet", "Faucet", "Shower", "Vanity"],
        products_df=products_df,
    )
    _print_bundle_result("TEST 4: 8x6 ft | Budget 200,000 | Classic Luxury", result_4)
