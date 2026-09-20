"""
KOHLER AI Bathroom Designer & Planner
--------------------------------------
Main Streamlit entry point.

PHASE 3C STATUS: Implemented.

This file wires together the pipeline built in earlier phases. It does
NOT contain any of its own product-selection, pricing, or LLM-prompt
logic — it only calls out to the modules that already implement each
step, and displays the results:

    user's free-text description
        -> src.llm_parser.parse_requirements()             (Phase 3A)
        -> src.validation.validate_requirements()           (Phase 3B)
        -> src.validation.check_requirements_completeness() (Phase 3B)
        -> src.validation.requirements_to_recommendation_args() (Phase 3B)
        -> src.recommendation_engine.recommend_products()   (Phase 2)
        -> rendered bundles, here in this file

Real KOHLER product data always comes from data/products.csv via
recommendation_engine.load_products() — nothing here hard-codes a
product, price, or specification.
"""

import copy
import html
import os
import re
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from src.usd_parser import parse_usd_file, USDParserError
from src.architecture_adapter import architecture_to_requirements

from src.llm_parser import (
    parse_requirements,
    LLMParserError,
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseValidationError,
)
from src.validation import (
    validate_requirements,
    check_requirements_completeness,
    requirements_to_recommendation_args,
    RequirementsValidationError,
)
from src.recommendation_engine import recommend_products, load_products

load_dotenv()

EXAMPLE_REQUEST = (
    "I have an 8 by 6 feet bathroom, budget of 2 lakh. I want a modern "
    "minimalist bathroom with a smart toilet, water-saving products and "
    "a white finish."
)

# ----------------------------------------------------------------------
# DEMO_MODE
# ----------------------------------------------------------------------
# Off by default. When DEMO_MODE=true, the app skips the paid LLM call and
# uses a small deterministic local parser on the text entered in the UI.
# This keeps the demo usable without changing the real LLM path.
# ----------------------------------------------------------------------
DEMO_MODE = os.environ.get("DEMO_MODE", "false").strip().lower() == "true"


def parse_demo_requirements(user_text: str) -> dict:
    """Parse the demo's core requirements locally without calling an LLM."""
    text = user_text.strip()

    # Dimensions: supports "7 x 10 ft", "7 × 10 feet", "7 by 10 ft", etc.
    dimension_match = re.search(
        r"(?P<a>\d+(?:\.\d+)?)\s*(?:x|×|by)\s*"
        r"(?P<b>\d+(?:\.\d+)?)\s*(?:feet|foot|ft)?",
        text,
        flags=re.IGNORECASE,
    )

    length_ft = width_ft = None
    if dimension_match:
        length_ft = float(dimension_match.group("a"))
        width_ft = float(dimension_match.group("b"))

    # Budget: supports ₹1.5 lakh/lakhs/lac/lacs, 1.5 lakh, ₹150000, etc.
    budget_inr = None
    budget_match = re.search(
        r"(?:₹\s*)?(?P<amount>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>lakh|lakhs|lac|lacs|l)\b",
        text,
        flags=re.IGNORECASE,
    )
    if budget_match:
        budget_inr = float(budget_match.group("amount")) * 100000
    else:
        budget_match = re.search(
            r"(?:₹\s*)?(?P<amount>\d{1,3}(?:,\d{3})+|\d{5,7})(?!\s*(?:lakh|lakhs|lac|lacs|l)\b)",
            text,
            flags=re.IGNORECASE,
        )
        if budget_match:
            budget_inr = float(budget_match.group("amount").replace(",", ""))

    category_patterns = [
        ("Smart Toilet", r"\bsmart\s+toilet\b"),
        ("Toilet", r"\btoilet\b"),
        ("Washbasin", r"\bwashbasin\b|\bbasin\b|\bsink\b"),
        ("Faucet", r"\bfaucet\b|\btap\b"),
        ("Shower", r"\bshower\b"),
        ("Vanity", r"\bvanity\b"),
    ]
    required_categories = []
    for category, pattern in category_patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            required_categories.append(category)

    # If "smart toilet" was requested, don't duplicate it as a generic toilet.
    if "Smart Toilet" in required_categories and "Toilet" in required_categories:
        required_categories.remove("Toilet")

    style_keywords = [
        "modern", "minimalist", "contemporary", "classic",
        "traditional", "luxury", "elegant", "sophisticated",
    ]
    theme = next(
        (
            keyword.title()
            for keyword in style_keywords
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE)
        ),
        None,
    )

    color_keywords = [
        "matte black", "polished chrome", "black", "dark", "white",
        "chrome", "brass", "gold", "silver",
    ]
    color = next(
        (
            keyword.title()
            for keyword in color_keywords
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE)
        ),
        None,
    )

    water_efficiency_keyword = None
    if re.search(
        r"water[-\s]?efficient|water[-\s]?saving|eco[-\s]?friendly|eco",
        text,
        re.IGNORECASE,
    ):
        water_efficiency_keyword = "water-efficient"

    feature_keywords = []
    for keyword, pattern in [
        ("smart", r"\bsmart\b"),
        ("dual-flush", r"dual[-\s]?flush"),
        ("wall-hung", r"wall[-\s]?hung"),
    ]:
        if re.search(pattern, text, flags=re.IGNORECASE):
            feature_keywords.append(keyword)

    return {
        "length_ft": length_ft,
        "width_ft": width_ft,
        "budget_inr": budget_inr,
        "theme": theme,
        "required_categories": required_categories,
        "preferences": {
            "color": color,
            "water_efficiency_keyword": water_efficiency_keyword,
            "feature_keywords": feature_keywords,
        },
    }


