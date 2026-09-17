"""
llm_parser.py
--------------
PHASE 3A STATUS: Implemented.

Purpose:
    Convert a customer's free-text bathroom description into structured
    REQUIREMENTS ONLY — room dimensions, budget, theme, required product
    categories, and soft preferences.

------------------------------------------------------------------------
STRICT BOUNDARY: WHAT THE LLM IS NOT ALLOWED TO DO
------------------------------------------------------------------------
The LLM (via the Anthropic API) is used ONLY for natural-language
understanding of the customer's *requirements*. It NEVER:
    - selects a KOHLER product
    - invents a SKU, price, MRP, or product dimension
    - invents a product feature or specification
    - reads or reasons about data/products.csv
    - recommends a specific product

All of that remains the exclusive responsibility of the deterministic
`recommendation_engine.py`, which is the only source of truth for
products, prices, dimensions, specifications, and compatibility scoring.
This module hands recommendation_engine.py clean, validated requirements
— nothing more.
------------------------------------------------------------------------

Environment variables (loaded from a local .env file via python-dotenv):
    LLM_PROVIDER  - optional, defaults to "anthropic". This phase only
                    supports "anthropic"; any other value raises a
                    configuration error.
    LLM_API_KEY   - required. Your Anthropic API key. Never hard-code
                    this or print it in error messages.
    LLM_MODEL     - optional. Defaults to DEFAULT_MODEL below if unset.
"""

import json
import os

from dotenv import load_dotenv

# The Anthropic SDK is an optional-at-import-time dependency: importing
# it here would crash this whole module (and therefore anything that
# imports it, like a future app.py) if someone hasn't run
# `pip install -r requirements.txt` yet. Instead we defer the "is it
# installed?" check to parse_requirements(), so validation/helper
# functions in this file can still be tested without the package.
try:
    import anthropic
    _ANTHROPIC_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - exercised only when uninstalled
    anthropic = None
    _ANTHROPIC_IMPORT_ERROR = exc

# Load variables from a local .env file (if present) into the environment.
# This does nothing (and doesn't error) if no .env file exists.
load_dotenv()


# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

# Small, fast, inexpensive model — a good fit for a narrow structured
# extraction task like this one. Overridable via the LLM_MODEL env var.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

MAX_RESPONSE_TOKENS = 512

# The six allowed product categories, exactly as recommendation_engine.py
# and the real KOHLER catalog expect them. The LLM is instructed to
# normalize natural language ("sink", "tap", "basin", ...) into one of
# these; the Python validation below then filters out anything that
# isn't in this exact list, as a safety net.
ALLOWED_CATEGORIES = [
    "Toilet",
    "Smart Toilet",
    "Faucet",
    "Shower",
    "Vanity",
    "Washbasin",
]

# ----------------------------------------------------------------------
# System prompt
# ----------------------------------------------------------------------
# This is the exact prompt sent to the LLM on every call. It is mirrored
# (for human reading / editing reference) in prompts/prompts.md, but THIS
# constant is the one actually used at runtime, so there's no risk of the
# app silently using stale wording if the markdown file falls out of sync.
# ----------------------------------------------------------------------

