"""LLM-based structured extractor (the second, independent extractor).

The deterministic regex parser and this LLM extractor read the *same* document
independently. Neither corrects the other: a separate reconciliation step
compares them line by line.

Two guarantees live here:

* the model must return a fixed JSON schema — one entry per line item with a
  numeric value, the source page and the verbatim printed line;
* every returned figure is **verified against the page text** it claims to come
  from, so the model cannot invent numbers (see ``ground_items``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import settings

STATEMENTS = ("pnl", "balance_sheet", "cash_flow")
logger = logging.getLogger(__name__)


@dataclass
class StatementLine:
    statement: str            # pnl | balance_sheet | cash_flow
    line_item: str
    value: float
    page: int
    source_text: str = ""
    grounded: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMExtractionResult:
    lines: List[StatementLine]
    trace: Dict[str, Any]


# --------------------------------------------------------------------------- #
# pure helpers (unit-tested without the LLM)
# --------------------------------------------------------------------------- #
def parse_value(raw: Any) -> Optional[float]:
    """Coerce a figure to float. Handles '1,238,931', '(483,753)', '€64,861'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    digits = re.sub(r"[^0-9.]", "", text)
    if digits in ("", "."):
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return -value if negative else value


def value_candidates(value: float) -> List[str]:
    """Printed forms the value could take: '669,550' and '669550'."""
    whole = abs(int(round(value)))
    return [f"{whole:,}", str(whole)]


def _unwrap_payload(data: Dict[str, Any]) -> Any:
    for wrapper in ("statements", "data", "result", "financials"):
        inner = data.get(wrapper)
        if isinstance(inner, dict) and any(key in inner for key in STATEMENTS):
            return inner
    return data


def normalize_payload_with_discarded(
    data: Any,
) -> Tuple[List[StatementLine], List[Dict[str, Any]]]:
    """Normalize model output while retaining why individual entries were skipped."""
    lines: List[StatementLine] = []
    discarded: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return lines, [{"reason": "response_not_object", "entry": data}]

    data = _unwrap_payload(data)
    if not isinstance(data, dict):
        return lines, [{"reason": "response_not_object", "entry": data}]

    for statement in STATEMENTS:
        entries = data.get(statement)
        if entries is None:
            continue
        if not isinstance(entries, list):
            discarded.append(
                {"statement": statement, "reason": "statement_not_array", "entry": entries}
            )
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                discarded.append(
                    {"statement": statement, "reason": "entry_not_object", "entry": entry}
                )
                continue
            value = parse_value(entry.get("value", entry.get("amount")))
            label = entry.get("line_item") or entry.get("label") or entry.get("name")
            if not label:
                discarded.append(
                    {"statement": statement, "reason": "missing_label", "entry": entry}
                )
                continue
            if value is None:
                discarded.append(
                    {
                        "statement": statement,
                        "line_item": str(label).strip(),
                        "reason": "invalid_value",
                        "entry": entry,
                    }
                )
                continue
            try:
                page = int(entry.get("page") or entry.get("page_number") or 0)
            except (TypeError, ValueError):
                page = 0
            if page < 1:
                discarded.append(
                    {
                        "statement": statement,
                        "line_item": str(label).strip(),
                        "value": value,
                        "reason": "invalid_page",
                        "entry": entry,
                    }
                )
                continue
            lines.append(
                StatementLine(
                    statement=statement,
                    line_item=str(label).strip(),
                    value=value,
                    page=page,
                    source_text=str(
                        entry.get("source_text") or entry.get("text") or entry.get("line") or ""
                    ),
                )
            )
    return lines, discarded


def normalize_payload(data: Any) -> List[StatementLine]:
    """Turn the model's JSON into StatementLine objects, whatever the shape."""
    lines, _discarded = normalize_payload_with_discarded(data)
    return lines


def _value_candidates(value: float) -> List[str]:
    whole = abs(int(round(value)))
    return [f"{whole:,}", str(whole)]


