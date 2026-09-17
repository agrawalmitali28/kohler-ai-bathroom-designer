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

import html
import os
import re

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

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


def _render_product(product: dict) -> None:
    """Render a compact product card using only catalog data."""
    name = html.escape(_fmt(product.get("product_name", "Unnamed product")))
    category = html.escape(_fmt(product.get("category")))
    collection = html.escape(_fmt(product.get("collection")))
    price = _fmt_inr(product.get("price"))
    color = html.escape(_fmt(product.get("color")))
    finish = html.escape(_fmt(product.get("finish")))
    style = html.escape(_fmt(product.get("style")))
    source_url = product.get("source_url")

    with st.container(border=True):
        st.caption(category)
        st.markdown(f"### {name}")
        st.markdown(f"**{price}**")
        st.write(f"Collection: {collection}")
        st.write(f"Finish: {finish} · Color: {color}")
        st.write(f"Style: {style}")

        with st.expander("Product details"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**SKU:** {_fmt(product.get('sku'))}")
                st.write(f"**MRP:** {_fmt_inr(product.get('mrp'))}")
                st.write(f"**Installation:** {_fmt(product.get('installation_type'))}")
                st.write(f"**Water efficiency:** {_fmt(product.get('water_efficiency'))}")
                st.write(f"**Flow rate:** {_fmt(product.get('flow_rate_lpm'))} LPM")
            with col2:
                st.write(
                    f"**Dimensions:** {_fmt(product.get('width_in'))} × "
                    f"{_fmt(product.get('depth_in'))} × {_fmt(product.get('height_in'))} in"
                )
                st.write(f"**Features:** {_fmt(product.get('features'))}")
                st.write(f"**Smart features:** {_fmt(product.get('smart_features'))}")

            description = product.get("description")
            if description is not None and not (isinstance(description, float) and pd.isna(description)):
                st.write(description)

        if source_url and not (isinstance(source_url, float) and pd.isna(source_url)):
            st.link_button("View on KOHLER India", source_url)


def _render_bundle(index: int, bundle: dict) -> None:
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

    with st.expander("Show technical details"):
        st.json(clean_requirements)


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
                _render_bundle(i, bundle)
        elif engine_result["status"] in ("no_valid_bundles", "missing_categories"):
            st.warning(engine_result["message"])
        else:  # pragma: no cover - defensive, engine doesn't currently return other statuses
            st.error(f"Unexpected engine status '{engine_result['status']}': {engine_result['message']}")
        return

    st.error(f"Unexpected internal state (stage='{stage}'). Please try again.")  # pragma: no cover


# ----------------------------------------------------------------------
# Streamlit page
# ----------------------------------------------------------------------

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

    user_text = st.text_area(
        "Describe your bathroom",
        placeholder=EXAMPLE_REQUEST,
        height=140,
        help="Include bathroom dimensions, budget, preferred style/finish, and the products you need.",
    )

    if st.button("Design My Bathroom", type="primary"):
        products_df = get_catalog()
        with st.spinner("Understanding your requirements and finding compatible products..."):
            result = run_pipeline(user_text, products_df, demo_mode=DEMO_MODE)
        st.session_state["pipeline_result"] = result

    if "pipeline_result" in st.session_state:
        st.divider()
        render_result(st.session_state["pipeline_result"])




if __name__ == "__main__":
    main()