# Human-friendly labels for the "please provide" message.
FIELD_LABELS = {
    "length_ft": "Bathroom length",
    "width_ft": "Bathroom width",
    "budget_inr": "Budget",
    "required_categories": "At least one product type you need (e.g. toilet, faucet, shower)",
}


# ----------------------------------------------------------------------
# Catalog loading (cacheable — the CSV doesn't change during a session)
# ----------------------------------------------------------------------

@st.cache_data
def get_catalog() -> pd.DataFrame:
    """Load the real KOHLER product catalog once per session."""
    return load_products()


# ----------------------------------------------------------------------
# The pipeline itself — kept separate from Streamlit widget code so it
# can be understood (and in principle tested) independently of the UI.
# Returns a plain dict describing what happened, rather than raising,
# so the render step can decide how to display each outcome.
# ----------------------------------------------------------------------

def run_pipeline(user_text: str, products_df: pd.DataFrame, demo_mode: bool = False) -> dict:
    """
    Run the full requirements -> recommendations pipeline for one
    user request.

    Returns a dict with at least a "stage" key describing where the
    pipeline landed:
        "empty_input"       - user_text was blank
        "llm_config_error"  - missing/invalid API key or SDK setup
        "llm_request_error" - the API call itself failed
        "llm_response_error"- the LLM's response wasn't usable structured data
        "validation_error"  - parsed requirements failed field-level validation
        "incomplete"        - requirements are valid but missing critical info
        "adapter_error"     - defensive: adapter refused incomplete data
        "engine_error"      - the recommendation engine raised unexpectedly
        "success"           - recommend_products() ran; see "engine_result"
    """
    if not isinstance(user_text, str) or not user_text.strip():
        return {"stage": "empty_input"}

    if demo_mode:
        raw_requirements = parse_demo_requirements(user_text)
    else:
        try:
            raw_requirements = parse_requirements(user_text)
        except LLMConfigurationError as exc:
            return {"stage": "llm_config_error", "error": str(exc)}
        except LLMRequestError as exc:
            return {"stage": "llm_request_error", "error": str(exc)}
        except LLMResponseValidationError as exc:
            return {"stage": "llm_response_error", "error": str(exc)}
        except LLMParserError as exc:  # any other LLMParserError subtype
            return {"stage": "llm_response_error", "error": str(exc)}

    try:
        clean_requirements = validate_requirements(raw_requirements)
    except RequirementsValidationError as exc:
        return {"stage": "validation_error", "error": str(exc), "raw_requirements": raw_requirements}

    completeness = check_requirements_completeness(clean_requirements)
    if not completeness["valid"]:
        return {
            "stage": "incomplete",
            "raw_requirements": raw_requirements,
            "clean_requirements": clean_requirements,
            "completeness": completeness,
        }

    try:
        recommendation_args = requirements_to_recommendation_args(clean_requirements)
    except RequirementsValidationError as exc:
        # Defensive only — completeness was just confirmed True above, so
        # this should not normally happen.
        return {"stage": "adapter_error", "error": str(exc), "clean_requirements": clean_requirements}

    try:
        engine_result = recommend_products(products_df=products_df, **recommendation_args)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user below, not swallowed
        return {
            "stage": "engine_error",
            "error": f"{type(exc).__name__}: {exc}",
            "clean_requirements": clean_requirements,
        }

    return {
        "stage": "success",
        "raw_requirements": raw_requirements,
        "clean_requirements": clean_requirements,
        "engine_result": engine_result,
    }

def run_requirements_pipeline(
    raw_requirements: dict,
    products_df: pd.DataFrame,
) -> dict:
    """Run already-structured requirements through validation and recommendations."""

    try:
        clean_requirements = validate_requirements(raw_requirements)
    except RequirementsValidationError as exc:
        return {
            "stage": "validation_error",
            "error": str(exc),
            "raw_requirements": raw_requirements,
        }

    completeness = check_requirements_completeness(clean_requirements)

    if not completeness["valid"]:
        return {
            "stage": "incomplete",
            "raw_requirements": raw_requirements,
            "clean_requirements": clean_requirements,
            "completeness": completeness,
        }

    try:
        recommendation_args = requirements_to_recommendation_args(
            clean_requirements
        )
    except RequirementsValidationError as exc:
        return {
            "stage": "adapter_error",
            "error": str(exc),
            "clean_requirements": clean_requirements,
        }

    try:
        engine_result = recommend_products(
            products_df=products_df,
            **recommendation_args,
        )
    except Exception as exc:
        return {
            "stage": "engine_error",
            "error": f"{type(exc).__name__}: {exc}",
            "clean_requirements": clean_requirements,
        }

    return {
        "stage": "success",
        "raw_requirements": raw_requirements,
        "clean_requirements": clean_requirements,
        "engine_result": engine_result,
    }



# ----------------------------------------------------------------------
# What-If redesign
# ----------------------------------------------------------------------