def ground_items(
    pages: Sequence[Tuple[int, str]], lines: Sequence[StatementLine]
) -> Tuple[List[StatementLine], List[StatementLine]]:
    """Split lines into (grounded, ungrounded) by checking the claimed page.

    A value is grounded when its digits — with or without thousands separators —
    appear in the text of the page it claims to come from. Sign is ignored, so
    a value of -483753 matches "(483,753)" or "483,753".
    """
    page_text = {num: text for num, text in pages}
    grounded: List[StatementLine] = []
    ungrounded: List[StatementLine] = []
    for line in lines:
        text = page_text.get(line.page, "")
        ok = bool(text) and any(cand in text for cand in _value_candidates(line.value))
        line.grounded = ok
        (grounded if ok else ungrounded).append(line)
    return grounded, ungrounded


# --------------------------------------------------------------------------- #
# LLM call
# --------------------------------------------------------------------------- #
PROMPT = """You are a financial statements extractor. You are given the text of a
company report with page markers like [p.N].

Return ONLY a JSON object with this exact schema:
{
  "pnl":           [{"line_item": "<label>", "value": <number>, "page": <int>, "source_text": "<verbatim line>"}],
  "balance_sheet": [ ... same shape ... ],
  "cash_flow":     [ ... same shape ... ]
}

Rules:
- value is a plain number in euros: no currency symbol, no thousands separators. Use a negative number for losses/outflows exactly as printed.
- page is the integer page number (from the [p.N] marker) where the line appears.
- source_text must be copied verbatim from that page.
- Include ONLY line items that are actually printed in the document. Never compute, infer, estimate or rename figures.
- If a statement is not present, return an empty list for it.

Document:
---
{document}
---"""

COLUMN_PROMPT = """You are a financial statements extractor. You are given the text of a
company report with page markers like [p.N].

The report contains financial statements with multiple columns of figures. The
{column_name} column is the one you must extract. The {other_name} column
(comparative/prior period) should be IGNORED.

Return ONLY a JSON object with this exact schema:
{
  "pnl":           [{"line_item": "<label>", "value": <number>, "page": <int>, "source_text": "<verbatim line>"}],
  "balance_sheet": [ ... same shape ... ],
  "cash_flow":     [ ... same shape ... ]
}

Rules:
- value is a plain number in euros: no currency symbol, no thousands separators. Use a negative number for losses/outflows exactly as printed.
- page is the integer page number (from the [p.N] marker) where the line appears.
- source_text must be copied verbatim from that page.
- Include ONLY line items from the {column_name} column. Never compute, infer, estimate or rename figures.
- If a statement is not present, return an empty list for it.
- DO NOT include figures from the {other_name} column.

Document:
---
{document}
---"""


def annotate_pages(pages: Sequence[Tuple[int, str]]) -> str:
    return "\n".join(f"[p.{num}]\n{text}" for num, text in pages)


def build_client():
    if not settings.GROQ_API_KEY:
        return None
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=settings.GROQ_BASE_URL, api_key=settings.GROQ_API_KEY)


