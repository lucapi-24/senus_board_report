# Senus PLC — AI-native Board Report

An AI-native, full-stack platform that prepares a Board Report for **Senus PLC**
(Natural Capital management software, Euronext Access Dublin). It extracts financial
information from the company's source documents into a database, models the figures,
derives board-grade KPIs, and presents them through an interactive, CEO-facing dashboard
with AI-generated commentary.

It was built as the *Technology Graduate Assessment — Board Report* (Assiduous, 2026).

---

## Quick start (Docker)

Requires Docker with the Compose plugin.

```bash
# 1. Provide the one required secret (never committed)
cp backend/.env.example backend/.env
#    then edit backend/.env and set GROQ_API_KEY=<your key from https://console.groq.com>

# 2. Build and run the full stack
docker compose up --build
```

| Service   | URL                              | Description                          |
|-----------|----------------------------------|--------------------------------------|
| Frontend  | http://localhost:8080            | React dashboard (login → board view) |
| API       | http://localhost:8000/docs       | FastAPI + OpenAPI docs               |
| Postgres  | localhost:5432                   | Database (`senus_board`)             |

The database is seeded automatically the first time the API starts (from the
committed extraction JSON). Login is a demo gate — **any credentials work**.

---

## Architecture

```
                         ┌──────────────────────────────────────────┐
                         │              Docker Compose              │
                         │                                          │
  Browser ──HTTP──▶ ┌────┴─────┐   /api/*   ┌───────────┐         ┌─┴─────────┐
                   │ frontend │ ──proxy──▶ │   api     │ ──SQL──▶│ postgres  │
                   │ nginx    │            │ FastAPI   │ asyncpg │ 16-alpine │
                   │ :80 →8080│            │ :8000     │         │  :5432    │
                   └──────────┘            └─────┬─────┘         └───────────┘
                                                 │
                                          AI (Groq LLM)
                                       insights / narrative
```

**Offline extraction pipeline** (run on demand, not at runtime):

```
source PDF ─▶ pdfplumber (text) ─▶ regex line-item parser ─▶ derived subtotals
           ─▶ sign normalisation ─▶ compute_kpis ─▶ extracted.json ─▶ PostgreSQL
```

The frontend is a static Vite build served by nginx, which also reverse-proxies
`/api/*` to the backend. The API is stateless; all state lives in Postgres.

---

## Technologies

| Layer      | Choice                                   | Why                                                        |
|------------|------------------------------------------|------------------------------------------------------------|
| Frontend   | React 19 + TypeScript + Vite             | Fast DX, typed API contracts, small bundle                 |
| Charts     | Recharts                                 | Declarative, responsive React charts                       |
| Styling    | Hand-written CSS with design tokens      | Exact match to senus.com brand, no framework overhead      |
| Backend    | FastAPI + Pydantic v2                    | Async, typed schemas, auto OpenAPI docs                    |
| ORM / DB   | SQLAlchemy 2 (async) + asyncpg + Postgres| Typed models, real relational store, async I/O             |
| AI         | Groq (model `openai/gpt-oss-120b`)       | Fast, free-tier LLM for commentary and narrative           |
| Extraction | pdfplumber + regex                       | Deterministic, auditable figures (LLM reserved for prose)  |
| Serving    | nginx (frontend) + uvicorn (API)         | Standard, containerised                                    |
| Tooling    | pytest, Vitest, GitHub Actions, Docker   | Tested and reproducible                                    |

---

## Data model

| Table                 | Key columns                                              | Purpose                          |
|-----------------------|----------------------------------------------------------|----------------------------------|
| `periods`             | `label`, `start_date`, `end_date`, `source_doc_id`       | A reporting period (HY2026, HY2025, FY2025) |
| `pnl_items`           | `period_id`, `line_item`, `value_eur`                    | Income statement lines           |
| `balance_sheet_items` | `period_id`, `line_item`, `value_eur`                    | Balance sheet lines              |
| `cash_flow_items`     | `period_id`, `line_item`, `value_eur`                    | Cash flow lines                  |
| `kpis`                | `period_id`, `metric`, `value`                           | Computed KPIs                    |
| `documents`           | `filename`, `period_label`, `raw_json`                   | Source provenance + raw payload  |

