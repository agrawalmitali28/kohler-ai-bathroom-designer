# KOHLER AI Bathroom Designer & Planner (Prototype)

> **Disclaimer:** This is an independent, individual case-study prototype
> created for demonstration purposes only. It is **not** an official
> KOHLER product, and it is **not** affiliated with, endorsed by, or
> representative of KOHLER Co. All product data in `data/products.csv`
> is invented **demonstration data** and does not represent real KOHLER
> specifications, pricing, or availability.

## What this project is

An AI-assisted bathroom design assistant that:

1. Understands customer requirements (dimensions, budget, aesthetic theme, required products).
2. Uses an **existing** LLM API (no model training/fine-tuning) to convert natural language into structured JSON.
3. Uses deterministic Python logic to validate budget and spatial constraints.
4. Recommends compatible product bundles from a prototype catalog.
5. Generates a simple 2D bathroom layout.
6. Supports conversational "what-if" redesign.
7. Explains why products were selected.

## What this project is NOT

- Not a custom-trained or fine-tuned LLM — it only calls an existing hosted LLM API for natural-language understanding.
- Not using real KOHLER product specifications or pricing.
- Not built with React, Node.js, Unity, Unreal, Blender, Kubernetes, or similar infrastructure.

## Tech stack

- Python
- Streamlit
- Pandas / NumPy
- An existing LLM API (provider TBD — see `.env.example`)
- CSV product catalog
- HTML/SVG for 2D visualization

## Project structure

```
kohler-ai-bathroom-designer/
│
├── app.py                      # Streamlit entry point
├── requirements.txt            # Python dependencies
├── README.md                   # This file
├── .gitignore                  # Protects API keys and Python artifacts
├── .env.example                # Template for required environment variables
│
├── data/
│   └── products.csv            # Prototype/demo product catalog (~25 items)
│
├── src/
│   ├── __init__.py
│   ├── llm_parser.py           # NL -> structured JSON (LLM API calls)
│   ├── recommendation_engine.py# Deterministic product bundle selection
│   ├── layout_engine.py        # 2D layout generation (SVG)
│   └── validation.py           # Deterministic budget/spatial validation
│
├── prompts/
│   └── prompts.md              # LLM prompt templates
│
├── docs/                       # Supporting documentation
├── presentation/                # Slides / case-study materials
├── demo/                       # Demo scripts / recordings
└── assets/
    └── screenshots/            # App screenshots
```

## Current status: Phase 1 only

This phase only sets up the project scaffold and the prototype product
catalog. No LLM integration, recommendation logic, or UI has been built
yet — all `src/` modules contain placeholder functions that raise
`NotImplementedError`, and `app.py` shows a minimal placeholder screen.

## Setup (for later phases)

```bash
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # then fill in your real LLM API key
streamlit run app.py
```

## Data disclaimer

`data/products.csv` contains **invented, realistic-looking demonstration
data** for prototyping purposes only. It does not represent official
KOHLER specifications, pricing, dimensions, or product availability.
