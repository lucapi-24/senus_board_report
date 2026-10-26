import asyncio
import json
import logging
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload
from fastapi import HTTPException
from app.models.financial import (
    BalanceSheetItem,
    CashFlowItem,
    Document,
    Insight,
    Period,
    PnLItem,
)
from app.schemas.financial import (
    AIInsight,
    InsightEvidence,
    InsightSource,
    PeriodCompareRequest,
    PeriodCompareResponse,
)
from app.core.config import settings
from app.services.metrics import CATALOG_BY_KEY, kpi_metadata, require_same_period_duration
from app.services.period_detector import period_duration_months
from openai import AsyncOpenAI


logger = logging.getLogger(__name__)

INSIGHT_MAX_TOKENS = 800
INSIGHT_TOKEN_BUDGET = 8000
INSIGHT_REASONING_EFFORT = "low"
MAX_PDF_EXCERPT_LINES = 18

client = AsyncOpenAI(
    base_url=settings.GROQ_BASE_URL,
    api_key=settings.GROQ_API_KEY,
) if settings.GROQ_API_KEY else None

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])")
_CAUSAL_RE = re.compile(
    r"\b(?:because|due to|cause[sd]? by|caus(?:e|es|ed|ing)|"
    r"drive|drives|drove|driven|driving|as a result of|as a consequence of|"
    r"on the back of|reflect(?:s|ed|ing)?|"
    r"attributable to|owing to|result(?:s|ed|ing) from|"
    r"lead(?:s|ing)? to|led to|thanks to|in response to|following|"
    r"explain(?:s|ed|ing)?|consequently|therefore)\b",
    re.IGNORECASE,
)
_KPI_DEPENDENCIES = {
    "revenue": ("pnl:Revenue", "pnl:Turnover"),
    "cash": ("balance_sheet:Cash and cash equivalents", "balance_sheet:Cash and bank balances"),
    "ebitda": ("pnl:Group operating loss", "pnl:Operating loss", "cash_flow:Depreciation"),
    "gross_margin": ("pnl:Gross Profit", "pnl:Revenue", "pnl:Turnover"),
    "operating_margin": ("pnl:Group operating loss", "pnl:Operating loss", "pnl:Revenue", "pnl:Turnover"),
    "ebitda_margin": ("pnl:Group operating loss", "pnl:Operating loss", "cash_flow:Depreciation", "pnl:Revenue", "pnl:Turnover"),
    "net_margin": ("pnl:Loss for the period", "pnl:Revenue", "pnl:Turnover"),
    "admin_expense_ratio": ("pnl:Administrative expenses", "pnl:Revenue", "pnl:Turnover"),
    "fcf": ("cash_flow:Net cash from operations", "cash_flow:Capex"),
    "monthly_burn": ("kpi:fcf",),
    "runway_months": ("kpi:cash", "kpi:monthly_burn"),
    "net_cash": ("kpi:cash", "balance_sheet:Creditors > 1yr"),
    "current_ratio": ("balance_sheet:Total current assets", "balance_sheet:Creditors < 1yr", "balance_sheet:Contingent consideration"),
    "current_ratio_ex_contingent": ("balance_sheet:Total current assets", "balance_sheet:Creditors < 1yr"),
    "total_assets": ("balance_sheet:Total fixed assets", "balance_sheet:Total current assets"),
    "equity_ratio": ("balance_sheet:Equity", "balance_sheet:Net Assets", "kpi:total_assets"),
    "intangibles_ratio": ("balance_sheet:Goodwill", "balance_sheet:Development Costs", "kpi:total_assets"),
    "debt_to_equity": ("balance_sheet:Creditors > 1yr", "balance_sheet:Equity", "balance_sheet:Net Assets"),
    "interest_cover": ("pnl:Group operating loss", "pnl:Operating loss", "pnl:Interest payable"),
    "dscr": ("cash_flow:Net cash from operations", "cash_flow:Interest paid", "cash_flow:Loan repayments"),
    "roce": ("pnl:Group operating loss", "pnl:Operating loss", "balance_sheet:Total assets", "balance_sheet:Creditors < 1yr"),
    "roce_annualised": ("kpi:roce",),
    "roce_ex_contingent": ("pnl:Group operating loss", "pnl:Operating loss", "balance_sheet:Total assets", "balance_sheet:Creditors < 1yr"),
    "roe": ("pnl:Loss for the period", "balance_sheet:Equity", "balance_sheet:Net Assets"),
}