async def extract_structured(
    pages: Sequence[Tuple[int, str]],
    period_label: str,
    client: Any = None,
    max_attempts: int = 3,
    column: Optional[int] = None,
) -> LLMExtractionResult:
    """Ask the model for the statements and return the grounded lines.

    If ``column`` is provided, the model is instructed to extract only from that
    table column (0 = current/leftmost, 1 = comparative, etc.) and ignore the
    other columns.
    """
    client = client or build_client()
    if client is None:
        return LLMExtractionResult(
            lines=[],
            trace={
                "status": "regex_only",
                "api_result": "not_configured",
                "model": settings.GROQ_MODEL,
                "returned_lines": [],
                "discarded_lines": [],
                "warning": "LLM is not configured; extraction will use regex only.",
            },
        )

    import asyncio

    if column is not None:
        column_names = {0: "current period (leftmost)", 1: "comparative prior-period (second)", 2: "third comparative"}
        column_name = column_names.get(column, f"column {column}")
        other_names = {0: "comparative prior-period column(s)", 1: "current period column", 2: "other comparative column(s)"}
        other_name = other_names.get(column, "other column(s)")
        prompt = COLUMN_PROMPT.replace("{column_name}", column_name).replace("{other_name}", other_name)
    else:
        prompt = PROMPT
    prompt = prompt.replace("{document}", annotate_pages(pages))
    response = None
    last_error: Optional[Exception] = None
    started = perf_counter()
    for attempt in range(max_attempts):
        try:
            response = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=4000,
                response_format={"type": "json_object"},
            )
            break
        except Exception as exc:  # transient rate limits / network blips
            last_error = exc
            await asyncio.sleep(1.5 * (attempt + 1))
    if response is None:
        if last_error:
            elapsed_ms = round((perf_counter() - started) * 1000)
            status_code = getattr(last_error, "status_code", None)
            trace = {
                "status": "regex_only",
                "api_result": "error",
                "error_code": str(status_code or type(last_error).__name__),
                "error_message": str(last_error),
                "model": settings.GROQ_MODEL,
                "latency_ms": elapsed_ms,
                "attempts": max_attempts,
                "returned_lines": [],
                "discarded_lines": [],
                "warning": f"LLM is unavailable: {last_error}",
            }
            return LLMExtractionResult(lines=[], trace=trace)
        return LLMExtractionResult(
            lines=[],
            trace={
                "status": "regex_only",
                "api_result": "error",
                "error_code": "no_response",
                "model": settings.GROQ_MODEL,
                "returned_lines": [],
                "discarded_lines": [],
                "warning": "LLM returned no response; extraction will use regex only.",
            },
        )

    elapsed_ms = round((perf_counter() - started) * 1000)
    raw_response = response.choices[0].message.content or ""
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return LLMExtractionResult(
            lines=[],
            trace={
                "status": "regex_only",
                "api_result": "error",
                "error_code": "invalid_json",
                "error_message": str(exc),
                "model": settings.GROQ_MODEL,
                "latency_ms": elapsed_ms,
                "returned_lines": [],
                "discarded_lines": [
                    {"reason": "invalid_json", "entry": raw_response[:2000]}
                ],
                "raw_response": raw_response,
                "warning": "LLM returned invalid JSON; extraction will use regex only.",
            },
        )
    normalized, normalization_discarded = normalize_payload_with_discarded(data)
    grounded, ungrounded = ground_items(pages, normalized)
    ground_discarded = [
        {
            "statement": line.statement,
            "line_item": line.line_item,
            "value": line.value,
            "page": line.page,
            "reason": (
                "claimed_page_not_in_document"
                if line.page not in {page for page, _text in pages}
                else "value_not_found_on_claimed_page"
            ),
        }
        for line in ungrounded
    ]
    discarded = normalization_discarded + ground_discarded
    usage = getattr(response, "usage", None)
    trace = {
        "status": "ok" if grounded else "regex_only",
        "api_result": "ok",
        "model": settings.GROQ_MODEL,
        "latency_ms": elapsed_ms,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "raw_item_count": (
            sum(
                len(value)
                for value in _unwrap_payload(data).values()
                if isinstance(value, list)
            )
            if isinstance(_unwrap_payload(data), dict)
            else 0
        ),
        "normalized_item_count": len(normalized),
        "accepted_count": len(grounded),
        "discarded_count": len(discarded),
        "returned_lines": [
            {
                "statement": line.statement,
                "line_item": line.line_item,
                "value": line.value,
                "page": line.page,
                "source_text": line.source_text,
            }
            for line in grounded
        ],
        "discarded_lines": discarded,
        "raw_response": raw_response,
    }
    if not grounded:
        trace["warning"] = (
            "LLM returned no grounded financial statement items; extraction will use regex only."
        )
    elif discarded:
        trace["warning"] = (
            f"LLM returned {len(grounded)} grounded items and "
            f"{len(discarded)} items were discarded."
        )
    logger.info(
        "LLM extraction result model=%s period=%s latency_ms=%s "
        "accepted=%s discarded=%s",
        settings.GROQ_MODEL,
        period_label,
        elapsed_ms,
        len(grounded),
        len(discarded),
    )
    return LLMExtractionResult(lines=grounded, trace=trace)