def parse_demo_changes(user_text: str) -> dict:
    """Parse only the changes requested for a What-If redesign."""
    text = user_text.strip()
    changes = parse_demo_requirements(text)

    # A What-If request can omit dimensions/categories because they are
    # inherited from the current design. Detect a few natural-language
    # variants that the base demo parser intentionally keeps simple.
    if re.search(r"\bluxurious(?:ly)?\b", text, re.IGNORECASE):
        changes["theme"] = "Luxury"
    elif re.search(r"\bmore\s+luxurious\b", text, re.IGNORECASE):
        changes["theme"] = "Luxury"

    all_categories = {
        "Smart Toilet": r"\bsmart\s+toilet\b",
        "Toilet": r"\btoilet\b",
        "Washbasin": r"\bwashbasin\b|\bbasin\b|\bsink\b",
        "Faucet": r"\bfaucet\b|\btap\b",
        "Shower": r"\bshower\b",
        "Vanity": r"\bvanity\b",
    }

    additions = []
    removals = []
    for category, pattern in all_categories.items():
        if re.search(
            rf"\b(?:add|include|install|want|need|also)\b[^.]*{pattern}",
            text,
            re.IGNORECASE,
        ):
            additions.append(category)
        if re.search(
            rf"\b(?:remove|delete|drop|without)\b[^.]*{pattern}",
            text,
            re.IGNORECASE,
        ):
            removals.append(category)

    changes["_add_categories"] = additions
    changes["_remove_categories"] = removals
    # Categories found by the base parser are not automatically treated as
    # changes; only explicit add/remove language changes the current design.
    changes["required_categories"] = []

    return changes


def merge_what_if_requirements(base: dict, changes: dict) -> dict:
    """Apply only explicitly requested changes to the current requirements."""
    merged = copy.deepcopy(base)
    merged_preferences = merged.setdefault("preferences", {})
    change_preferences = changes.get("preferences") or {}

    for field in ("length_ft", "width_ft", "budget_inr", "theme"):
        value = changes.get(field)
        if value is not None:
            merged[field] = value

    for field in ("color", "water_efficiency_keyword"):
        value = change_preferences.get(field)
        if value is not None:
            merged_preferences[field] = value

    feature_changes = change_preferences.get("feature_keywords") or []
    if feature_changes:
        merged_preferences["feature_keywords"] = list(
            dict.fromkeys(
                (merged_preferences.get("feature_keywords") or []) + feature_changes
            )
        )

    categories = list(merged.get("required_categories") or [])

    for category in changes.get("_remove_categories") or []:
        if category == "Smart Toilet":
            categories = [
                c for c in categories if c not in ("Smart Toilet", "Toilet")
            ]
        elif category in categories:
            categories.remove(category)

    for category in changes.get("_add_categories") or []:
        if category not in categories:
            categories.append(category)

    # Keep Smart Toilet distinct from generic Toilet.
    if "Smart Toilet" in categories and "Toilet" in categories:
        categories.remove("Toilet")

    merged["required_categories"] = categories
    return merged


