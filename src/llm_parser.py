"""
llm_parser.py
--------------
PHASE 1 STATUS: Placeholder only. Not implemented yet.

Purpose (future phase):
    Take a free-text customer requirement (e.g. "I want a modern 6x8 ft
    bathroom with a smart toilet under $3000") and call an existing LLM
    API to convert it into structured JSON, such as:

    {
        "width_ft": 6,
        "depth_ft": 8,
        "budget_usd": 3000,
        "style": "modern",
        "required_products": ["smart_toilet"]
    }

This module will NOT train or fine-tune any model. It will only call an
existing hosted LLM API (e.g. OpenAI, Anthropic) for natural-language
understanding, per project constraints.
"""


def parse_requirements(user_text: str) -> dict:
    """
    Placeholder function. To be implemented in a later phase.

    Args:
        user_text: Free-text bathroom requirements from the customer.

    Returns:
        A dict of structured requirements (schema TBD in Phase 2).
    """
    raise NotImplementedError("LLM integration will be implemented in a later phase.")