def _format_money(value: Optional[float]) -> str:
    return f"€{value:,.0f}" if value is not None else "n/d"


def _format_kpi(value: Optional[float], digits: int, suffix: str) -> str:
    return f"{value:.{digits}f}{suffix}" if value is not None else "n/d"


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _number_tolerance(value: Any) -> float:
    decimals = 0
    if isinstance(value, str) and "." in value:
        decimals = len(value.rsplit(".", 1)[1])
    elif isinstance(value, float):
        rendered = str(value)
        if "." in rendered:
            decimals = len(rendered.rsplit(".", 1)[1])
    return 0.5 * (10 ** -decimals)


def _parse_number(value: str, preceding: str = "") -> Optional[float]:
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return None
    if re.search(r"\(\s*[€$£]?$", preceding):
        number = -abs(number)
    return number


def _numeric_mentions(*texts: str) -> List[Tuple[float, int]]:
    mentions: List[Tuple[float, int]] = []
    for text in texts:
        for match in _NUMBER_RE.finditer(text or ""):
            value = _parse_number(match.group(), text[:match.start()])
            if value is not None:
                mentions.append((value, len(match.group().partition(".")[2])))
    return mentions


def _source_index(period: Period) -> Dict[str, Dict[str, Any]]:
    from app.services.deterministic import compute_kpis

    metadata = period.doc_meta or {}
    provenance = period.doc_provenance or {}
    source_note = metadata.get("source_description")
    assignment_source_notes = (
        {
            "pnl:Revenue": "Assignment brief",
            "kpi:revenue": "Assignment brief",
            "kpi:customer_accounts": "Assignment brief",
            "kpi:cash": "HY2026 cash flow opening balance",
        }
        if period.label == "FY2025" and period.source_doc_id is None
        else {}
    )
    statement_items = {
        "pnl": period.pnl_items,
        "balance_sheet": period.bs_items,
        "cash_flow": period.cf_items,
    }
    sources: Dict[str, Dict[str, Any]] = {}
    values: Dict[str, Dict[str, float]] = {}
    for statement, items in statement_items.items():
        values[statement] = {}
        for item in items:
            key = f"{statement}:{item.line_item}"
            page = item.page
            if page is None:
                page = provenance.get(statement, {}).get(item.line_item)
            item_source_note = assignment_source_notes.get(
                key,
                source_note if period.source_doc_id is None else None,
            )
            sources[key] = {
                "source_id": key,
                "label": item.line_item,
                "value": float(item.value_eur),
                "origin": item.origin or "reported",
                "formula": None,
                "pages": [int(page)] if page is not None else [],
                "source_note": item_source_note,
            }
            values[statement][item.line_item] = float(item.value_eur)

    duration = period_duration_months(period.start_date, period.end_date)
    computed = compute_kpis(
        values["pnl"],
        values["balance_sheet"],
        values["cash_flow"],
        duration,
    )
    stored_kpis = {item.metric: item for item in period.kpis}
    for metric, spec in CATALOG_BY_KEY.items():
        key = f"kpi:{metric}"
        value = computed.get(metric)
        stored = stored_kpis.get(metric)
        if (
            value is None
            and metric in {"cash", "customer_accounts"}
            and stored
            and stored.value is not None
        ):
            value = float(stored.value)
        if value is None:
            continue
        metadata_for_metric = kpi_metadata(
            metric,
            value,
            values["cash_flow"],
            stored.note if stored else None,
        )
        dependency_ids = _KPI_DEPENDENCIES.get(metric, ())
        dependency_sources = [
            sources[source_id]
            for source_id in dependency_ids
            if source_id in sources
        ]
        pages = sorted({page for source in dependency_sources for page in source["pages"]})
        note = (
            assignment_source_notes.get(key)
            or (
                stored.note
                if stored and stored.note
                else source_note
                if period.source_doc_id is None and metadata_for_metric["kind"] == "reported"
                else None
            )
        )
        sources[key] = {
            "source_id": key,
            "label": str(spec["label"]),
            "value": float(value),
            "origin": str(metadata_for_metric["kind"]),
            "formula": metadata_for_metric["formula"],
            "pages": pages,
            "source_note": note,
        }

    duration_key = "period:duration_months"
    sources[duration_key] = {
        "source_id": duration_key,
        "label": "Period duration (months)",
        "value": float(duration),
        "origin": "derived",
        "formula": "Inclusive months between period start and end dates",
        "pages": [],
        "source_note": None,
    }
    return sources


