from __future__ import annotations


def architecture_to_requirements(
    architecture_data: dict,
    budget_inr: float,
    theme: str,
    required_categories: list[str],
) -> dict:
    """Convert USD/USDZ spatial data and user selections into requirements."""
    room = architecture_data.get("room", {})

    return {
        "length_ft": room.get("length_ft"),
        "width_ft": room.get("width_ft"),
        "budget_inr": budget_inr,
        "theme": theme,
        "required_categories": list(required_categories),
        "preferences": {
            "color": None,
            "water_efficiency_keyword": None,
            "feature_keywords": [],
        },
    }