SYSTEM_PROMPT = """You are a requirement-extraction assistant for a bathroom renovation planning tool.

Your ONLY job is to read a customer's free-text description of the bathroom they want, and extract structured REQUIREMENTS from it. You are not a product catalog and you are not a designer.

STRICT RULES:
1. Extract requirements only. Do not recommend, mention, or invent any specific product, brand, SKU, price, MRP, or dimension of a product.
2. Do not use, reference, or assume anything about a product catalog. You have no access to one and must not pretend otherwise.
3. Return ONLY the structured JSON described below. No prose, no explanation, no markdown code fences, no extra keys.
4. If a piece of information is not clearly stated or clearly implied by the customer's text, its value MUST be null (or an empty list/empty structure, as noted below). NEVER guess or invent a value that was not stated.
5. required_categories must contain ONLY values from this exact set: ["Toilet", "Smart Toilet", "Faucet", "Shower", "Vanity", "Washbasin"]. Normalize natural language into these categories, for example:
   - "toilet" -> "Toilet"
   - "smart toilet" / "smart cleansing seat" -> "Smart Toilet"
   - "basin" / "sink" -> "Washbasin"
   - "tap" / "faucet" -> "Faucet"
   - "shower" / "shower system" -> "Shower"
   - "vanity" / "vanity cabinet" -> "Vanity"
   Do not invent categories outside this set. If a requested item doesn't clearly map to one of these six, omit it.
6. Room dimensions must be expressed in FEET as plain numbers (e.g. 8, 6.5). If the customer gives dimensions in another unit, convert to feet.
7. Budget must be normalized to a single plain numeric value in INR (Indian Rupees), with no currency symbol, no commas, no words. Understand Indian numbering expressions such as "2 lakh", "2 lakhs", "1.5 lakh", "₹2,00,000", "200000 INR" and convert all of these to a plain number (e.g. "2 lakh" -> 200000, "1.5 lakh" -> 150000).
8. theme should be a short style word/phrase taken from what the customer said (e.g. "Modern", "Classic Luxury"). If no style/theme is mentioned, theme must be null. Do not invent a theme.

Return EXACTLY this JSON structure and nothing else:

{
    "length_ft": number or null,
    "width_ft": number or null,
    "budget_inr": number or null,
    "theme": string or null,
    "required_categories": [],
    "preferences": {
        "color": string or null,
        "water_efficiency_keyword": string or null,
        "feature_keywords": []
    }
}

Only fill in "preferences" sub-fields if the customer clearly expressed that preference (e.g. a specific color, a water-saving requirement, or a specific feature like "heated seat"). Otherwise leave them null / empty list.

Respond with ONLY the JSON object. Nothing before it, nothing after it."""


# ----------------------------------------------------------------------
# Custom exceptions
# ----------------------------------------------------------------------
# Using specific exception types (instead of generic Exception/ValueError)
# lets a later UI (Phase 3C) show a tailored, user-friendly message for
# each failure mode instead of a raw traceback.
# ----------------------------------------------------------------------

class LLMParserError(Exception):
    """Base class for all errors raised by this module."""


class LLMConfigurationError(LLMParserError):
    """Raised when the LLM cannot even be called: missing/invalid setup
    (no API key, unsupported provider, SDK not installed)."""


class LLMRequestError(LLMParserError):
    """Raised when the API call itself fails (network error, timeout,
    rate limit, non-2xx response from Anthropic)."""


class LLMResponseValidationError(LLMParserError):
    """Raised when the LLM responded, but its output isn't valid/usable
    structured data (not JSON, missing keys, wrong types, invalid
    values). We never silently repair this by inventing values —
    we raise instead."""


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------

def parse_requirements(user_text: str) -> dict:
    """
    Convert a free-text bathroom description into structured requirements
    by calling the Anthropic API, then validating the result.

    Args:
        user_text: Free-text bathroom requirements from the customer.

    Returns:
        A dict matching this exact structure:
        {
            "length_ft": number or None,
            "width_ft": number or None,
            "budget_inr": number or None,
            "theme": string or None,
            "required_categories": [ ... ],   # subset of ALLOWED_CATEGORIES
            "preferences": {
                "color": string or None,
                "water_efficiency_keyword": string or None,
                "feature_keywords": [ ... ],
            },
        }

    Raises:
        LLMConfigurationError: missing API key, unsupported provider, or
            the anthropic package isn't installed.
        LLMRequestError: the API call failed (network/timeout/rate-limit/
            server error).
        LLMResponseValidationError: the LLM's response wasn't usable
            structured data (bad JSON, wrong types, invalid values).
    """
    if not isinstance(user_text, str) or not user_text.strip():
        raise LLMResponseValidationError(
            "user_text must be a non-empty string describing the bathroom requirements."
        )

    client = _build_client()
    raw_response_text = _call_llm(client, user_text)
    parsed = _extract_json_from_response_text(raw_response_text)
    validated = _validate_and_normalize(parsed)
    return validated