def _insight_prompt_facts(
    source_index: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep model context to available period KPIs, not statement line items."""
    return [
        {
            "source_id": source["source_id"],
            "label": source["label"],
            "value": source["value"],
            "origin": source["origin"],
            "formula": source["formula"],
            "pages": source["pages"],
            "source_note": source["source_note"],
        }
        for key, source in source_index.items()
        if key.startswith("kpi:")
    ]


def _insight_pdf_excerpts(
    source_index: Dict[str, Dict[str, Any]],
    pdf_pages: Dict[int, str],
    max_lines: int = MAX_PDF_EXCERPT_LINES,
) -> Dict[int, str]:
    """Return short source lines for KPI inputs instead of whole PDF pages."""
    dependencies = {
        dependency
        for metric, dependency_ids in _KPI_DEPENDENCIES.items()
        if f"kpi:{metric}" in source_index
        for dependency in dependency_ids
        if dependency in source_index
    }
    lines_by_page: Dict[int, List[str]] = {}
    total_lines = 0
    for source_id in sorted(dependencies):
        source = source_index[source_id]
        label = _normalise_text(source["label"])
        numeric_value = float(source["value"])
        candidates = {
            f"{abs(int(round(numeric_value))):,}",
            str(abs(int(round(numeric_value)))),
        }
        for page in source["pages"]:
            page_text = pdf_pages.get(page)
            if not page_text:
                continue
            page_lines = page_text.splitlines()
            for index, line in enumerate(page_lines):
                normalized_line = _normalise_text(line)
                if label not in normalized_line and not any(
                    re.search(rf"(?<!\d){re.escape(candidate)}(?!\d)", line)
                    for candidate in candidates
                ):
                    continue
                excerpts = lines_by_page.setdefault(page, [])
                if line not in excerpts and total_lines < max_lines:
                    excerpts.append(line)
                    total_lines += 1
                if (
                    not any(char.isdigit() for char in line)
                    and index + 1 < len(page_lines)
                    and any(char.isdigit() for char in page_lines[index + 1])
                ):
                    continuation = page_lines[index + 1]
                    if continuation not in excerpts                     and total_lines < max_lines:
                        excerpts.append(continuation)
                        total_lines += 1
    return {
        page: "\n".join(lines)
        for page, lines in lines_by_page.items()
        if lines
    }


def _estimate_prompt_tokens(prompt: str) -> int:
    """Tokenizer-free estimate with headroom for JSON and financial figures."""
    return math.ceil(len(prompt.encode("utf-8")) / 3.5)


def _build_insight_prompt(
    period_label: str,
    period_months: int,
    facts: List[Dict[str, Any]],
    excerpts: Dict[int, str],
) -> str:
    serialized_facts = json.dumps(
        facts,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    serialized_excerpts = json.dumps(
        [{"page": page, "text": text} for page, text in excerpts.items()],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""Analyze the published or confirmed financial period {period_label} ({period_months} months).

Available KPIs for this period (these are the only numeric sources you may cite):
{serialized_facts}

Minimal source lines from the same period (page numbers are citations; do not infer other facts):
{serialized_excerpts}

Return a JSON object with an "insights" array of 3-4 concise items. Each item must have:
- "section": short topic
- "data": factual statement using only available financial facts
- "interpretation": a cautious interpretation, or an empty string
- "claims": one object per numeric claim in the text, each {{"claim_text": exact numeric text copied from data or interpretation, "source_id": exact available-fact source_id, "value": numeric value}}
- "evidence": optional exact PDF quotations as [{{"page": page_number, "quote": exact quote}}]

Rules:
- Every numeric value in data or interpretation must match a cited claim's source and value. Do not write numbers as words.
- Cite only source IDs listed in Available KPIs. Never use unavailable facts or infer missing values.
- Do not say why a metric changed, or attribute a cause, unless the entire causal sentence is quoted verbatim from a supplied excerpt and cited in evidence.
- Distinguish reported facts from interpretation. Do not claim a contract, customer, cause, forecast, target, or event unless explicitly supported by these facts or quoted evidence.
- Do not use confidence scores or add claims about other periods.
- Keep the complete response concise enough to fit within the response token limit."""


def _response_status(exc: Exception) -> Optional[int]:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or getattr(exc, "headers", None) or {}
    for name in (
        "retry-after",
        "x-ratelimit-reset-tokens",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset",
    ):
        raw_value = headers.get(name)
        if raw_value is None:
            continue
        value = str(raw_value).strip().lower()
        try:
            return max(float(value.rstrip("s")), 0.0)
        except ValueError:
            milliseconds = re.fullmatch(r"(\d+(?:\.\d+)?)ms", value)
            if milliseconds:
                return max(float(milliseconds.group(1)) / 1000, 0.0)
            try:
                retry_at = parsedate_to_datetime(str(raw_value))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


async def _request_insights(prompt: str, period_label: str) -> Any:
    prompt_tokens = _estimate_prompt_tokens(prompt)
    logger.info(
        "Groq insight request period=%s approximate_prompt_tokens=%d prompt_chars=%d max_tokens=%d reasoning_effort=%s",
        period_label,
        prompt_tokens,
        len(prompt),
        INSIGHT_MAX_TOKENS,
        INSIGHT_REASONING_EFFORT,
    )
    if prompt_tokens + INSIGHT_MAX_TOKENS > INSIGHT_TOKEN_BUDGET:
        raise ValueError(
            f"Insight prompt estimate ({prompt_tokens} tokens) exceeds the "
            f"{INSIGHT_TOKEN_BUDGET - INSIGHT_MAX_TOKENS} token input budget."
        )

    last_error: Optional[Exception] = None
    for attempt in range(2):
        try:
            return await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=INSIGHT_MAX_TOKENS,
                response_format={"type": "json_object"},
                extra_body={"reasoning_effort": INSIGHT_REASONING_EFFORT},
            )
        except Exception as exc:
            last_error = exc
            status = _response_status(exc)
            retry_after = _retry_after_seconds(exc)
            if attempt == 0 and status in (413, 429) and retry_after is not None:
                logger.warning(
                    "Groq insight request received HTTP %d for period %s; retrying once after %.2f seconds",
                    status,
                    period_label,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError("Groq insight request did not return a response.")


def _provider_error_insight(
    exc: Exception, period_label: str, existing: List[AIInsight]
) -> List[AIInsight]:
    status = _response_status(exc)
    if status == 413:
        section = "Groq request limit"
        message = (
            "Groq rejected the insight request with HTTP 413 (token or request-size "
            "limit). Existing insights were kept. Check again or retry manually later."
        )
    elif status == 429:
        section = "Groq rate limit"
        wait = _retry_after_seconds(exc)
        wait_text = f" Wait at least {wait:g} seconds before retrying." if wait is not None else ""
        message = (
            f"Groq rate limit (HTTP 429) persisted after one automatic retry.{wait_text} "
            "Existing insights were kept; use Retry / regenerate to try again manually."
        )
    else:
        section = "AI Error"
        message = (
            f"Could not generate insights for {period_label}. Existing insights were kept; "
            "use Retry / regenerate to try again manually."
        )
    return [
        *existing,
        AIInsight(section=section, insight=message),
    ]


def _validate_candidate(
    candidate: Dict[str, Any],
    source_index: Dict[str, Dict[str, Any]],
    pdf_pages: Dict[Any, str],
) -> Tuple[Optional[AIInsight], str]:
    section = candidate.get("section")
    data = candidate.get("data")
    interpretation = candidate.get("interpretation", "")
    claims = candidate.get("claims")
    evidence = candidate.get("evidence", [])
    if not all(isinstance(value, str) for value in (section, data, interpretation)):
        return None, "missing required insight text fields"
    if not isinstance(claims, list) or not claims:
        return None, "insight has no structured numeric claims"
    if not isinstance(evidence, list):
        return None, "evidence must be a list"

    validated_sources: List[InsightSource] = []
    claim_numbers: List[Tuple[float, int]] = []
    combined_text = f"{data.strip()}\n{interpretation.strip()}"
    for claim in claims:
        if not isinstance(claim, dict):
            return None, "claim is not an object"
        source_id = claim.get("source_id")
        claim_value = claim.get("value")
        claim_text = claim.get("claim_text")
        if not isinstance(source_id, str) or source_id not in source_index:
            return None, "claim references an unknown source"
        if not isinstance(claim_text, str) or not claim_text.strip():
            return None, "claim has no linked text"
        text_numbers = _numeric_mentions(claim_text)
        if len(text_numbers) != 1 or _normalise_text(claim_text) not in _normalise_text(combined_text):
            return None, "claim text does not identify one number in the insight"
        if isinstance(claim_value, bool) or not isinstance(claim_value, (int, float, str)):
            return None, "claim value is not numeric"
        try:
            numeric_value = float(claim_value)
        except ValueError:
            return None, "claim value is not numeric"
        if not math.isfinite(numeric_value):
            return None, "claim value is not finite"
        source = source_index[source_id]
        if abs(numeric_value - source["value"]) > _number_tolerance(claim_value):
            return None, "claim value does not match its source"
        claim_number, claim_precision = text_numbers[0]
        if abs(claim_number - source["value"]) > 0.5 * (10 ** -claim_precision):
            return None, "linked number does not match the cited source"
        claim_numbers.append((claim_number, claim_precision))
        validated_sources.append(
            InsightSource(
                **source,
                claim_text=claim_text,
                verified=True,
            )
        )

    validated_evidence: List[InsightEvidence] = []
    for item in evidence:
        if not isinstance(item, dict):
            return None, "evidence item is not an object"
        page = item.get("page")
        quote = item.get("quote")
        evidence_period = item.get("period_label")
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or not isinstance(quote, str)
            or (evidence_period is not None and not isinstance(evidence_period, str))
        ):
            return None, "evidence item has invalid page or quote"
        page_key = f"{evidence_period}:{page}" if evidence_period else page
        page_text = pdf_pages.get(page_key)
        if not page_text or _normalise_text(quote) not in _normalise_text(page_text):
            return None, "evidence quote does not match the cited PDF page"
        validated_evidence.append(
            InsightEvidence(page=page, quote=quote, period_label=evidence_period)
        )

    data_text = data.strip()
    interpretation_text = interpretation.strip()
    mentions = _numeric_mentions(data_text, interpretation_text)
    if not mentions:
        return None, "insight contains no verifiable numeric claims"
    for mention, precision in mentions:
        tolerance = 0.5 * (10 ** -precision)
        matching = any(
            abs(mention - claim_number) <= max(tolerance, 0.5 * (10 ** -claim_precision))
            for claim_number, claim_precision in claim_numbers
        )
        if not matching:
            return None, "insight contains a number with no linked structured claim"

    for text in (data_text, interpretation_text):
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if not _CAUSAL_RE.search(sentence):
                continue
            sentence_normalised = _normalise_text(sentence)
            if not any(
                sentence_normalised in _normalise_text(pdf_pages[item.page])
                for item in validated_evidence
            ):
                return None, "causal statement is not quoted verbatim from supplied PDF evidence"

    confidence = len(validated_sources) / len(claims)
    combined = "\n".join(
        part for part in (f"Data: {data_text}", f"Interpretation: {interpretation_text}" if interpretation_text else "")
    )
    return AIInsight(
        section=section.strip(),
        insight=combined,
        data=data_text,
        interpretation=interpretation_text or None,
        confidence=confidence,
        verified_claims=len(validated_sources),
        total_claims=len(claims),
        sources=validated_sources,
        evidence=validated_evidence,
    ), ""