def run_what_if(
    base_requirements: dict,
    user_text: str,
    products_df: pd.DataFrame,
    demo_mode: bool = False,
) -> dict:
    """Apply a What-If change to the current design and rerun the same engine."""
    if not isinstance(user_text, str) or not user_text.strip():
        return {"stage": "empty_what_if"}

    try:
        if demo_mode:
            changes = parse_demo_changes(user_text)
        else:
            changes = parse_requirements(user_text)

        merged_requirements = merge_what_if_requirements(base_requirements, changes)
        clean_requirements = validate_requirements(merged_requirements)
    except LLMConfigurationError as exc:
        return {"stage": "llm_config_error", "error": str(exc)}
    except LLMRequestError as exc:
        return {"stage": "llm_request_error", "error": str(exc)}
    except LLMResponseValidationError as exc:
        return {"stage": "llm_response_error", "error": str(exc)}
    except LLMParserError as exc:
        return {"stage": "llm_response_error", "error": str(exc)}
    except RequirementsValidationError as exc:
        return {"stage": "validation_error", "error": str(exc)}

    completeness = check_requirements_completeness(clean_requirements)
    if not completeness["valid"]:
        return {
            "stage": "incomplete",
            "clean_requirements": clean_requirements,
            "completeness": completeness,
        }

    try:
        recommendation_args = requirements_to_recommendation_args(clean_requirements)
        engine_result = recommend_products(
            products_df=products_df, **recommendation_args
        )
    except RequirementsValidationError as exc:
        return {"stage": "adapter_error", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {
            "stage": "engine_error",
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "stage": "success",
        "clean_requirements": clean_requirements,
        "engine_result": engine_result,
    }


def _render_what_if_result(result: dict) -> None:
    """Render a redesign result without nesting another What-If control."""
    if result["stage"] == "empty_what_if":
        st.warning("Please describe what you would like to change.")
        return

    if result["stage"] == "incomplete":
        _render_parsed_requirements(result["clean_requirements"])
        _render_missing_fields(result["completeness"])
        return

    if result["stage"] != "success":
        st.error(
            result.get("error", "The redesign could not be completed. Please try again.")
        )
        return

    _render_parsed_requirements(result["clean_requirements"])
    engine_result = result["engine_result"]

    if engine_result["status"] == "ok":
        st.success(engine_result["message"])
        st.markdown("## Updated KOHLER recommendations")
        for i, bundle in enumerate(engine_result["bundles"], start=1):
            _render_bundle(i, bundle, result["clean_requirements"])
    elif engine_result["status"] in ("no_valid_bundles", "missing_categories"):
        st.warning(engine_result["message"])
    else:
        st.error(
            f"Unexpected engine status '{engine_result['status']}': "
            f"{engine_result['message']}"
        )


def _render_what_if_controls(
    base_requirements: dict, products_df: pd.DataFrame
) -> None:
    """Show the optional What-If redesign control after the initial design."""
    st.divider()
    st.markdown("## Want to redesign it?")
    st.caption(
        "Describe a change to your current design. Existing dimensions and "
        "requirements are kept unless you explicitly change them."
    )

    what_if_text = st.text_area(
        "What would you like to change?",
        placeholder=(
            "Example: Make it more luxurious, change to a white theme, "
            "and increase the budget to ₹2 lakh."
        ),
        height=110,
        key="what_if_text",
    )

    if st.button("Redesign Bathroom", type="secondary", key="redesign_button"):
        with st.spinner("Updating your design..."):
            redesign_result = run_what_if(
                base_requirements,
                what_if_text,
                products_df,
                demo_mode=DEMO_MODE,
            )
        st.session_state["what_if_result"] = redesign_result

    if "what_if_result" in st.session_state:
        st.markdown("### Redesigned bathroom")
        _render_what_if_result(st.session_state["what_if_result"])


# ----------------------------------------------------------------------
# Small display helpers
# ----------------------------------------------------------------------

def _fmt(value) -> str:
    """Render a possibly-missing (None/NaN) catalog value for display."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "Not specified"
    return str(value)


def _fmt_inr(value) -> str:
    """Render a possibly-missing INR amount, comma-formatted."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "Not specified"
    try:
        return f"\u20b9{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _render_layout(bundle: dict, clean_requirements: dict) -> None:
    """
    Render a polished conceptual bathroom visualisation using local SVG only.

    This is intentionally a visual planning aid, not an architectural drawing.
    No recommendation, catalog, parser, or validation logic is changed here.
    """
    length_ft = clean_requirements.get("length_ft")
    width_ft = clean_requirements.get("width_ft")

    if length_ft is None or width_ft is None:
        st.info("Bathroom dimensions are required to render the layout.")
        return

    products = bundle.get("products", [])
    categories = []

    for product in products:
        category = str(product.get("category", "")).strip()
        if category and category not in categories:
            categories.append(category)

    canvas_w = 900
    canvas_h = 560

    room_x = 95
    room_y = 70
    room_w = 710
    room_h = 400

    def esc(value):
        return html.escape(str(value))

    def label(text, x, y, size=14, weight="600", anchor="middle"):
        return (
            f'<text x="{x}" y="{y}" font-size="{size}" '
            f'font-weight="{weight}" text-anchor="{anchor}" '
            f'fill="currentColor">{esc(text)}</text>'
        )

    def line(x1, y1, x2, y2, width=1.5, opacity=0.45):
        return (
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="currentColor" stroke-width="{width}" '
            f'stroke-opacity="{opacity}" />'
        )

    def rect(x, y, w, h, rx=12, fill="none", fill_opacity=1,
             stroke="currentColor", stroke_width=1.5, stroke_opacity=0.6):
        return (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" fill-opacity="{fill_opacity}" '
            f'stroke="{stroke}" stroke-width="{stroke_width}" '
            f'stroke-opacity="{stroke_opacity}" />'
        )

    svg = []

    # -----------------------------
    # SVG background
    # -----------------------------
    svg.append(
        f'<svg viewBox="0 0 {canvas_w} {canvas_h}" '
        f'width="100%" role="img" '
        f'aria-label="Conceptual KOHLER bathroom layout">'
    )

    # Subtle background
    svg.append(
        '<rect x="0" y="0" width="900" height="560" '
        'rx="22" fill="currentColor" fill-opacity="0.025"/>'
    )

    # Header
    svg.append(label("Bathroom Layout", 95, 34, 20, "700", "start"))
    svg.append(
        label(
            f"{length_ft:g} × {width_ft:g} ft • Conceptual visualisation",
            805,
            34,
            13,
            "500",
            "end",
        )
    )

    # -----------------------------
    # Room shell
    # -----------------------------
    svg.append(
        rect(
            room_x,
            room_y,
            room_w,
            room_h,
            rx=18,
            fill="currentColor",
            fill_opacity=0.035,
            stroke_width=3,
            stroke_opacity=0.55,
        )
    )

    # Floor grid
    grid_step = 40

    for x in range(room_x + grid_step, room_x + room_w, grid_step):
        svg.append(line(x, room_y, x, room_y + room_h, 0.8, 0.10))

    for y in range(room_y + grid_step, room_y + room_h, grid_step):
        svg.append(line(room_x, y, room_x + room_w, y, 0.8, 0.10))

    # -----------------------------
    # Dimension indicators
    # -----------------------------
    dim_y = room_y + room_h + 34

    svg.append(line(room_x, dim_y, room_x + room_w, dim_y, 1.5, 0.55))
    svg.append(line(room_x, dim_y - 7, room_x, dim_y + 7, 1.5, 0.55))
    svg.append(
        line(
            room_x + room_w,
            dim_y - 7,
            room_x + room_w,
            dim_y + 7,
            1.5,
            0.55,
        )
    )
    svg.append(label(f"{width_ft:g} ft", room_x + room_w / 2, dim_y + 22, 12))

    dim_x = room_x - 34

    svg.append(line(dim_x, room_y, dim_x, room_y + room_h, 1.5, 0.55))
    svg.append(line(dim_x - 7, room_y, dim_x + 7, room_y, 1.5, 0.55))
    svg.append(
        line(
            dim_x - 7,
            room_y + room_h,
            dim_x + 7,
            room_y + room_h,
            1.5,
            0.55,
        )
    )

    # Rotated length label
    svg.append(
        f'<text x="{dim_x - 12}" y="{room_y + room_h / 2}" '
        f'font-size="12" font-weight="600" text-anchor="middle" '
        f'fill="currentColor" '
        f'transform="rotate(-90 {dim_x - 12} {room_y + room_h / 2})">'
        f'{esc(f"{length_ft:g} ft")}</text>'
    )

    # -----------------------------
    # Fixture positions
    # -----------------------------
    positions = {
        "Shower": (room_x + 55, room_y + 45),
        "Toilet": (room_x + 490, room_y + 70),
        "Smart Toilet": (room_x + 490, room_y + 70),
        "Washbasin": (room_x + 70, room_y + 275),
        "Vanity": (room_x + 300, room_y + 275),
    }

    drawn_washbasin = False
    drawn_vanity = False

    # -----------------------------
    # Shower
    # -----------------------------
    if "Shower" in categories:
        x, y = positions["Shower"]
        w, h = 190, 145

        svg.append(
            rect(
                x,
                y,
                w,
                h,
                rx=14,
                fill="currentColor",
                fill_opacity=0.07,
                stroke_width=2,
                stroke_opacity=0.55,
            )
        )

        # Glass partition
        svg.append(line(x + w - 22, y + 10, x + w - 22, y + h - 10, 2, 0.35))

        # Shower head
        svg.append(
            f'<circle cx="{x + 48}" cy="{y + 45}" r="11" '
            f'fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-opacity="0.65"/>'
        )

        svg.append(line(x + 48, y + 56, x + 48, y + 80, 2, 0.55))

        # Water lines
        for dx in (-8, 0, 8):
            svg.append(
                line(
                    x + 48 + dx,
                    y + 81,
                    x + 48 + dx,
                    y + 99,
                    1.2,
                    0.35,
                )
            )

        svg.append(label("SHOWER", x + w / 2, y + h - 22, 13, "700"))

    # -----------------------------
    # Toilet / Smart Toilet
    # -----------------------------
    toilet_category = None

    if "Smart Toilet" in categories:
        toilet_category = "Smart Toilet"
    elif "Toilet" in categories:
        toilet_category = "Toilet"

    if toilet_category:
        x, y = positions[toilet_category]

        # Tank
        svg.append(
            rect(
                x + 28,
                y,
                72,
                32,
                rx=7,
                fill="currentColor",
                fill_opacity=0.07,
                stroke_width=1.8,
                stroke_opacity=0.55,
            )
        )

        # Bowl
        svg.append(
            f'<ellipse cx="{x + 64}" cy="{y + 66}" rx="48" ry="34" '
            f'fill="currentColor" fill-opacity="0.07" '
            f'stroke="currentColor" stroke-width="2" stroke-opacity="0.55"/>'
        )

        # Inner bowl
        svg.append(
            f'<ellipse cx="{x + 64}" cy="{y + 66}" rx="28" ry="17" '
            f'fill="none" stroke="currentColor" '
            f'stroke-width="1.5" stroke-opacity="0.35"/>'
        )

        # Seat/base
        svg.append(
            rect(
                x + 34,
                y + 88,
                60,
                18,
                rx=8,
                fill="currentColor",
                fill_opacity=0.045,
                stroke_width=1.5,
                stroke_opacity=0.40,
            )
        )

        toilet_label = "SMART TOILET" if toilet_category == "Smart Toilet" else "TOILET"
        svg.append(label(toilet_label, x + 64, y + 132, 13, "700"))

    # -----------------------------
    # Washbasin
    # -----------------------------
    if "Washbasin" in categories:
        drawn_washbasin = True
        x, y = positions["Washbasin"]
        w, h = 190, 105

        # Counter
        svg.append(
            rect(
                x,
                y,
                w,
                h,
                rx=13,
                fill="currentColor",
                fill_opacity=0.07,
                stroke_width=2,
                stroke_opacity=0.55,
            )
        )

        # Basin
        svg.append(
            f'<ellipse cx="{x + w / 2}" cy="{y + 54}" rx="58" ry="27" '
            f'fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-opacity="0.50"/>'
        )

        # Drain
        svg.append(
            f'<circle cx="{x + w / 2}" cy="{y + 55}" r="4" '
            f'fill="currentColor" fill-opacity="0.45"/>'
        )

        # Faucet attached to basin
        faucet_x = x + w / 2
        faucet_base_y = y + 25

        svg.append(line(faucet_x, faucet_base_y, faucet_x, faucet_base_y - 22, 3, 0.65))

        svg.append(
            f'<path d="M {faucet_x} {faucet_base_y - 22} '
            f'Q {faucet_x + 18} {faucet_base_y - 42} '
            f'{faucet_x + 18} {faucet_base_y - 20}" '
            f'fill="none" stroke="currentColor" '
            f'stroke-width="3" stroke-linecap="round" '
            f'stroke-opacity="0.65"/>'
        )

        # Water drop
        svg.append(
            f'<path d="M {faucet_x + 18} {faucet_base_y - 18} '
            f'c -4 7 -4 11 0 15 c 4 -4 4 -8 0 -15" '
            f'fill="currentColor" fill-opacity="0.35"/>'
        )

        svg.append(label("WASHBASIN", x + w / 2, y + h + 22, 13, "700"))

    # -----------------------------
    # Vanity
    # -----------------------------
    if "Vanity" in categories:
        drawn_vanity = True
        x, y = positions["Vanity"]
        w, h = 190, 105

        svg.append(
            rect(
                x,
                y,
                w,
                h,
                rx=13,
                fill="currentColor",
                fill_opacity=0.07,
                stroke_width=2,
                stroke_opacity=0.55,
            )
        )

        # Cabinet divisions
        svg.append(line(x + w / 2, y + 12, x + w / 2, y + h - 10, 1.2, 0.30))

        # Handles
        svg.append(line(x + 70, y + 53, x + 80, y + 53, 2, 0.45))
        svg.append(line(x + 110, y + 53, x + 120, y + 53, 2, 0.45))

        # Countertop basin suggestion
        svg.append(
            f'<ellipse cx="{x + w / 2}" cy="{y + 25}" rx="48" ry="12" '
            f'fill="none" stroke="currentColor" '
            f'stroke-width="1.5" stroke-opacity="0.35"/>'
        )

        svg.append(label("VANITY", x + w / 2, y + h + 22, 13, "700"))

    # -----------------------------
    # Faucet without a washbasin
    # -----------------------------
    if "Faucet" in categories and not drawn_washbasin and not drawn_vanity:
        x = room_x + 270
        y = room_y + 205

        svg.append(
            rect(
                x,
                y,
                150,
                55,
                rx=12,
                fill="currentColor",
                fill_opacity=0.045,
                stroke_width=1.5,
                stroke_opacity=0.40,
            )
        )

        svg.append(
            f'<path d="M {x + 65} {y + 38} '
            f'Q {x + 85} {y + 8} {x + 105} {y + 30}" '
            f'fill="none" stroke="currentColor" '
            f'stroke-width="3" stroke-linecap="round" '
            f'stroke-opacity="0.65"/>'
        )

        svg.append(label("FAUCET", x + 75, y + 78, 12, "700"))

    # -----------------------------
    # Generic fallback for any
    # unrecognised category
    # -----------------------------
    known_categories = {
        "Shower",
        "Toilet",
        "Smart Toilet",
        "Washbasin",
        "Vanity",
        "Faucet",
    }

    unknown_categories = [c for c in categories if c not in known_categories]

    if unknown_categories:
        fallback_x = room_x + 490
        fallback_y = room_y + 230

        for index, category in enumerate(unknown_categories):
            y = fallback_y + index * 75

            svg.append(
                rect(
                    fallback_x,
                    y,
                    150,
                    50,
                    rx=10,
                    fill="currentColor",
                    fill_opacity=0.05,
                    stroke_width=1.5,
                    stroke_opacity=0.40,
                )
            )

            svg.append(label(category.upper(), fallback_x + 75, y + 31, 11, "700"))

    # -----------------------------
    # Entry indicator
    # -----------------------------
    entry_x = room_x + room_w / 2 - 45

    svg.append(
        f'<rect x="{entry_x}" y="{room_y + room_h - 4}" '
        f'width="90" height="12" rx="6" '
        f'fill="currentColor" fill-opacity="0.12"/>'
    )

    svg.append(label("ENTRY", room_x + room_w / 2, room_y + room_h + 1, 10, "600"))

    # -----------------------------
    # Legend / disclaimer
    # -----------------------------
    svg.append(
        label(
            "Conceptual placement • not architectural scale",
            805,
            530,
            11,
            "500",
            "end",
        )
    )

    svg.append("</svg>")

    st.markdown(
        "".join(svg),
        unsafe_allow_html=True,
    )

def _render_product(product: dict) -> None:
    """Render a polished product card using only catalog data."""
    name = html.escape(_fmt(product.get("product_name", "Unnamed product")))
    category = html.escape(_fmt(product.get("category")))
    collection = html.escape(_fmt(product.get("collection")))
    price = _fmt_inr(product.get("price"))
    mrp = product.get("mrp")
    color = html.escape(_fmt(product.get("color")))
    finish = html.escape(_fmt(product.get("finish")))
    style = html.escape(_fmt(product.get("style")))
    water_efficiency = html.escape(_fmt(product.get("water_efficiency")))
    source_url = product.get("source_url")

    # Calculate displayed savings when both price and MRP are available.
    savings_text = None
    try:
        price_value = float(product.get("price"))
        mrp_value = float(product.get("mrp"))
        if mrp_value > price_value:
            savings_text = f"Save {_fmt_inr(mrp_value - price_value)}"
    except (TypeError, ValueError):
        pass

    with st.container(border=True):
        st.caption(category)

        st.markdown(f"### {name}")

        price_col, match_col = st.columns([2, 1])

        with price_col:
            st.markdown(f"**{price}**")
            if savings_text:
                st.caption(savings_text)

        with match_col:
            st.caption("KOHLER catalog")
            st.write(collection)

        st.write(
            f"**Finish:** {finish}  ·  "
            f"**Color:** {color}"
        )

        st.write(f"**Style:** {style}")

        if water_efficiency != "Not specified":
            st.write(f"**Water efficiency:** {water_efficiency}")

        with st.expander("Why this product fits"):
            st.write(
                f"This {category.lower()} is from the **{collection}** "
                f"collection and has a **{style.lower()}** style with "
                f"a **{finish.lower()}** finish."
            )

            if water_efficiency != "Not specified":
                st.write(
                    f"Water efficiency: **{water_efficiency}**."
                )

            features = product.get("features")
            if features is not None and not (
                isinstance(features, float) and pd.isna(features)
            ):
                st.write(f"Key features: {features}")

        with st.expander("Product details"):
            col1, col2 = st.columns(2)

            with col1:
                st.write(f"**SKU:** {_fmt(product.get('sku'))}")
                st.write(f"**MRP:** {_fmt_inr(product.get('mrp'))}")
                st.write(
                    f"**Installation:** "
                    f"{_fmt(product.get('installation_type'))}"
                )
                st.write(
                    f"**Water efficiency:** "
                    f"{_fmt(product.get('water_efficiency'))}"
                )
                st.write(
                    f"**Flow rate:** "
                    f"{_fmt(product.get('flow_rate_lpm'))} LPM"
                )

            with col2:
                st.write(
                    f"**Dimensions:** "
                    f"{_fmt(product.get('width_in'))} × "
                    f"{_fmt(product.get('depth_in'))} × "
                    f"{_fmt(product.get('height_in'))} in"
                )
                st.write(
                    f"**Features:** "
                    f"{_fmt(product.get('features'))}"
                )
                st.write(
                    f"**Smart features:** "
                    f"{_fmt(product.get('smart_features'))}"
                )

            description = product.get("description")
            if description is not None and not (
                isinstance(description, float) and pd.isna(description)
            ):
                st.write(description)

        if source_url and not (
            isinstance(source_url, float) and pd.isna(source_url)
        ):
            st.link_button("View on KOHLER India", source_url)

def _render_bundle(index: int, bundle: dict, clean_requirements: dict) -> None:
    """Render one recommendation bundle as a compact card."""
    st.markdown(f"## Bundle {index}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Compatibility", f"{bundle['compatibility_score']:.1f} / 100")
    col2.metric("Total product cost", _fmt_inr(bundle["total_price"]))
    col3.metric("Budget remaining", _fmt_inr(bundle["remaining_budget"]))

    breakdown = bundle["score_breakdown"]
    st.caption(
        f"Style {breakdown['style_score']:.2f} · "
        f"Budget {breakdown['budget_score']:.2f} · "
        f"Spatial fit {breakdown['spatial_score']:.2f} · "
        f"Features {breakdown['feature_score']:.2f}"
    )

    _render_layout(bundle, clean_requirements)

    product_columns = st.columns(len(bundle["products"]))
    for column, product in zip(product_columns, bundle["products"]):
        with column:
            _render_product(product)


def _render_missing_fields(completeness: dict) -> None:
    st.warning("Please provide a bit more information:")
    for field in completeness["missing_fields"]:
        st.write(f"- {FIELD_LABELS.get(field, field)}")


def _render_parsed_requirements(clean_requirements: dict) -> None:
    st.markdown("### What I understood")

    preferences = clean_requirements.get("preferences") or {}
    dimensions = (
        f"{_fmt(clean_requirements.get('length_ft'))} × "
        f"{_fmt(clean_requirements.get('width_ft'))} ft"
    )
    budget = _fmt_inr(clean_requirements.get("budget_inr"))
    categories = ", ".join(clean_requirements.get("required_categories") or []) or "Not specified"
    theme = _fmt(clean_requirements.get("theme"))
    color = _fmt(preferences.get("color"))
    water = _fmt(preferences.get("water_efficiency_keyword"))
    features = ", ".join(preferences.get("feature_keywords") or []) or "None"

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Bathroom size", dimensions)
    col2.metric("Budget", budget)
    col3.metric("Style", theme)
    col4.metric("Products needed", categories)

    st.caption(f"Preferences: {color} · {water} · Features: {features}")

    # with st.expander("Show technical details"):
    #     st.json(clean_requirements)


def render_result(result: dict) -> None:
    """Render whatever run_pipeline() returned."""
    stage = result["stage"]

    if stage == "empty_input":
        st.warning("Please describe your bathroom requirements before continuing.")
        return

    if stage == "llm_config_error":
        st.error(
            "The LLM isn't configured correctly, so requirements can't be parsed "
            "right now. Details: " + result["error"]
        )
        return

    if stage == "llm_request_error":
        st.error("The request to the LLM failed. Details: " + result["error"])
        return

    if stage == "llm_response_error":
        st.error(
            "The LLM's response couldn't be understood. Try rephrasing your "
            "request. Details: " + result["error"]
        )
        return

    if stage == "validation_error":
        st.error("The parsed requirements were invalid. Details: " + result["error"])
        return

    if stage == "incomplete":
        _render_parsed_requirements(result["clean_requirements"])
        _render_missing_fields(result["completeness"])
        return

    if stage == "adapter_error":
        st.error("Could not prepare the recommendation request. Details: " + result["error"])
        return

    if stage == "engine_error":
        st.error(
            "The recommendation engine hit an unexpected error. This is a bug, "
            "not a normal 'no results' case. Details: " + result["error"]
        )
        return

    if stage == "success":
        _render_parsed_requirements(result["clean_requirements"])
        engine_result = result["engine_result"]

        if engine_result["status"] == "ok":
            st.success(engine_result["message"])
            st.markdown("## Your KOHLER recommendations")
            for i, bundle in enumerate(engine_result["bundles"], start=1):
                _render_bundle(i, bundle, result["clean_requirements"])
        elif engine_result["status"] in ("no_valid_bundles", "missing_categories"):
            st.warning(engine_result["message"])
        else:  # pragma: no cover - defensive, engine doesn't currently return other statuses
            st.error(f"Unexpected engine status '{engine_result['status']}': {engine_result['message']}")
        return

    st.error(f"Unexpected internal state (stage='{stage}'). Please try again.")  # pragma: no cover


# ----------------------------------------------------------------------
# Streamlit page
# ----------------------------------------------------------------------

ARCHITECTURE_CATEGORY_OPTIONS = [
    "Toilet",
    "Washbasin",
    "Faucet",
    "Shower",
    "Vanity",
    "Smart Toilet",
]


def _render_category_card_selector() -> list[str]:
    """Let the user select the KOHLER product categories they want."""
    st.markdown("### What would you like to include?")
    st.caption("Choose one or more KOHLER product categories.")

    selected_categories = st.pills(
        "Product categories",
        ARCHITECTURE_CATEGORY_OPTIONS,
        selection_mode="multi",
        key="architecture_selected_categories",
        label_visibility="collapsed",
    )

    if selected_categories:
        st.caption(f"Selected: {' · '.join(selected_categories)}")
    else:
        st.caption("Select at least one product category.")

    return selected_categories

def _render_architecture_data(architecture_data: dict) -> None:
    """Display the bathroom information extracted from a USD/USDZ file."""
    st.markdown("### Detected Bathroom Architecture")

    room = architecture_data.get("room", {})
    components = architecture_data.get("components", [])

    length = room.get("length_ft")
    width = room.get("width_ft")
    height = room.get("height_ft")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Room length",
        f"{length:g} ft" if length is not None else "Not detected",
    )

    col2.metric(
        "Room width",
        f"{width:g} ft" if width is not None else "Not detected",
    )

    col3.metric(
        "Room height",
        f"{height:g} ft" if height is not None else "Not detected",
    )

    st.markdown("#### Detected Components")

    if not components:
        st.info("No recognized room components were found in the file.")
        return

    for row_start in range(0, len(components), 3):
        row_components = components[row_start:row_start + 3]

        columns = st.columns(3)

        for column, component in zip(columns, row_components):
            with column:
                name = component.get("name", "Unnamed component")
                component_type = component.get("type", "Unknown")
                dimensions = component.get("dimensions")

                if dimensions:
                    dimension_text = (
                        f"{dimensions['width_ft']:g} × "
                        f"{dimensions['depth_ft']:g} × "
                        f"{dimensions['height_ft']:g} ft"
                    )
                else:
                    dimension_text = "Dimensions not detected"

                with st.container(border=True):
                    st.markdown(f"**{name}**")
                    st.caption(component_type.title())
                    st.write(f"Dimensions: **{dimension_text}**")

