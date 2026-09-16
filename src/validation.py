"""
validation.py
---------------
PHASE 1 STATUS: Placeholder only. Not implemented yet.

Purpose (future phase):
    Deterministic (non-LLM) Python logic to validate:
      - Budget constraints (sum of selected products vs. customer budget)
      - Spatial constraints (do selected products' footprints fit within
        the given bathroom dimensions, with reasonable clearances)

This module is intentionally free of any LLM calls — it exists to keep
rule-based checks reliable and explainable.
"""


def validate_budget(selected_products: list, budget_usd: float) -> dict:
    """
    Placeholder function. To be implemented in a later phase.

    Returns:
        A dict describing whether the selection is within budget, e.g.
        {"within_budget": bool, "total_cost": float, "remaining": float}.
    """
    raise NotImplementedError("Budget validation will be implemented in a later phase.")


def validate_spatial_fit(selected_products: list, room_dims: dict) -> dict:
    """
    Placeholder function. To be implemented in a later phase.

    Returns:
        A dict describing whether the selection fits the room, e.g.
        {"fits": bool, "issues": [...]}.
    """
    raise NotImplementedError("Spatial validation will be implemented in a later phase.")