def _stored_insight(row: Insight) -> Optional[AIInsight]:
    try:
        content = json.loads(row.text)
        sources = json.loads(row.sources_json or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(content, dict) or not isinstance(sources, dict) or sources.get("version") != 1:
        return None
    claims = sources.get("claims", [])
    evidence = sources.get("evidence", [])
    if not claims:
        return None
    try:
        return AIInsight(
            section=row.section,
            insight=f"Data: {content['data']}\nInterpretation: {content.get('interpretation') or ''}".strip(),
            data=content["data"],
            interpretation=content.get("interpretation"),
            confidence=float(row.confidence or 0),
            verified_claims=len(claims),
            total_claims=len(claims),
            sources=claims,
            evidence=evidence,
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _load_persisted_insights(db: AsyncSession, period_id: int) -> List[AIInsight]:
    result = await db.execute(
        select(Insight).where(Insight.period_id == period_id).order_by(Insight.created_at)
    )
    return [
        parsed
        for row in result.scalars().all()
        if (parsed := _stored_insight(row)) is not None
    ]


def _period_is_eligible(period: Period, document: Optional[Document]) -> bool:
    if document and document.status == "published":
        return True
    items = period.pnl_items + period.bs_items + period.cf_items
    if items and all(item.review_status in {"confirmed", "corrected"} for item in items):
        return True
    metadata = period.doc_meta or {}
    return (
        period.label == "FY2025"
        and period.source_doc_id is None
        and metadata.get("document_title")
        == "FY2025 partial data (assignment brief and HY2026 cash flow)"
        and bool(metadata.get("source_description"))
    )


async def generate_insights(
    period_label: str, db: AsyncSession, force: bool = False
) -> List[AIInsight]:
    """Generate only deterministic-verified, source-linked insights."""
    result = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
        )
        .where(Period.label == period_label)
    )
    period = result.scalar_one_or_none()
    if not period:
        return [AIInsight(section="Error", insight=f"Period {period_label} not found")]

    document = await db.get(Document, period.source_doc_id) if period.source_doc_id else None
    if not _period_is_eligible(period, document):
        return [AIInsight(
            section="Unavailable",
            insight="Insights are available only for published periods or periods with all figures confirmed or corrected.",
        )]

    existing = await _load_persisted_insights(db, period.id)
    if existing and not force:
        return existing
    if not client:
        return existing or [AIInsight(
            section="Configuration",
            insight="Groq API key not configured. Set GROQ_API_KEY environment variable.",
        )]

    source_index = _source_index(period)
    pdf_pages: Dict[int, str] = {}
    if document and document.stored_path:
        path = Path(document.stored_path)
        if path.is_file():
            from app.services.ingest import read_pages

            pdf_pages = {
                page: text[:4000]
                for page, text in await asyncio.to_thread(read_pages, str(path))
                if text.strip()
            }
        else:
            logger.warning("Insight source PDF is missing for document id %s", document.id)

    facts = _insight_prompt_facts(source_index)
    supplied_pages = _insight_pdf_excerpts(source_index, pdf_pages)
    period_months = period_duration_months(period.start_date, period.end_date)
    prompt = _build_insight_prompt(
        period.label, period_months, facts, supplied_pages
    )

    try:
        response = await _request_insights(prompt, period.label)
        payload = json.loads(response.choices[0].message.content or "{}")
    except Exception as exc:
        logger.exception("Insight request failed for period %s", period.label)
        return _provider_error_insight(exc, period.label, existing)

    if not isinstance(payload, dict):
        logger.warning("AI response was not a JSON object for period %s", period.label)
        return [*existing, AIInsight(section="AI Error", insight="Groq returned an invalid insights response. Existing insights were kept; retry manually.")]

    candidates = payload.get("insights") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        logger.warning("AI response has no insights list for period %s", period.label)
        return [*existing, AIInsight(section="AI Error", insight="Groq response did not contain an insights list. Existing insights were kept; retry manually.")]

    accepted: List[AIInsight] = []
    persisted: List[Insight] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            logger.warning("Discarding malformed insight for period %s", period.label)
            continue
        insight, reason = _validate_candidate(candidate, source_index, supplied_pages)
        if insight is None:
            logger.warning(
                "Discarding unverifiable insight for period %s (%s)",
                period.label,
                reason,
            )
            continue
        accepted.append(insight)
        persisted.append(
            Insight(
                period_id=period.id,
                section=insight.section,
                text=json.dumps(
                    {"data": insight.data, "interpretation": insight.interpretation},
                    ensure_ascii=False,
                ),
                confidence=insight.confidence,
                sources_json=json.dumps(
                    {
                        "version": 1,
                        "claims": [source.model_dump() for source in insight.sources],
                        "evidence": [item.model_dump() for item in insight.evidence],
                    },
                    ensure_ascii=False,
                ),
            )
        )

    if accepted:
        await db.execute(
            delete(Insight).where(
                Insight.period_id == period.id,
                ~Insight.section.like("Period comparison:%"),
            )
        )
        db.add_all(persisted)
        await db.commit()
        return accepted
    logger.warning(
        "No verified insights generated for period %s; persisted insights were retained",
        period.label,
    )
    return [
        *existing,
        AIInsight(
            section="No verified insights",
            insight=(
                "The generated insights did not pass deterministic source and number "
                "checks. Existing insights were kept; retry manually."
            ),
        ),
    ]


