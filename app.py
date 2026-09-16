"""
KOHLER AI Bathroom Designer & Planner
--------------------------------------
Main Streamlit entry point.

PHASE 1 STATUS: Placeholder only.
No UI, LLM integration, or recommendation logic has been implemented yet.
This file exists so the project structure is runnable end-to-end once
later phases add real functionality.
"""

import streamlit as st


def main() -> None:
    st.set_page_config(page_title="KOHLER AI Bathroom Designer (Prototype)", layout="wide")

    st.title("KOHLER AI Bathroom Designer & Planner")
    st.caption("Prototype / demo build — not an official KOHLER product.")

    st.info(
        "Phase 1 scaffold only. UI, LLM parsing, recommendation logic, "
        "and layout generation will be added in later phases."
    )


if __name__ == "__main__":
    main()
