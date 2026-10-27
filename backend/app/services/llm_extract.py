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

import asyncio
import email.utils
import json
import logging
import math
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from app.core.config import settings

STATEMENTS = ("pnl", "balance_sheet", "cash_flow")
TPM_LIMIT = 8000
MAX_COMPLETION_TOKENS = 1800
TOKEN_ESTIMATE_SAFETY_MARGIN = 200
_TOKEN_RESERVATIONS: deque[Tuple[float, int]] = deque()
_TOKEN_RESERVATION_LOCK = threading.Lock()
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


def build_prompt(pages: Sequence[Tuple[int, str]], column: Optional[int]) -> str:
    if column is not None:
        column_names = {
            0: "current period (leftmost)",
            1: "comparative prior-period (second)",
            2: "third comparative",
        }
        other_names = {
            0: "comparative prior-period column(s)",
            1: "current period column",
            2: "other comparative column(s)",
        }
        column_name = column_names.get(column, f"column {column}")
        other_name = other_names.get(column, "other column(s)")
        template = COLUMN_PROMPT.replace("{column_name}", column_name).replace(
            "{other_name}", other_name
        )
    else:
        template = PROMPT
    return template.replace("{document}", annotate_pages(pages))


def select_statement_pages(
    pages: Sequence[Tuple[int, str]], deterministic: Dict[str, Any]
) -> List[Tuple[int, str]]:
    """Keep only pages identified as statement pages, excluding narrative notes."""
    statement_pages = {
        int(page)
        for values in (deterministic.get("provenance") or {}).values()
        for page in values.values()
        if page
    }
    heading = re.compile(
        r"^\s*(?:condensed\s+)?(?:consolidated\s+)?(?:profit and loss account|"
        r"balance sheet|cash flow statement|statement of cash flows|"
        r"statement of financial position|statement of comprehensive income)\b",
        re.IGNORECASE,
    )
    continuation_markers = re.compile(
        r"\b(?:payments to acquire|net cash (?:inflow|used)|receipt\s*/\s*\(repayment\)|"
        r"cash and cash equivalent at (?:beginning|end) of period)\b",
        re.IGNORECASE,
    )
    heading_pages = set()
    for page_number, text in pages:
        if any(heading.search(line) for line in text.splitlines()):
            statement_pages.add(page_number)
            heading_pages.add(page_number)
    for page_number, text in pages:
        if (
            page_number - 1 in heading_pages
            and not re.search(r"\bnotes to\b", text, re.IGNORECASE)
            and continuation_markers.search(text)
        ):
            statement_pages.add(page_number)
    return [(number, text) for number, text in pages if number in statement_pages]


def estimate_tokens(text: str) -> int:
    """Conservative rough estimate; not a provider-tokenizer measurement."""
    return math.ceil(len(text.encode("utf-8")) / 3.5)


