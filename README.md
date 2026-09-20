# 🚿 KOHLER AI Bathroom Designer

> An AI-assisted bathroom planning and product recommendation prototype that converts natural-language requirements or USD/USDZ bathroom architecture into coordinated, budget-aware KOHLER product recommendations.

**Repository:** https://github.com/agrawalmitali28/kohler-ai-bathroom-designer

---

## 🔗 Submission Links

Add the final links below before submitting the project:

- 🎥 **Demo Video:** (https://www.youtube.com/watch?v=1_AfSvL3mpU)
- 📊 Presentation [View Presentation](presentation/presentation.pdf)
- 📄 Prompts & AI Documentation [View Prompts Documentation](prompts/prompts-documentation.pdf)

> Tip: If these files are uploaded to Google Drive, GitHub Releases, or another public location, make sure the sharing permissions allow the evaluators to open them.

---

## 💡 Problem

Bathroom planning often requires users to balance:

- Bathroom dimensions and available space
- Product compatibility
- Budget constraints
- Style preferences
- Multiple bathroom fixtures
- Real product availability

Users may know what kind of bathroom they want without knowing which products fit together or stay within their budget.

---

## 💡 Solution

**KOHLER AI Bathroom Designer** acts as a bathroom planning assistant.

The system:

1. Understands the user's bathroom requirements.
2. Extracts structured requirements such as dimensions, budget, style, products, and preferences.
3. Uses real KOHLER catalog data instead of invented product information.
4. Recommends coordinated product bundles.
5. Checks budget and spatial compatibility.
6. Visualizes the proposed bathroom layout.
7. Allows **What-If redesigns** such as changing the budget, style, products, or preferences.
8. Supports **USD/USDZ architecture input** to understand the physical bathroom space.

---

## ✨ Key Features

### 📝 1. Natural-Language Bathroom Description

Users can describe their bathroom in their own words, for example:

> "I have an 8 by 6 feet bathroom with a budget of ₹2 lakh. I want a modern minimalist bathroom with a smart toilet, water-saving products and a white finish."

The system converts the description into structured requirements.

---

### 🤖 2. AI Requirement Parsing

The live architecture uses an LLM requirement parser to extract:

- Bathroom length
- Bathroom width
- Budget
- Theme/style
- Required product categories
- Color preferences
- Water-efficiency preferences
- Feature preferences

The AI layer is responsible for understanding requirements — **not for inventing KOHLER products, prices, SKUs, or catalog specifications.**

---

### 🛠️ 3. No-Cost DEMO_MODE

The project includes a deterministic local parser for demonstrations.

When:

```text
DEMO_MODE=true
```

the application does not make a paid LLM API call.

Instead, the local parser extracts supported requirements and sends them through the **same validation → adapter → recommendation pipeline**.

This makes the prototype demonstrable without requiring paid API credits.

---

### 🏠 4. USD / USDZ Architecture Input

Users can provide a bathroom architecture file in:

- `.usd`
- `.usda`
- `.usdc`
- `.usdz`

The OpenUSD parser extracts physical-space information such as:

- Room dimensions
- Architectural components
- Component dimensions
- Spatial context

### Important design principle

The architecture file describes **the physical bathroom**, not what KOHLER products the user wants.

For example, if the USD file already contains a toilet, that toilet is treated as spatial information. It does **not** automatically mean the user wants a KOHLER toilet.

The user explicitly selects the product categories required for the design.

---

### 🎯 5. Product Category Selection

Supported categories:

- Toilet
- Smart Toilet
- Washbasin
- Faucet
- Shower
- Vanity

The selected categories are passed into the existing recommendation pipeline.

---

### 💰 6. Budget-Aware Recommendations

The recommendation engine considers the user's budget while generating product bundles.

Results include information such as:

- Product price
- Total bundle price
- Remaining budget
- Compatibility score
- Product details
- Why the products were selected

---

### 🛁 7. Real KOHLER Catalog

The project uses a curated KOHLER India catalog stored in:

```text
data/products.csv
```

The catalog contains real product information including:

- SKU
- Product name
- Category
- Collection
- Price
- MRP
- Dimensions
- Installation type
- Color
- Finish
- Style
- Water efficiency
- Flow rate
- Features
- Smart features
- Description
- Official source URL
- Verification date

The recommendation engine uses this catalog as the source of product information.

---

### 🔄 8. What-If Redesign

After receiving a design, users can explore changes without starting from scratch.

Examples:

> "Increase the budget to ₹2.5 lakh."

> "Make the bathroom more luxurious."

> "Remove the vanity."

> "Add a shower."

The system applies only the explicitly requested changes and reruns the same recommendation pipeline.

---

### 📐 9. Conceptual Bathroom Layout

The application generates a local conceptual bathroom visualization based on:

- Room dimensions
- Selected/recommended product categories
- Spatial constraints

The visualization is intended for planning and demonstration.

> It is not an architectural, construction, or installation drawing.

---

## 🧠 System Architecture

### Natural-Language Flow

```text
User Bathroom Description
          │
          ▼
Requirement Parser
(LLM / DEMO_MODE Parser)
          │
          ▼
Structured Requirements
          │
          ▼
Validation
          │
          ▼
Recommendation Adapter
          │
          ▼
KOHLER Recommendation Engine
          │
          ▼
Real KOHLER Catalog
          │
          ▼
Recommended Product Bundles
          │
          ▼
Layout + What-If Redesign
```

### USD / USDZ Flow

```text
USD / USDZ Architecture
          │
          ▼
OpenUSD Parser
          │
          ├── Room Dimensions
          └── Architectural Components
                    │
                    ▼
        User Product Selection
                    +
              Budget + Style
                    │
                    ▼
          Structured Requirements
                    │
                    ▼
                Validation
                    │
                    ▼
       Existing Recommendation Engine
                    │
                    ▼
          KOHLER Product Bundles
                    │
                    ▼
             Bathroom Layout
```

---

## 🧩 Project Structure

```text
kohler-ai-bathroom-designer/
│
├── app.py
│
├── data/
│   └── products.csv
│
├── src/
│   ├── llm_parser.py
│   ├── validation.py
│   ├── architecture_adapter.py
│   ├── usd_parser.py
│   └── recommendation_engine.py
│
├── prompts/
│   └── prompts.md
│
├── requirements.txt
├── .env
├── .gitignore
└── README.md
```

---

## ⚙️ Technology Stack

| Area | Technology |
|---|---|
| Frontend / UI | Streamlit |
| Language | Python |
| AI / LLM | Anthropic Claude |
| Demo fallback | Deterministic local parser |
| Architecture | OpenUSD / USDZ |
| Data processing | Pandas |
| Product recommendation | Custom Python recommendation engine |
| Product data | KOHLER India catalog |
| Environment | Python + dotenv |
| Version control | Git / GitHub |

---

## 🔄 Core Processing Pipeline

The application separates the responsibilities of each component:

```text
User Intent
    ↓
Requirement Parsing
    ↓
Validation
    ↓
Requirement Adapter
    ↓
Recommendation Engine
    ↓
KOHLER Catalog
    ↓
Product Bundles
    ↓
Visualization
```

### Responsibility separation

**AI / parser**

Understands the user's request.

**USD parser**

Understands physical architecture and spatial information.

**Validation**

Checks whether the structured requirements are usable.

**Adapter**

Converts validated requirements into the interface expected by the recommendation engine.

**Recommendation engine**

Selects products from the catalog.

**Streamlit UI**

Presents requirements, recommendations, layout, and What-If results.

---

## 🧪 Example Requirement

```text
I have an 8 by 6 feet bathroom with a budget of ₹2 lakh.
I want a modern minimalist bathroom with a smart toilet,
water-saving products and a white finish.
```

Conceptually, this becomes:

```json
{
  "length_ft": 8,
  "width_ft": 6,
  "budget_inr": 200000,
  "theme": "Modern Minimalist",
  "required_categories": [
    "Smart Toilet"
  ],
  "preferences": {
    "color": "white",
    "water_efficiency_keyword": "water-saving",
    "feature_keywords": [
      "smart"
    ]
  }
}
```

The recommendation engine then works with the structured requirements and the real catalog.

---

## 📊 Recommendation Output

A successful recommendation contains product bundles with information such as:

- Compatibility score
- Total product cost
- Remaining budget
- Recommended products
- Product category
- KOHLER collection
- Finish and color
- Installation type
- Dimensions
- Features
- Smart features
- Official KOHLER India product link

---

## 🔐 Environment Configuration

Create a local `.env` file:

```env
LLM_PROVIDER=anthropic
LLM_API_KEY=YOUR_API_KEY
LLM_MODEL=claude-haiku-4-5-20251001
APP_ENV=development
DEMO_MODE=true
```

### For the competition demo

Use:

```env
DEMO_MODE=true
```

This avoids the paid LLM call.

For live natural-language LLM processing:

```env
DEMO_MODE=false
```

> Never commit your real API key to GitHub.

---

## ▶️ Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/agrawalmitali28/kohler-ai-bathroom-designer.git
cd kohler-ai-bathroom-designer
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

### 3. Activate it on Windows

```bash
.venv\Scripts\activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure `.env`

Set:

```env
DEMO_MODE=true
```

### 6. Start Streamlit

```bash
streamlit run app.py
```

The application will open in the browser.

---

## 📦 OpenUSD Support

The project uses the Python OpenUSD bindings.

Install:

```bash
pip install usd-core
```

The architecture parser uses:

```python
from pxr import Usd
```

Supported input formats include:

```text
.usd
.usda
.usdc
.usdz
```

---

## 🧠 AI Prompt Documentation

The project's AI instructions and workflow documentation are provided separately in the submission PDF.

**Prompts / AI Documentation PDF:**

```text
[PASTE YOUR PROMPTS PDF LINK HERE]
```

The documentation covers:

- Requirement extraction
- Structured output schema
- DEMO_MODE behavior
- What-If parsing
- USD/USDZ architecture workflow
- Validation
- Recommendation flow
- Reproducibility

---

## 🎥 Demo

### Demo Video

```text
[PASTE YOUR DEMO VIDEO LINK HERE]
```

Recommended demo flow:

1. Introduce the problem.
2. Show the bathroom input.
3. Show requirement understanding.
4. Show KOHLER recommendations.
5. Show product details and budget.
6. Show the conceptual bathroom layout.
7. Demonstrate a What-If redesign.
8. Show USD/USDZ architecture input if included in the final demo.

---

## 📊 Presentation

### Competition Presentation

```text
[PASTE YOUR PRESENTATION LINK HERE]
```

---

## 🏆 Project Highlights

- Natural-language bathroom planning
- Real KOHLER catalog integration
- Budget-aware product recommendations
- Requirement validation
- What-If redesign
- Conceptual bathroom visualization
- USD/USDZ architecture parsing
- Separation between spatial context and product intent
- No-cost deterministic DEMO_MODE
- Streamlit-based interactive prototype

---

## ⚠️ Disclaimer

This is a competition/prototype project and is **not an official KOHLER product or service**.

The generated bathroom layout is conceptual and should not be used as an architectural, construction, plumbing, or installation plan.

Product information is based on the catalog data included with this prototype and should be verified against current official KOHLER sources before real-world purchase or installation.

---

## 👩‍💻 Author

**Mitali Agrawal**

B.Tech — Computer Science Engineering

GitHub:  
https://github.com/agrawalmitali28
