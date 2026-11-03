# Senus PLC Board Report: Design Write-up

Senus PLC Board Report turns interim and annual financial reports into a reviewable
management dashboard. The design prioritises traceability and controlled correction:
an extracted number should remain connected to its source document and page, while
calculated metrics should be reproducible without relying on generated text.

## Technology and application structure

The browser application uses React and TypeScript, built with Vite and served by
Nginx. TypeScript gives the frontend explicit API and component contracts; React
supports the interactive company, financial, and document-review screens. Recharts
renders the dashboard visualisations. Nginx serves the single-page application and
forwards `/api/` requests to the backend, keeping the browser on one application
origin.

The API uses Python, FastAPI, Pydantic, and asynchronous SQLAlchemy with `asyncpg`.
Python is also used for PDF parsing, KPI calculations, reconciliation, and validation,
so financial rules are implemented and tested as code rather than delegated to an
LLM. PostgreSQL stores the relational records; Alembic migrations evolve the schema.
Docker packages the frontend and API, while Docker Compose describes the local
frontend, API, and PostgreSQL services. The deployed application is hosted on Google
Cloud Platform and can be accessed at
[http://34.56.190.78:8080/](http://34.56.190.78:8080/). The repository does not
identify the specific GCP compute, networking, or database products behind that
address, so no more specific production topology is claimed here.

## Relational model and provenance

`documents` represents the uploaded source and its lifecycle, detected periods,
extraction/review payloads, and narrative data. A document can contain several
`periods`, each identified by dates and a label. Each period groups three statement
tables—`pnl_items`, `balance_sheet_items`, and `cash_flow_items`—rather than placing
all financial categories in one generic table. This keeps statement-specific data
recognisable while preserving common review fields such as source page, origin,
confidence, review status, and the previous value after correction.

`kpis` stores a metric per period, including its value, formula, classification
(`reported`, `derived`, or `na`), and an explanation when unavailable. This makes
missing data explicit instead of disguising it as zero, and lets the interface show
how derived values were calculated. `validations` records accounting checks and
their expected and observed values. `users` and `audit_log` support role-based access
and a record of important uploads, confirmations, corrections, and publication
actions. The document/period relationship also allows a report to contribute more
than one comparative period.

## Document-ingestion workflow

An administrator uploads a PDF. `pdfplumber` extracts text page by page; the current
workflow does not OCR scanned pages. Deterministic date and column detection looks
for statement headers and dates. The user must confirm detected periods before
processing; inconsistent statement dates, durations, or comparative columns block
extraction rather than being guessed by a model.

For each confirmed period, two extractors work independently: a regular-expression
parser tuned to the report's statement layouts, and—when `GROQ_API_KEY` is
configured—a structured LLM extractor. LLM entries include a line item, amount,
page, and source text. The system checks figures against the cited page text. A
reconciliation step compares the outputs: the deterministic value is selected by
default, disagreements remain visible for human review, and the alternative value
is retained instead of being silently substituted. Page provenance, extractor
agreement, and available accounting identities contribute to the confidence score.

An administrator can inspect, confirm, or correct extracted items before publishing.
The document lifecycle tracks upload, processing, extraction, review, publication,
and errors. Replacing an existing period requires explicit confirmation. After
correction, the backend recalculates KPIs and accounting validations with deterministic
Python formulas. This separates observed source figures from derived measures and
keeps reported inconsistencies visible—for example, an incorrect printed subtotal
can be preserved and flagged instead of automatically "fixed."

## Security posture

Database access goes through SQLAlchemy's ORM with bound parameters, so there is no
string-built SQL. PDF uploads are admin-only, limited to 20 MB, validated by
extension and `%PDF-` signature, stored under a sanitised, UUID-prefixed name, and
deleted if they cannot be parsed. Nginx adds a Content-Security-Policy and related
headers, CORS is restricted to local origins, and repeated failed logins are
throttled in memory.

As a deliberate demo decision, the login page pre-fills and displays the seeded
accounts (`board@senus.com` as admin, `viewer@senus.com` as read-only, password
`senus123`) so reviewers can sign in immediately. A production deployment would
remove them and set a strong `SECRET_KEY`.

Known security gaps, accepted for this demo: traffic is plain HTTP (no TLS), so
credentials and JWTs are unencrypted; the JWT lives in `localStorage` and tokens
cannot be revoked; the default `SECRET_KEY` and database password are unchanged;
uploads are not antivirus-scanned and are parsed
inside the API process; with a Groq key, document text is sent to a third party and
could carry prompt injection (mitigated by grounding, deterministic precedence, and
human review, not eliminated); the login throttle is per process and in memory; the
CSP allows inline styles; and authorisation is limited to two roles with no
per-document access control.

## Current gaps and next improvements

The parser and its tests target the report layouts and labels currently encountered;
they are not a general financial-document parser. Scanned PDFs, unfamiliar table
layouts, and complex multi-column reports need OCR or additional parsing and
validation before they can be handled reliably. Extraction confidence is a review
prioritisation aid, not a guarantee of correctness, and human review remains
important. Current source material also lacks monthly series, revenue by channel,
and bookings, so the system correctly reports those measures as unavailable rather
than estimating them.

The next improvements should therefore focus on broader document-layout coverage,
more explicit review of low-confidence or unmatched figures, and expanded source
data for currently unavailable KPIs. Deployment documentation should also record
the exact GCP resources, database and storage configuration, backup policy, and
operational monitoring; those details cannot be inferred from the public URL or
local Compose configuration.