def _comparison_source_index(periods: List[Period]) -> Dict[str, Dict[str, Any]]:
    sources: Dict[str, Dict[str, Any]] = {}
    for period in periods:
        for source_id, source in _source_index(period).items():
            comparison_id = f"{period.label}:{source_id}"
            sources[comparison_id] = {
                **source,
                "source_id": comparison_id,
                "period_label": period.label,
            }
    return sources


async def _comparison_pdf_pages(
    period: Period, document: Optional[Document]
) -> Dict[str, str]:
    if not document or not document.stored_path:
        return {}
    path = Path(document.stored_path)
    if not path.is_file():
        logger.warning(
            "Comparison source PDF is missing for document id %s", document.id
        )
        return {}

    from app.services.ingest import read_pages

    return {
        f"{period.label}:{page}": text[:4000]
        for page, text in await asyncio.to_thread(read_pages, str(path))
        if text.strip()
    }


async def compare_periods(request: PeriodCompareRequest, db: AsyncSession) -> PeriodCompareResponse:
    """Generate and persist a source-verified comparison of two periods."""
    result_a = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
        )
        .where(Period.label == request.period_label_a)
    )
    period_a = result_a.scalar_one_or_none()
    if not period_a:
        raise HTTPException(status_code=404, detail=f"Period '{request.period_label_a}' not found.")

    result_b = await db.execute(
        select(Period)
        .options(
            selectinload(Period.pnl_items),
            selectinload(Period.bs_items),
            selectinload(Period.cf_items),
            selectinload(Period.kpis),
        )
        .where(Period.label == request.period_label_b)
    )
    period_b = result_b.scalar_one_or_none()
    if not period_b:
        raise HTTPException(status_code=404, detail=f"Period '{request.period_label_b}' not found.")

    try:
        require_same_period_duration(
            period_a.start_date,
            period_a.end_date,
            period_b.start_date,
            period_b.end_date,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    if period_a.id == period_b.id:
        raise HTTPException(status_code=422, detail="Select two different periods to compare.")

    document_a = await db.get(Document, period_a.source_doc_id) if period_a.source_doc_id else None
    document_b = await db.get(Document, period_b.source_doc_id) if period_b.source_doc_id else None
    if not _period_is_eligible(period_a, document_a) or not _period_is_eligible(period_b, document_b):
        raise HTTPException(
            status_code=409,
            detail="Comparisons are available only for published periods or periods with all figures confirmed or corrected.",
        )

    if not client:
        raise HTTPException(
            status_code=503,
            detail="Groq API key is not configured; a verified AI comparison cannot be generated.",
        )

    source_index = _comparison_source_index([period_a, period_b])
    comparison_pdf_pages = {
        **await _comparison_pdf_pages(period_a, document_a),
        **await _comparison_pdf_pages(period_b, document_b),
    }
    source_facts = [
        source
        for source_id, source in source_index.items()
        if ":kpi:" in source_id
    ]
    excerpts = [
        {
            "period_label": period.label,
            "page": page,
            "text": text,
        }
        for period in (period_a, period_b)
        for page, text in _insight_pdf_excerpts(
            _source_index(period),
            {
                int(key.rsplit(":", 1)[1]): text
                for key, text in comparison_pdf_pages.items()
                if key.startswith(f"{period.label}:")
            },
            max_lines=MAX_PDF_EXCERPT_LINES // 2,
        ).items()
    ]
    supplied_pages = comparison_pdf_pages
    prompt = f"""Compare these two published or confirmed financial periods for Senus PLC.

Available KPIs (the only numeric sources you may cite):
{json.dumps(source_facts, ensure_ascii=False, separators=(",", ":"))}

Minimal source lines (each page is identified by period_label and page):
{json.dumps(excerpts, ensure_ascii=False, separators=(",", ":"))}

Return one JSON object named "comparison" with:
- "section": short topic
- "data": factual comparison using only the available source facts
- "interpretation": cautious interpretation, or an empty string
- "claims": one object for each numeric claim, each with "claim_text" (the exact numeric text in data or interpretation), "source_id" (an exact source_id above), and "value" (that source's numeric value)
- "evidence": optional exact PDF quotations, each with "period_label", "page", and "quote"

Rules:
- Every number in data or interpretation must match a claim's source ID and value.
- Do not calculate new figures, percentage changes, or deltas. Use only numbers in the supplied source facts.
- Do not state causes or attribute changes unless the entire causal sentence is quoted verbatim from a supplied PDF excerpt with its period_label and page.
- Do not introduce facts about another period, forecasts, targets, or unsupported recommendations.
- Do not return a confidence score.
"""

    try:
        response = await _request_insights(
            prompt,
            f"{period_a.label} vs {period_b.label}",
        )
        payload = json.loads(response.choices[0].message.content or "{}")
    except Exception as exc:
        status = _response_status(exc)
        if status in (413, 429):
            provider_error = _provider_error_insight(exc, f"{period_a.label} vs {period_b.label}", [])
            raise HTTPException(
                status_code=status,
                detail=provider_error[-1].insight,
            ) from exc
        logger.exception(
            "AI period comparison failed for %s and %s",
            period_a.label,
            period_b.label,
        )
        raise HTTPException(status_code=502, detail="AI period comparison failed.") from exc

    candidate = payload.get("comparison") if isinstance(payload, dict) else None
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=502, detail="AI response did not contain a comparison.")
    verified, reason = _validate_candidate(candidate, source_index, supplied_pages)
    if verified is None:
        logger.warning(
            "Discarding unverifiable comparison for %s and %s (%s)",
            period_a.label,
            period_b.label,
            reason,
        )
        raise HTTPException(
            status_code=502,
            detail="AI comparison did not pass deterministic source and number checks.",
        )

    section = f"Period comparison: {period_a.label} vs {period_b.label}"
    await db.execute(
        delete(Insight).where(
            Insight.period_id.in_([period_a.id, period_b.id]),
            Insight.section == section,
        )
    )
    persisted = [
        Insight(
            period_id=period_id,
            section=section,
            text=json.dumps(
                {"data": verified.data, "interpretation": verified.interpretation},
                ensure_ascii=False,
            ),
            confidence=verified.confidence,
            sources_json=json.dumps(
                {
                    "version": 1,
                    "claims": [source.model_dump() for source in verified.sources],
                    "evidence": [item.model_dump() for item in verified.evidence],
                    "period_labels": [period_a.label, period_b.label],
                },
                ensure_ascii=False,
            ),
        )
        for period_id in {period_a.id, period_b.id}
    ]
    db.add_all(persisted)
    await db.commit()

    return PeriodCompareResponse(
        label_a=period_a.label,
        label_b=period_b.label,
        insight=verified.insight,
        confidence=verified.confidence,
        data=verified.data,
        interpretation=verified.interpretation,
        verified_claims=verified.verified_claims,
        total_claims=verified.total_claims,
        sources=verified.sources,
        evidence=verified.evidence,
    )