async def reserve_token_budget(
    estimated_tokens: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> float:
    """Reserve a process-wide rolling-minute token budget before a Groq call."""
    if estimated_tokens > TPM_LIMIT:
        raise ValueError(f"Estimated request ({estimated_tokens}) exceeds {TPM_LIMIT} TPM.")
    total_wait = 0.0
    while True:
        with _TOKEN_RESERVATION_LOCK:
            now = clock()
            while _TOKEN_RESERVATIONS and now - _TOKEN_RESERVATIONS[0][0] >= 60:
                _TOKEN_RESERVATIONS.popleft()
            reserved = sum(tokens for _timestamp, tokens in _TOKEN_RESERVATIONS)
            if reserved + estimated_tokens <= TPM_LIMIT:
                _TOKEN_RESERVATIONS.append((now, estimated_tokens))
                return total_wait
            wait_seconds = max(0.0, _TOKEN_RESERVATIONS[0][0] + 60 - now)
        await sleep(wait_seconds)
        total_wait += wait_seconds


def build_client():
    if not settings.GROQ_API_KEY:
        return None
    from openai import AsyncOpenAI

    return AsyncOpenAI(base_url=settings.GROQ_BASE_URL, api_key=settings.GROQ_API_KEY)


def _status_code(error: Exception) -> Optional[int]:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(error: Exception) -> Optional[float]:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    normalized = {str(key).lower(): str(value).strip() for key, value in headers.items()}
    retry_after = normalized.get("retry-after")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            try:
                target = email.utils.parsedate_to_datetime(retry_after)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    for header in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        value = normalized.get(header)
        if value:
            match = re.fullmatch(r"([\d.]+)\s*(ms|s|m)?", value, re.IGNORECASE)
            if match:
                amount = float(match.group(1))
                unit = (match.group(2) or "s").lower()
                return amount / 1000 if unit == "ms" else amount * (60 if unit == "m" else 1)
    return None


def _retryable(error: Exception) -> bool:
    status = _status_code(error)
    return status is None or status == 408 or status == 429 or status >= 500


async def extract_structured(
    pages: Sequence[Tuple[int, str]],
    period_label: str,
    client: Any = None,
    max_attempts: int = 3,
    column: Optional[int] = None,
    expected_count: Optional[int] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    reserve_budget: Callable[..., Awaitable[float]] = reserve_token_budget,
    document_id: Optional[int] = None,
) -> LLMExtractionResult:
    """Ask the model for the statements and return the grounded lines.

    If ``column`` is provided, the model is instructed to extract only from that
    table column (0 = current/leftmost, 1 = comparative, etc.) and ignore the
    other columns.
    """
    attempts_detail: List[Dict[str, Any]] = []

    def finish(lines: List[StatementLine], trace: Dict[str, Any]) -> LLMExtractionResult:
        trace.setdefault("document_id", document_id)
        trace.setdefault("period_label", period_label)
        trace.setdefault("call_id", uuid.uuid4().hex)
        trace.setdefault("completed_at", datetime.now(timezone.utc).isoformat())
        trace.setdefault("attempts_detail", attempts_detail)
        return LLMExtractionResult(lines=lines, trace=trace)

    client = client or build_client()
    if client is None:
        return finish(
            [],
            {
                "status": "regex_only",
                "api_result": "not_configured",
                "model": settings.GROQ_MODEL,
                "document_id": document_id,
                "period_label": period_label,
                "returned_lines": [],
                "discarded_lines": [],
                "error_code": "missing_api_key",
                "warning": "LLM no disponible: falta configurar la clave de Groq; se usará solo regex.",
            },
        )

    prompt = build_prompt(pages, column)
    if not pages:
        return finish(
            [],
            {
                "status": "regex_only",
                "api_result": "not_called",
                "error_code": "no_statement_pages",
                "model": settings.GROQ_MODEL,
                "document_id": document_id,
                "period_label": period_label,
                "returned_lines": [],
                "discarded_lines": [],
                "warning": "LLM no disponible: no se detectaron páginas de estados financieros; se usará solo regex.",
            },
        )
    estimated_input_tokens = estimate_tokens(prompt)
    completion_tokens = min(
        MAX_COMPLETION_TOKENS,
        TPM_LIMIT - estimated_input_tokens - TOKEN_ESTIMATE_SAFETY_MARGIN,
    )
    request_budget = estimated_input_tokens + completion_tokens + TOKEN_ESTIMATE_SAFETY_MARGIN
    if completion_tokens < 400 or request_budget > TPM_LIMIT:
        return finish(
            [],
            {
                "status": "regex_only",
                "api_result": "not_called",
                "error_code": "estimated_request_exceeds_tpm",
                "model": settings.GROQ_MODEL,
                "document_id": document_id,
                "period_label": period_label,
                "estimated_input_tokens": estimated_input_tokens,
                "max_tokens": completion_tokens,
                "estimated_request_tokens": request_budget,
                "returned_lines": [],
                "discarded_lines": [],
                "warning": "LLM no disponible: el prompt estimado no cabe en el límite TPM; se usará solo regex.",
            },
        )
    logger.info(
        "LLM extraction request document_id=%s period=%s model=%s "
        "estimated_input_tokens=%s max_tokens=%s estimated_request_tokens=%s",
        document_id,
        period_label,
        settings.GROQ_MODEL,
        estimated_input_tokens,
        completion_tokens,
        request_budget,
    )
    rate_wait_ms = round(
        await reserve_budget(request_budget, sleep=sleep) * 1000
    )
    reasoning = (
        {"extra_body": {"reasoning_effort": "low"}}
        if "gpt-oss" in settings.GROQ_MODEL.lower()
        else {}
    )
    response = None
    last_error: Optional[Exception] = None
    started = perf_counter()
    attempts = 0
    for attempt in range(max_attempts):
        attempts += 1
        attempt_started = perf_counter()
        try:
            response = await client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=completion_tokens,
                response_format={"type": "json_object"},
                **reasoning,
            )
            attempts_detail.append(
                {
                    "attempt": attempts,
                    "result": "ok",
                    "latency_ms": round((perf_counter() - attempt_started) * 1000),
                }
            )
            break
        except Exception as exc:  # transient rate limits / network blips
            last_error = exc
            if not _retryable(exc) or attempt + 1 >= max_attempts:
                attempts_detail.append(
                    {
                        "attempt": attempts,
                        "result": "error",
                        "error_code": str(_status_code(exc) or type(exc).__name__),
                        "latency_ms": round((perf_counter() - attempt_started) * 1000),
                    }
                )
                break
            delay = _retry_after_seconds(exc)
            delay = delay if delay is not None else 1.5 * (attempt + 1)
            attempts_detail.append(
                {
                    "attempt": attempts,
                    "result": "error",
                    "error_code": str(_status_code(exc) or type(exc).__name__),
                    "latency_ms": round((perf_counter() - attempt_started) * 1000),
                    "retry_delay_seconds": delay,
                }
            )
            await sleep(delay)
    if response is None:
        if last_error:
            elapsed_ms = round((perf_counter() - started) * 1000)
            status_code = getattr(last_error, "status_code", None)
            trace = {
                "status": "regex_only",
                "api_result": "error",
                "error_code": str(status_code or type(last_error).__name__),
                "error_message": str(last_error)[:1000],
                "model": settings.GROQ_MODEL,
                "latency_ms": elapsed_ms,
                "rate_limit_wait_ms": rate_wait_ms,
                "attempts": attempts,
                "estimated_input_tokens": estimated_input_tokens,
                "max_tokens": completion_tokens,
                "estimated_request_tokens": request_budget,
                "returned_lines": [],
                "discarded_lines": [],
                "warning": f"LLM no disponible: {last_error}; se usará solo regex.",
            }
            return finish([], trace)
        return finish(
            [],
            {
                "status": "regex_only",
                "api_result": "error",
                "error_code": "no_response",
                "model": settings.GROQ_MODEL,
                "rate_limit_wait_ms": rate_wait_ms,
                "estimated_input_tokens": estimated_input_tokens,
                "max_tokens": completion_tokens,
                "estimated_request_tokens": request_budget,
                "returned_lines": [],
                "discarded_lines": [],
                "warning": "LLM no disponible: no hubo respuesta; se usará solo regex.",
            },
        )

    elapsed_ms = round((perf_counter() - started) * 1000)
    raw_response = response.choices[0].message.content or ""
    if not raw_response.strip():
        return finish(
            [],
            {
                "status": "regex_only",
                "api_result": "ok",
                "error_code": "empty_response",
                "model": settings.GROQ_MODEL,
                "latency_ms": elapsed_ms,
                "rate_limit_wait_ms": rate_wait_ms,
                "attempts": attempts,
                "estimated_input_tokens": estimated_input_tokens,
                "max_tokens": completion_tokens,
                "estimated_request_tokens": request_budget,
                "returned_lines": [],
                "discarded_lines": [],
                "raw_response": raw_response,
                "warning": "LLM no disponible: la respuesta estaba vacía; se usará solo regex.",
            },
        )
    try:
        data = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return finish(
            [],
            {
                "status": "regex_only",
                "api_result": "error",
                "error_code": "invalid_json",
                "error_message": str(exc),
                "model": settings.GROQ_MODEL,
                "latency_ms": elapsed_ms,
                "rate_limit_wait_ms": rate_wait_ms,
                "attempts": attempts,
                "estimated_input_tokens": estimated_input_tokens,
                "max_tokens": completion_tokens,
                "estimated_request_tokens": request_budget,
                "returned_lines": [],
                "discarded_lines": [
                    {"reason": "invalid_json", "entry": raw_response[:2000]}
                ],
                "raw_response": raw_response,
                "warning": "LLM no disponible: JSON inválido; se usará solo regex.",
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
        "error_code": None,
        "latency_ms": elapsed_ms,
        "rate_limit_wait_ms": rate_wait_ms,
        "attempts": attempts,
        "estimated_input_tokens": estimated_input_tokens,
        "max_tokens": completion_tokens,
        "estimated_request_tokens": request_budget,
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
            "LLM no disponible: no devolvió cifras fundamentadas; se usará solo regex."
        )
    elif discarded or (expected_count is not None and len(grounded) < expected_count):
        missing_count = max((expected_count or 0) - len(grounded), 0)
        trace["status"] = "partial"
        trace["warning"] = (
            f"LLM devolvió {len(grounded)} partidas fundamentadas de "
            f"{expected_count if expected_count is not None else len(normalized)} esperadas; "
            f"{len(discarded)} descartadas y {missing_count} ausentes."
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
    return finish(grounded, trace)