def main() -> None:
    st.set_page_config(
        page_title="KOHLER AI Bathroom Designer",
        page_icon="🚿",
        layout="wide",
    )

    st.markdown(
        """
        <style>
        .block-container {padding-top: 2rem; padding-bottom: 3rem;}
        div[data-testid="stMetric"] {
            border: 1px solid rgba(128,128,128,0.25);
            border-radius: 12px;
            padding: 0.75rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("KOHLER AI Bathroom Designer")
    st.caption("Describe your bathroom. Get a coordinated, budget-checked KOHLER product bundle.")

    if DEMO_MODE:
        st.info(
            "🛠️ DEMO MODE · No paid LLM API call. "
            "Your typed request is parsed locally, then sent through the same "
            "validation, adapter, catalog, and recommendation pipeline."
        )

        input_mode = st.radio(
        "How do you want to provide your bathroom?",
        ["Describe it", "Upload USD / USDZ"],
        horizontal=True,
    )

    user_text = ""

    if input_mode == "Describe it":
        user_text = st.text_area(
            "Describe your bathroom",
            placeholder=EXAMPLE_REQUEST,
            height=140,
            help="Include bathroom dimensions, budget, preferred style/finish, and the products you need.",
        )

        design_button = st.button("Design My Bathroom", type="primary")

    else:
        architecture_file = st.file_uploader(
            "Upload your bathroom architecture",
            type=["usd", "usda", "usdc", "usdz"],
            help="Upload a USD/USDZ architectural model containing your bathroom layout and dimensions.",
        )

        if architecture_file:
            st.success(f"Uploaded: {architecture_file.name}")

        architecture_selected_categories = _render_category_card_selector()

        architecture_budget = st.number_input(
            "Budget (₹)",
            min_value=10000,
            max_value=10000000,
            value=150000,
            step=10000,
            help="Maximum budget for the recommended KOHLER products.",
        )

        architecture_theme = st.selectbox(
            "Preferred bathroom style",
            [
                "Modern",
                "Contemporary",
                "Elegant",
                "Minimalist",
                "Luxury",
                "Classic",
            ],
        )

        design_button = st.button(
            "Analyze & Design Bathroom",
            type="primary",
            disabled=(
                architecture_file is None
                or not architecture_selected_categories
            ),
        )

    if design_button:
        if input_mode == "Describe it":
            products_df = get_catalog()
            with st.spinner("Understanding your requirements and finding compatible products..."):
                result = run_pipeline(
                    user_text,
                    products_df,
                    demo_mode=DEMO_MODE,
                )
            st.session_state["pipeline_result"] = result

        else:
            if architecture_file is None:
                st.warning("Please upload a USD or USDZ file first.")
            else:
                try:
                    file_suffix = Path(architecture_file.name).suffix

                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=file_suffix,
                    ) as temp_file:
                        temp_file.write(architecture_file.getbuffer())
                        temp_path = temp_file.name

                    with st.spinner("Analyzing your bathroom architecture..."):
                        architecture_data = parse_usd_file(temp_path)

                    st.session_state["architecture_data"] = architecture_data

                    requirements = architecture_to_requirements(
                        architecture_data,
                        budget_inr=architecture_budget,
                        theme=architecture_theme,
                        required_categories=architecture_selected_categories,
                    )

                    st.session_state["architecture_requirements"] = requirements

                    products_df = get_catalog()

                    with st.spinner("Finding compatible KOHLER products..."):
                        result = run_requirements_pipeline(
                            requirements,
                            products_df,
                        )

                    st.session_state["pipeline_result"] = result

                    st.success("Bathroom architecture analyzed successfully.")

                    _render_architecture_data(architecture_data)

                except USDParserError as exc:
                    st.error(f"Could not analyze the architecture file: {exc}")

                except Exception as exc:
                    st.error(f"Unexpected error while analyzing the file: {exc}")

    if "pipeline_result" in st.session_state:
        st.divider()
        initial_result = st.session_state["pipeline_result"]
        render_result(initial_result)

        if (
            initial_result.get("stage") == "success"
            and initial_result.get("engine_result", {}).get("status") == "ok"
        ):
            _render_what_if_controls(
                initial_result["clean_requirements"],
                get_catalog(),
            )




if __name__ == "__main__":
    main()