# ----------------------------------------------------------------------
# Step 1: build the API client (configuration check)
# ----------------------------------------------------------------------

def _build_client():
    """
    Read configuration from environment variables and construct an
    Anthropic client. Raises LLMConfigurationError for anything that
    stops us before we can even attempt an API call.
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()
    if provider != "anthropic":
        raise LLMConfigurationError(
            f"Unsupported LLM_PROVIDER '{provider}'. Phase 3A only supports 'anthropic'."
        )

    if _ANTHROPIC_IMPORT_ERROR is not None:
        raise LLMConfigurationError(
            "The 'anthropic' package is not installed. Run "
            "`pip install -r requirements.txt` and try again."
        )

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise LLMConfigurationError(
            "LLM_API_KEY is not set. Copy .env.example to .env and fill in "
            "your Anthropic API key, then try again."
        )

    return anthropic.Anthropic(api_key=api_key)


# ----------------------------------------------------------------------
# Step 2: call the LLM
# ----------------------------------------------------------------------

def _call_llm(client, user_text: str) -> str:
    """
    Call the Anthropic Messages API and return the raw text of the
    response. Wraps SDK-specific exceptions into our own error types so
    callers (and a future UI) don't need to know about the anthropic
    package's exception hierarchy. Never includes the API key in any
    error message.
    """
    model = os.environ.get("LLM_MODEL") or DEFAULT_MODEL

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_RESPONSE_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.AuthenticationError as exc:
        raise LLMConfigurationError(
            "The configured LLM_API_KEY was rejected by Anthropic (authentication "
            "failed). Double-check the key in your .env file."
        ) from exc
    except anthropic.APITimeoutError as exc:
        raise LLMRequestError(
            "The request to the Anthropic API timed out. Please try again."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise LLMRequestError(
            "Could not connect to the Anthropic API. Check your network connection."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise LLMRequestError(
            "The Anthropic API rate limit was hit. Please wait a moment and try again."
        ) from exc
    except anthropic.APIStatusError as exc:
        raise LLMRequestError(
            f"The Anthropic API returned an error (status {exc.status_code}). "
            "Please try again later."
        ) from exc
    except Exception as exc:  # last-resort catch-all, still no key exposure
        raise LLMRequestError(
            f"An unexpected error occurred while calling the LLM API: {type(exc).__name__}"
        ) from exc

    # Concatenate all text blocks in the response (normally there's just one).
    text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    full_text = "".join(text_parts).strip()

    if not full_text:
        raise LLMResponseValidationError("The LLM returned an empty response.")

    return full_text


# ----------------------------------------------------------------------
# Step 3: parse JSON out of the response text
# ----------------------------------------------------------------------

def _extract_json_from_response_text(text: str) -> dict:
    """
    Parse the LLM's response text as JSON. Tolerates the model wrapping
    the JSON in a ```json ... ``` code fence even though the prompt asks
    it not to, since that's a common, harmless model habit.

    Raises:
        LLMResponseValidationError if the text isn't valid JSON, or the
        parsed JSON isn't a dict.
    """
    cleaned = text.strip()

    if cleaned.startswith("```"):
        # Strip a leading ```json or ``` line, and a trailing ``` line.
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMResponseValidationError(
            "The LLM did not return valid JSON. Please try rephrasing your request."
        ) from exc

    if not isinstance(parsed, dict):
        raise LLMResponseValidationError(
            "The LLM's JSON response was not a JSON object as expected."
        )

    return parsed


# ----------------------------------------------------------------------
# Step 4: validate and normalize the parsed structure
# ----------------------------------------------------------------------

REQUIRED_TOP_LEVEL_KEYS = [
    "length_ft", "width_ft", "budget_inr", "theme",
    "required_categories", "preferences",
]


def _validate_and_normalize(parsed: dict) -> dict:
    """
    Validate the LLM's parsed JSON against the required schema and
    return a clean, normalized dict. Never invents a missing value —
    if something required is missing or clearly wrong, this raises
    LLMResponseValidationError instead of guessing.

    Args:
        parsed: The dict produced by _extract_json_from_response_text().

    Returns:
        A validated dict, with:
          - numeric fields as int/float or None
          - theme as str or None
          - required_categories as a list containing only values from
            ALLOWED_CATEGORIES (unrecognized values are dropped, not
            treated as an error, since this is a normalization step)
          - preferences as a dict with color/water_efficiency_keyword/
            feature_keywords always present (defaulting to None/[] if
            the LLM omitted the whole sub-structure — that's completing
            the *shape*, not inventing a *value*)
    """
    missing_keys = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in parsed]
    if missing_keys:
        raise LLMResponseValidationError(
            f"The LLM's response is missing required field(s): {', '.join(missing_keys)}."
        )

    length_ft = _validate_nonnegative_number(parsed["length_ft"], "length_ft")
    width_ft = _validate_nonnegative_number(parsed["width_ft"], "width_ft")
    budget_inr = _validate_nonnegative_number(parsed["budget_inr"], "budget_inr")

    theme = parsed["theme"]
    if theme is not None and not isinstance(theme, str):
        raise LLMResponseValidationError("theme must be a string or null.")

    required_categories = parsed["required_categories"]
    if not isinstance(required_categories, list):
        raise LLMResponseValidationError("required_categories must be a list.")

    # Keep only the six allowed categories, drop anything else, and
    # remove duplicates while preserving the order the LLM returned them in.
    seen = set()
    normalized_categories = []
    for category in required_categories:
        if isinstance(category, str) and category in ALLOWED_CATEGORIES and category not in seen:
            normalized_categories.append(category)
            seen.add(category)

    preferences = parsed["preferences"]
    if preferences is None:
        preferences = {}
    if not isinstance(preferences, dict):
        raise LLMResponseValidationError("preferences must be an object (or null).")

    color = preferences.get("color")
    if color is not None and not isinstance(color, str):
        raise LLMResponseValidationError("preferences.color must be a string or null.")

    water_efficiency_keyword = preferences.get("water_efficiency_keyword")
    if water_efficiency_keyword is not None and not isinstance(water_efficiency_keyword, str):
        raise LLMResponseValidationError(
            "preferences.water_efficiency_keyword must be a string or null."
        )

    feature_keywords = preferences.get("feature_keywords", [])
    if feature_keywords is None:
        feature_keywords = []
    if not isinstance(feature_keywords, list) or not all(isinstance(f, str) for f in feature_keywords):
        raise LLMResponseValidationError(
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


def _validate_nonnegative_number(value, field_name: str):
    """
    Confirm `value` is either None, or a non-negative int/float.

    Booleans are explicitly rejected even though `bool` is technically a
    subclass of `int` in Python — a True/False value here would indicate
    a confused LLM response, not a real measurement or budget.

    Raises LLMResponseValidationError on anything else (wrong type, or
    a negative number).
    """
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMResponseValidationError(f"{field_name} must be a number or null.")

    if value < 0:
        raise LLMResponseValidationError(f"{field_name} must not be negative.")

    return value


# ----------------------------------------------------------------------
# Simple demonstration / test section
# ----------------------------------------------------------------------
# Run this file directly to see example output:
#     python3 src/llm_parser.py
#
# This does NOT require Streamlit. If LLM_API_KEY isn't configured, the
# live-API tests print a clear explanation instead of crashing, and the
# validation-only tests (which don't call the API at all) still run.
# ----------------------------------------------------------------------

LIVE_TEST_CASES = [
    "I have an 8 by 6 feet bathroom, budget of 2 lakh, modern style. "
    "I want a smart toilet, shower and vanity.",

    "My bathroom is 5 by 4 feet. Budget is \u20b980,000. I need a toilet, basin and tap.",

    "I want a luxury modern bathroom with a smart toilet.",
]


def _print_result(test_name: str, result: dict) -> None:
    print(f"--- {test_name} ---")
    print(json.dumps(result, indent=2))
    print()


def _run_live_api_tests() -> None:
    print("=" * 70)
    print("LIVE API TESTS (calls the real Anthropic API)")
    print("=" * 70)

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        print(
            "Skipping live API tests: LLM_API_KEY is not set.\n"
            "Copy .env.example to .env, add your Anthropic API key as "
            "LLM_API_KEY, and re-run this script to test real parsing.\n"
        )
        return

    for i, text in enumerate(LIVE_TEST_CASES, start=1):
        print(f"Input {i}: {text}")
        try:
            result = parse_requirements(text)
            _print_result(f"Test {i} result", result)
        except LLMParserError as exc:
            print(f"Test {i} FAILED with {type(exc).__name__}: {exc}\n")


def _run_offline_validation_tests() -> None:
    """
    These tests exercise the JSON-parsing and validation logic directly,
    with hand-built inputs, WITHOUT calling the Anthropic API. They run
    regardless of whether LLM_API_KEY is configured, so the core
    validation logic is always verifiable.
    """
    print("=" * 70)
    print("OFFLINE VALIDATION TESTS (no API call)")
    print("=" * 70)

    # 1. A well-formed response should pass straight through.
    good_response_text = json.dumps({
        "length_ft": 8,
        "width_ft": 6,
        "budget_inr": 200000,
        "theme": "Modern",
        "required_categories": ["Smart Toilet", "Shower", "Vanity"],
        "preferences": {"color": None, "water_efficiency_keyword": None, "feature_keywords": []},
    })
    parsed = _extract_json_from_response_text(good_response_text)
    result = _validate_and_normalize(parsed)
    _print_result("Well-formed response", result)

    # 2. Markdown code fences around the JSON should be tolerated.
    fenced_response_text = "```json\n" + good_response_text + "\n```"
    parsed = _extract_json_from_response_text(fenced_response_text)
    result = _validate_and_normalize(parsed)
    print("Markdown-fenced JSON parsed successfully:", result == _validate_and_normalize(
        _extract_json_from_response_text(good_response_text)
    ))
    print()

    # 3. An invalid category should be silently dropped, not cause a crash.
    response_with_bad_category = json.dumps({
        "length_ft": None,
        "width_ft": None,
        "budget_inr": None,
        "theme": None,
        "required_categories": ["Smart Toilet", "Jacuzzi"],  # "Jacuzzi" is not allowed
        "preferences": {},
    })
    parsed = _extract_json_from_response_text(response_with_bad_category)
    result = _validate_and_normalize(parsed)
    _print_result("Invalid category filtered out", result)

    # 4. A negative budget must be rejected, not silently clamped to 0.
    response_with_negative_budget = json.dumps({
        "length_ft": 8, "width_ft": 6, "budget_inr": -500, "theme": None,
        "required_categories": [], "preferences": {},
    })
    parsed = _extract_json_from_response_text(response_with_negative_budget)
    try:
        _validate_and_normalize(parsed)
        print("Negative budget test: FAILED (should have raised an error)\n")
    except LLMResponseValidationError as exc:
        print(f"Negative budget test: correctly rejected -> {exc}\n")

    # 5. Malformed (non-JSON) text must be rejected, not guessed at.
    try:
        _extract_json_from_response_text("Sure! Here's your bathroom plan: ...")
        print("Malformed JSON test: FAILED (should have raised an error)\n")
    except LLMResponseValidationError as exc:
        print(f"Malformed JSON test: correctly rejected -> {exc}\n")

    # 6. A missing required top-level key must be rejected.
    response_missing_key = json.dumps({
        "length_ft": 8, "width_ft": 6, "budget_inr": 100000, "theme": None,
        "required_categories": [],
        # "preferences" key is missing entirely
    })
    parsed = _extract_json_from_response_text(response_missing_key)
    try:
        _validate_and_normalize(parsed)
        print("Missing key test: FAILED (should have raised an error)\n")
    except LLMResponseValidationError as exc:
        print(f"Missing key test: correctly rejected -> {exc}\n")


if __name__ == "__main__":
    _run_offline_validation_tests()
    _run_live_api_tests()
