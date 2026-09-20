from __future__ import annotations

import re
from pathlib import Path

from pxr import Usd


class USDParserError(Exception):
    """Raised when a USD/USDZ architecture file cannot be parsed."""


def _get_attribute(prim, name: str):
    """Return a USD attribute value, or None if it does not exist."""
    attribute = prim.GetAttribute(name)

    if not attribute:
        return None

    return attribute.Get()


def _parse_dimension_string(value):
    """
    Parse dimensions such as:
    '10 ft x 7 ft x 9 ft'
    '4 ft x 1.5 ft x 3 ft'
    """
    if not value:
        return None

    numbers = re.findall(r"\d+(?:\.\d+)?", str(value))

    if len(numbers) != 3:
        return None

    return {
        "width_ft": float(numbers[0]),
        "depth_ft": float(numbers[1]),
        "height_ft": float(numbers[2]),
    }


def parse_usd_file(file_path: str | Path) -> dict:
    """
    Parse a USD/USDZ bathroom architecture file.

    Returns a structured representation containing room
    information and recognized components.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise USDParserError(f"File not found: {file_path}")

    try:
        stage = Usd.Stage.Open(str(file_path))
    except Exception as exc:
        raise USDParserError(
            f"Could not open USD file: {exc}"
        ) from exc

    if stage is None:
        raise USDParserError(
            f"OpenUSD could not open: {file_path.name}"
        )

    default_prim = stage.GetDefaultPrim()

    if not default_prim:
        raise USDParserError(
            "The USD file does not contain a default bathroom prim."
        )

    room = {
        "type": "bathroom",
        "length_ft": None,
        "width_ft": None,
        "height_ft": None,
    }

    room_length = _get_attribute(default_prim, "roomLength")
    room_width = _get_attribute(default_prim, "roomWidth")
    room_height = _get_attribute(default_prim, "roomHeight")

    if room_length:
        match = re.search(r"\d+(?:\.\d+)?", str(room_length))
        if match:
            room["length_ft"] = float(match.group())

    if room_width:
        match = re.search(r"\d+(?:\.\d+)?", str(room_width))
        if match:
            room["width_ft"] = float(match.group())

    if room_height:
        match = re.search(r"\d+(?:\.\d+)?", str(room_height))
        if match:
            room["height_ft"] = float(match.group())

    components = []

    for prim in stage.Traverse():
        if prim == default_prim:
            continue

        component_type = _get_attribute(
            prim,
            "componentType",
        )

        dimensions = _get_attribute(
            prim,
            "dimensions",
        )

        if not component_type:
            continue

        component = {
            "name": prim.GetName(),
            "type": str(component_type),
            "dimensions": _parse_dimension_string(dimensions),
        }

        components.append(component)

    return {
        "source_file": file_path.name,
        "room": room,
        "components": components,
    }