The schema is versioned with **Alembic**. The API container runs `alembic upgrade head`
before starting, so the database evolves through committed migrations instead of being
recreated. To add a schema change:

```bash
docker compose run --rm api alembic revision --autogenerate -m "describe change"
docker compose run --rm api alembic upgrade head
```

---

## The AI extraction pipeline

`backend/scripts/extract_pdf.py` turns the source PDF into structured rows:

1. **Text extraction** — `pdfplumber`. Table mode proved unreliable on the two-column
   layout, so text + regex is used instead (deterministic and reviewable).
2. **Line-item parsing** — labelled regex patterns per statement. Handles the report's
   real quirks: labels wrapping onto the next line (`Creditors: ... after more than one\n year`),
   suffixes (`Called up share capital presented as equity`), and unlabelled subtotal rows
   (`Total Fixed Assets`, `Total Current Assets`, `EBITDA`) which are derived from their components.
   The report is two-column (current + comparative); the parser reads either column, so the
   **HY2025 comparative is extracted from the same document** for a true like-for-like YoY.
3. **Sign normalisation** — the source prints costs/losses as positive; losses are stored negative.
4. **KPI computation** — board metrics computed from the normalised statements (see below).
5. **Narrative insights** — the LLM reads the narrative sections and returns structured
   qualitative commentary (drivers, cost structure, milestones, pipeline, risks).

At request time, `/api/insights/{period}` asks the LLM for CFO-grade commentary grounded in
the period's stored figures (with a retry and a tolerant JSON normaliser).

**Why not let the LLM extract the numbers?** Financial figures must be exact and auditable.
Deterministic parsing is reproducible and unit-testable; the LLM is used where it adds value —
interpreting and explaining the numbers, not inventing them.

---

## KPIs & methodology

Computed in `compute_kpis` (`backend/scripts/extract_pdf.py`):

- **Profitability** — gross / operating / EBITDA / net margin, admin-expense ratio.
- **Cash & liquidity** — FCF (operating cash flow + capex), monthly burn, cash runway,
  net cash, current ratio (with and without contingent consideration).
- **Solvency & leverage** — equity ratio, intangibles ratio, debt-to-equity, interest cover,
  DSCR (only when cash available for debt service is positive).
- **Returns** — ROCE (EBIT ÷ capital employed, where capital employed = total assets −
  current liabilities), annualised ROCE, ROE.

HY2026 (six months to 31 Dec 2025) headline results:

| Metric            | Value                     |
|-------------------|---------------------------|
| Revenue           | €354,813                  |
| Gross margin      | 81.7%                     |
| Operating margin  | −136.3%                   |
| EBITDA margin     | −133.5%                   |
| Cash runway       | 10.5 months               |
| Current ratio     | 0.75× (2.39× ex-contingent) |
| ROCE              | −75.9% (sem.) / −151.8% (ann.) |
| ROE               | −86.5%                    |
| DSCR              | n/a (negative operating cash flow) |

---

## Frontend

Single-page dashboard (state-based auth, no router): period selector, KPI cards,
YoY comparison and margin charts, EBITDA→FCF bridge, revenue/profit trend, and
AI insight cards. Styled as an extension of senus.com (dark green / teal / peach
palette, Figtree typography).

---

## AI-assisted development workflow

AI tools were used throughout, with the developer reviewing and standing behind every output:

- **Scaffolding & implementation** — AI assisted with component/API scaffolding and SQLAlchemy models.
- **The product itself uses AI** — Groq drives narrative extraction and on-demand insights.
- **Debugging** — AI helped trace an empty-statements API bug (Pydantic field/ORM attribute
  name mismatch) and a non-deterministic insights parser.
- **Reconciliation** — AI generated a reference breakdown used to cross-check extracted
  figures (see Validation); discrepancies were investigated and resolved manually.

All financial formulas and extracted values were verified against the source document and
locked down with golden-value tests — no number in the app is taken on the model's word.

---

## Validation

