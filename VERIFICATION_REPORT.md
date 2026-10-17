# Verify AI insights against actual DB data for HY2026 and HY2025

## 1) Data snapshot (ground truth from DB/extracted; matches compute_kpis)

**HY2025 (2024-07-01..2024-12-31, 6m):**
- Revenue €340,931, GP €272,331 (GM 79.9%), Admin €677,908, OpLoss €-405,577, NetLoss €-406,613
- Cash €72,382, Equity €173,316, NCA €193,421
- CR 3.15x (ex-cont 3.15x), Runway 0.96m, EqR 49.7%, IntR 0.0%, EBITDAm -116.0%, ROE -234.6%, ROCE -156.7%, DSCR None

**HY2026 (2025-07-01..2025-12-31, 6m):**
- Revenue €354,813, GP €289,952 (GM 81.7%), Admin €781,975, OpLoss €-483,753, NetLoss €-485,144
- Cash €735,189, Equity €561,081, NCA €-313,766, Cont €-850,000
- CR 0.75x (ex-cont 2.39x), Runway 10.53m, EqR 29.9%, IntR 48.5%, EBITDAm -133.5%, ROE -86.5%, ROCE -75.9%, DSCR None

## 2) Current AI insights (from API GET /api/insights/{label})

**HY2025:**
- Revenue Quality: Revenue of €340,931 generated a gross margin of 79.9%, showing strong pricing power, yet the company remains loss‑making, indicating the need to translate margin advantage into operating profit
- Cost Discipline: Admin expenses of €677,908 exceed gross profit of €272,331, driving an operating loss of €-405,577; a priority is to curtail administrative overhead to narrow the loss gap
- Cash Runway: Cash of €72,382 provides only a 1.0 month runway, creating immediate liquidity risk that requires either rapid cost reduction or new financing
- Strategic Milestones & Risks: The recent Loamin acquisition and €1.1m equity raise have not extended the cash runway; integration costs could further strain cash, so board should enforce tight integration budgeting and monitor cash burn

**HY2026:**
- Revenue Quality: Revenue of €354,813 generated a gross margin of 81.7%, confirming a high‑margin product but the absolute top‑line is modest; accelerating sales is essential to achieve the stated 50% CAGR to 2030
- Cost Discipline: Admin expenses of €781,975 exceed gross profit of €289,952, creating an operating loss of €-483,753; a focused reduction of administrative overhead is required to move toward breakeven
- Cash Runway: Cash of €735,189 provides a runway of 10.5 months, but negative operating cash flow (DSCR not significant) means the runway will erode unless cash burn is curbed or additional funding is secured
- Liquidity Risk: The current ratio of 0.75x (ex‑contingent 2.39x) falls below 1x when the contingent consideration of €850,000 is included, highlighting a liquidity shortfall that must be addressed through either liability mitigation or current asset improvement

## 3) Verdict - discrepancies found

**Discrepancies found: YES**

### Critical
- **Period-mixing in prompt CONTEXT (backend/app/services/ai_insights.py:82-92)**: The prompt hardcodes CONTEXT stating facts from the "HY2026 report" (Loamin acquisition in H1, €1.1m equity raised, Dec 2025 listing on Euronext Access, etc.) regardless of period_label. For HY2025, this produces false/unsupported statements such as "The recent Loamin acquisition and €1.1m equity raise have not extended the cash runway..." - these events are not verifiably associated with HY2025 per the data; narrative_insights for HY2025 is empty and the context is period-cross-contaminated.

### Moderate
- **Unsubstantiated/subjective inflection**: Some recommendations imply targets/causes not present in period facts (e.g. referencing "stated 50% CAGR to 2030" from hardcoded context). While not directly contradicting computed KPIs, this violates the rule of citing only numbers/facts present in the period being analyzed.

### Minor
- **Causality language**: Phrasing like "integration costs could further strain cash" is speculative; prompt should discourage inventing causes unless explicitly stated.

## 4) Proposed minimal code changes to backend/app/services/ai_insights.py

Replace hardcoded period-specific CONTEXT with period-aware minimal context and strengthen guardrails.

`diff
diff --git a/backend/app/services/ai_insights.py b/backend/app/services/ai_insights.py
--- a/backend/app/services/ai_insights.py
+++ b/backend/app/services/ai_insights.py
@@ -79,17 +79,25 @@ async def generate_insights(period_label: str, db: AsyncSession) -> List[AIInsight]
     - ROCE: {kpis.get("roce", 0):.1f}%
     - DSCR: {("%%.2fx" %% kpis["dscr"]) if "dscr" in kpis else "not significant (negative operating cash flow)"}
 
-    CONTEXT (facts stated in the HY2026 report): Senus is an early-stage Natural Capital SaaS company, listed Dec 2025 on Euronext Access. Stated strategy: 50% CAGR to 2030. Acquired Loamin (geospatial AI) in H1. €1.1m equity raised. Pre-revenue scaling phase.
+    # Period-aware context: avoid cross-period contamination.
+    CONTEXT: You are analyzing financials for period {period_label} (6 months).
+             Do not reference events from other periods unless they are explicitly
+             documented for {period_label}. Do not state long-term targets
+             (e.g. CAGR) unless explicitly stated in the data for {period_label}.
 
     STRICT RULES:
     - Use ONLY the figures and facts in FINANCIAL DATA and CONTEXT above. Never invent events, causes, costs, contracts or forecasts.
     - Every numeric claim must match a figure above; do not restate a figure as text without the number.
     - Do not claim new contracts, partnerships or funding unless explicitly named above.
     - If something material is missing (e.g. no prior-year comparator), say so instead of estimating.
+    - This is a 6-month period ({period_label}); do not conflate with full-year or other periods.
+    - Do not contradict computed KPIs (e.g. if DSCR is marked "not significant", never state a DSCR value or imply positive debt service coverage).
+    - Prefer descriptive statements over speculative recommendations; do not invent causes/costs.
 
     Return JSON array of insights with: section, insight, confidence (0-1). Focus on: revenue quality, cost discipline, cash runway, strategic milestones, risks.
 
     Only use the percentages and ratios listed above. Do not introduce any other calculated ratio or percentage, and do not invent figures."""
`

## 5) GROQ_API_KEY / environment note
- GROQ_API_KEY is set (backend/.env). ai_insights.py only initializes client if key present; otherwise returns configuration error. We called existing API (read-only) as requested.

## 6) Final answer
**Discrepancies found: YES (critical - period mixing in prompt CONTEXT causing HY2025 to reference HY2026 events).** Minimal fix: make CONTEXT period-aware and add guardrails (no cross-period references, don’t contradict computed KPIs, be explicit 6-month). See diff above. No UI changes.