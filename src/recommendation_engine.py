"""
recommendation_engine.py
--------------------------
PHASE 1 STATUS: Placeholder only. Not implemented yet.

Purpose (future phase):
    Given structured requirements (from llm_parser.py) and validated
    constraints (from validation.py), select compatible KOHLER product
    bundles from data/products.csv using deterministic Python logic
    (pandas filtering/scoring) — no LLM involved in this step.
"""


def recommend_products(requirements: dict, product_catalog) -> list:
    """
    Placeholder function. To be implemented in a later phase.

    Args:
        requirements: Structured requirements dict.
        product_catalog: pandas DataFrame loaded from data/products.csv.

    Returns:
        A list of recommended product bundles (schema TBD in Phase 3).
    """
    raise NotImplementedError("Recommendation engine will be implemented in a later phase.")