1. **Source reconciliation** — extracted figures were cross-checked against an independently
   produced reference breakdown of the same report. Raw line items matched; several KPI
   formulas were found to be wrong and fixed:
   - EBITDA was adding depreciation to a *positive* loss (sign error) → now `op. loss + depreciation`.
   - ROCE used the wrong denominator → now `EBIT ÷ (total assets − current liabilities)`.
   - Current ratio read a non-existent balance-sheet key → now uses `Total Current Assets`.
   - DSCR was forced onto negative cash flow → now reported as *not significant*.
2. **Automated tests** — `backend/tests/` pins the parser edge cases and the 19 HY2026 KPI
   golden values; `src/api/client.test.ts` covers the formatters.
3. **CI** — GitHub Actions runs pytest, Vitest and the production build on every push.

---

## Testing & CI

```bash
# Backend
docker compose exec api python -m pytest -q      # or: cd backend && pytest

# Frontend
npm test          # Vitest
npm run build     # type-check + production build
```

`.github/workflows/ci.yml` runs both suites (plus the frontend build) on every push/PR.

---

## Project structure

```
.
├── backend/
│   ├── app/
│   │   ├── api/financial.py        # REST endpoints
│   │   ├── core/{config,database,seed}.py
│   │   ├── models/financial.py     # SQLAlchemy tables
│   │   ├── schemas/financial.py    # Pydantic response models
│   │   └── services/ai_insights.py # LLM board commentary
│   ├── scripts/
│   │   ├── extract_pdf.py          # PDF → structured data + KPIs
│   │   ├── load_data.py            # JSON → Postgres
│   │   ├── dump_periods.py         # Postgres → JSON (regenerate fixtures)
│   │   └── extracted/*.json        # committed extraction output (seed data)
│   ├── tests/                      # pytest (parser + KPI golden values)
│   └── data/                       # source PDFs
├── src/                            # React frontend
│   ├── api/client.ts               # typed API client + formatters
│   └── components/                 # Login, Header, Dashboard, KpiCard, …
├── Dockerfile / nginx.conf         # frontend image (Vite build → nginx)
├── docker-compose.yml              # postgres + api + frontend
└── .github/workflows/ci.yml        # CI
```

---

## Assumptions & data limitations

- **Periods** — HY2026 = six months ended 31 Dec 2025; HY2025 = the comparative six months
  ended 31 Dec 2024 (both extracted from the same interim report); FY2025 = year ended 30 Jun 2025.
- **Source quality** — HY2026 and HY2025 are from the primary interim report and are fully
  mapped. FY2025 is available only as summary data, so it is presented as a full-year *P&L
  reference* only — its balance sheet and cash flow are intentionally left empty rather than
  showing unverifiable figures.
- **Sign convention** — the source presents costs and losses as positive; these are normalised
  to negative in the database.
- **EBITDA** — proxy = operating loss + depreciation (no separate EBITDA line in the source).
- **Contingent consideration** (€850k, Loamin acquisition) is a non-cash liability; liquidity
  ratios are shown both including and excluding it.
- **DSCR** is reported as *n/a* because cash available for debt service is negative.
- **Auth** — the login is a demo gate, not a security boundary.

---

## Environment variables

| Variable       | Required | Description                                   |
|----------------|----------|-----------------------------------------------|
| `DATABASE_URL` | yes      | Postgres DSN (compose sets it automatically)  |
| `GROQ_API_KEY` | yes (AI) | Enables narrative + on-demand insights        |
| `GROQ_MODEL`   | no       | Defaults to `openai/gpt-oss-120b`             |
| `SQL_ECHO`     | no       | Log all SQL statements (default `false`)      |

## Auth, roles and audit

- Login with demo credentials: **board@senus.com / senus123** (admin, can upload/process/review/publish) or **viewer@senus.com / senus123** (read-only).
- All /api endpoints require JWT (Bearer token). Write operations require admin role.
- Every confirm/correct/upload/process/publish is logged in audit_log with user email.
- Header shows the logged-in user and role; the Documents tab shows a badge when items require review (
eview_status = flagged).

## Metrics catalogue

GET /api/metrics/catalog returns metadata for all derived KPIs (label, unit, category, direction, formula, note). The computation lives in backend/scripts/extract_pdf.py (single source of truth).

## AI insights verification

See VERIFICATION_REPORT.md for ground-truth comparison against computed KPIs and the minimal fix to prevent cross-period contamination in the AI prompt.

