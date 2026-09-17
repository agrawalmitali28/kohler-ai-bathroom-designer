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

import os

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
# Off by default. This is a prototype for a 2-day competition build, and
# the LLM API key may not be available in every environment the UI gets
# shown in (e.g. a judge's machine, a quick screen-share). When someone
# explicitly sets DEMO_MODE=true in their .env, the app skips the real
# LLM call and instead feeds one FIXED, clearly-labeled example request
# straight into validation -> adapter -> recommendation_engine, so the
# rest of the pipeline can still be demonstrated.
#
# This never silently substitutes for a missing API key: if DEMO_MODE is
# not explicitly enabled and the API key is missing, the app shows a
# real configuration error (see run_pipeline()) rather than pretending
# to work.
# ----------------------------------------------------------------------
DEMO_MODE = os.environ.get("DEMO_MODE", "false").strip().lower() == "true"

DEMO_FIXED_REQUEST_TEXT = (
    "[DEMO MODE — fixed example, not the text you typed] "
    "My bathroom is 5 by 4 feet. Budget is \u20b980,000. I need a toilet, basin and tap."
)
DEMO_FIXED_REQUIREMENTS = {
    "length_ft": 5,
    "width_ft": 4,
    "budget_inr": 80000,
    "theme": "Modern",
    "required_categories": ["Toilet", "Washbasin", "Faucet"],
    "preferences": {"color": None, "water_efficiency_keyword": None, "feature_keywords": []},
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
    if demo_mode:
        raw_requirements = DEMO_FIXED_REQUIREMENTS
    else:
        if not isinstance(user_text, str) or not user_text.strip():
            return {"stage": "empty_input"}

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
    """Render one product's full catalog details inside an expander."""
    with st.expander(f"{product.get('category', '')}: {product.get('product_name', 'Unnamed product')}"):
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"**SKU:** {_fmt(product.get('sku'))}")
            st.write(f"**Category:** {_fmt(product.get('category'))}")
            st.write(f"**Collection:** {_fmt(product.get('collection'))}")
            st.write(f"**Price:** {_fmt_inr(product.get('price'))}")
            st.write(f"**MRP:** {_fmt_inr(product.get('mrp'))}")
            st.write(f"**Installation type:** {_fmt(product.get('installation_type'))}")
        with col2:
            st.write(f"**Style:** {_fmt(product.get('style'))}")
            st.write(f"**Color:** {_fmt(product.get('color'))}")
            st.write(f"**Finish:** {_fmt(product.get('finish'))}")
            st.write(f"**Water efficiency:** {_fmt(product.get('water_efficiency'))}")
            st.write(f"**Flow rate (LPM):** {_fmt(product.get('flow_rate_lpm'))}")

        features = product.get("features")
        if features is not None and not (isinstance(features, float) and pd.isna(features)):
            st.write(f"**Features:** {features}")

        smart_features = product.get("smart_features")
        if smart_features is not None and not (isinstance(smart_features, float) and pd.isna(smart_features)):
            st.write(f"**Smart features:** {smart_features}")

        description = product.get("description")
        if description is not None and not (isinstance(description, float) and pd.isna(description)):
            st.write(description)

        source_url = product.get("source_url")
        if source_url and not (isinstance(source_url, float) and pd.isna(source_url)):
            st.link_button("View product on KOHLER India", source_url)


def _render_bundle(index: int, bundle: dict) -> None:
    st.subheader(f"Bundle #{index}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Compatibility Score", f"{bundle['compatibility_score']:.1f} / 100")
    col2.metric("Total price", _fmt_inr(bundle["total_price"]))
    col3.metric("Remaining budget", _fmt_inr(bundle["remaining_budget"]))

    breakdown = bundle["score_breakdown"]
    st.caption(
        f"Score breakdown \u2014 style: {breakdown['style_score']:.2f} \u00b7 "
        f"budget: {breakdown['budget_score']:.2f} \u00b7 "
        f"spatial: {breakdown['spatial_score']:.2f} \u00b7 "
        f"feature: {breakdown['feature_score']:.2f}"
    )

    for product in bundle["products"]:
        _render_product(product)

    st.divider()


def _render_missing_fields(completeness: dict) -> None:
    st.warning("Please provide a bit more information:")
    for field in completeness["missing_fields"]:
        st.write(f"- {FIELD_LABELS.get(field, field)}")


def _render_parsed_requirements(clean_requirements: dict) -> None:
    with st.expander("Parsed requirements (for demo/debug visibility)"):
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
    st.set_page_config(page_title="KOHLER AI Bathroom Designer (Prototype)", layout="wide")

    st.title("KOHLER AI Bathroom Designer & Planner")
    st.caption("Prototype / demo build \u2014 not an official KOHLER product.")
    st.write(
        "Describe the bathroom you want in your own words \u2014 dimensions, budget, "
        "style, and the fixtures you need \u2014 and this tool will turn it into a "
        "real, budget-checked KOHLER product bundle."
    )

    if DEMO_MODE:
        st.info(
            "\U0001F6E0\uFE0F DEMO MODE is on (DEMO_MODE=true). The LLM will NOT be called \u2014 "
            "a fixed example request is used instead. Set DEMO_MODE=false (or remove it) "
            "in your .env to use real natural-language input."
        )

    user_text = st.text_area(
        "Describe your bathroom",
        placeholder=EXAMPLE_REQUEST,
        height=120,
        disabled=DEMO_MODE,
    )

    if DEMO_MODE:
        st.caption(f"Fixed demo request that will be used: \u201c{DEMO_FIXED_REQUEST_TEXT}\u201d")

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
