"""LLM-based structured extractor (the second, independent extractor).

The deterministic regex parser and this LLM extractor read the *same* document
independently. Neither corrects the other: a separate reconciliation step
compares them line by line.

Two guarantees live here:

* the model must return a fixed JSON schema — one entry per line item with a
  numeric value, the source page and the verbatim printed line;
* every returned figure is **verified against page text**. A wrong page is only
  corrected when the figure exactly matches the deterministic value and the
  printed row can be located under the right statement heading or an
  unambiguous continuation page.
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
MAX_COMPLETION_TOKENS = 3000
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
    column: Optional[int] = None
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
    expected_column: Optional[int] = None,
) -> Tuple[List[StatementLine], List[Dict[str, Any]]]:
    """Normalize model output while retaining why individual entries were skipped."""
    lines: List[StatementLine] = []
    discarded: List[Dict[str, Any]] = []
    if not isinstance(data, dict):
        return lines, [{"reason": "response_not_object", "entry": data}]

    data = _unwrap_payload(data)
    if not isinstance(data, dict):
        return lines, [{"reason": "response_not_object", "entry": data}]

    for statement, entries in data.items():
        if statement not in STATEMENTS and isinstance(entries, list):
            discarded.extend(
                {
                    "statement": str(statement),
                    "reason": "unknown_statement",
                    "entry": entry,
                }
                for entry in entries
            )

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
            raw_column = entry.get("column")
            try:
                returned_column = (
                    int(raw_column) if raw_column is not None else expected_column
                )
            except (TypeError, ValueError):
                discarded.append(
                    {
                        "statement": statement,
                        "line_item": str(label).strip(),
                        "value": value,
                        "page": page,
                        "reason": "invalid_column",
                        "entry": entry,
                    }
                )
                continue
            if (
                expected_column is not None
                and returned_column is not None
                and returned_column != expected_column
            ):
                discarded.append(
                    {
                        "statement": statement,
                        "line_item": str(label).strip(),
                        "value": value,
                        "page": page,
                        "column": returned_column,
                        "reason": "wrong_column",
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
                    column=returned_column,
                    meta={
                        "column_inferred_from_request": raw_column is None
                        and expected_column is not None
                    },
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


def _has_statement_heading(statement: str, text: str) -> bool:
    patterns = {
        "pnl": r"\bprofit and loss\b|\bincome statement\b",
        "balance_sheet": r"\bbalance sheet\b|\bstatement of financial position\b",
        "cash_flow": r"\bcash\s*flows?\b|\bcashflow\b",
    }
    return bool(re.search(patterns.get(statement, r"(?!)"), text, re.IGNORECASE))


def _has_any_statement_heading(text: str) -> bool:
    return any(_has_statement_heading(statement, text) for statement in STATEMENTS)


NUMBER_TOKEN_RE = re.compile(r"(?<![\w])(?:\(?-?\d[\d,]*(?:\.\d+)?\)?)(?![\w])")
UNLABELLED_TOTAL_ANCHORS = {
    "Total Fixed Assets": "Tangible Assets",
    "Total Current Assets": "Cash and cash equivalents",
}
GROUNDING_LABEL_ALIASES = {
    "Group operating loss": ("Operating loss",),
    "Revenue": ("Turnover",),
    "Loss for the period": ("Loss for the 6 month period",),
    "Net cash from operations": ("Net Cash used in operating activities",),
    "Net cash from investing": ("Net Cash used in investment activities",),
    "Net cash from financing": ("Net Cash inflow from financing activities",),
    "Equity": ("Equity attributable to owners of the company",),
    "Cash at start": ("Cash and cash equivalent at beginning of period",),
    "Cash at end": ("Cash and cash equivalent at end of period",),
}


def _column_value_matches(text: str, label_end: int, value: float, column: int) -> bool:
    tokens = [
        parse_value(match.group(0))
        for match in NUMBER_TOKEN_RE.finditer(text[label_end:])
    ]
    tokens = [token for token in tokens if token is not None]
    return (
        len(tokens) > column
        and abs(float(tokens[column])) == abs(value)
    )


def _statement_pages_for_line(
    pages: Sequence[Tuple[int, str]], line: StatementLine
) -> List[int]:
    heading_patterns = {
        statement: re.compile(
            {
                "pnl": r"\bprofit and loss\b|\bincome statement\b",
                "balance_sheet": r"\bbalance sheet\b|\bstatement of financial position\b",
                "cash_flow": r"\bcash\s*flows?\b|\bcashflow\b",
            }[statement],
            re.IGNORECASE,
        )
        for statement in STATEMENTS
    }
    active_statement: Optional[str] = None
    matching_pages = set()
    labels = (
        line.line_item,
        *GROUNDING_LABEL_ALIASES.get(line.line_item, ()),
    )
    normalized_labels = [
        " ".join(label.casefold().split())
        for label in labels
    ]
    column = line.column or 0
    has_statement_headings = any(_has_any_statement_heading(text) for _, text in pages)
    for page, page_content in pages:
        page_lines = page_content.splitlines()
        for index, text_line in enumerate(page_lines):
            for statement, pattern in heading_patterns.items():
                if pattern.search(text_line):
                    active_statement = statement
                    break
            normalized_line = " ".join(text_line.casefold().split())
            if active_statement not in (None, line.statement):
                continue
            if active_statement is None and has_statement_headings:
                continue

            label_match = next(
                (
                    match
                    for label in normalized_labels
                    if (match := re.search(re.escape(label), normalized_line))
                ),
                None,
            )
            if label_match:
                row_after_label = normalized_line[label_match.end() :]
                if _column_value_matches(row_after_label, 0, line.value, column):
                    matching_pages.add(page)
                    continue
                if not NUMBER_TOKEN_RE.search(row_after_label):
                    for continuation in page_lines[index + 1 : index + 3]:
                        if re.fullmatch(r"[\s€£$(),.\-+\d]+", continuation) and (
                            _column_value_matches(continuation, 0, line.value, column)
                        ):
                            matching_pages.add(page)
                            break
                    if page in matching_pages:
                        continue
            elif not NUMBER_TOKEN_RE.search(normalized_line):
                for continuation in page_lines[index + 1 : index + 2]:
                    normalized_continuation = " ".join(
                        continuation.casefold().split()
                    )
                    if not NUMBER_TOKEN_RE.search(normalized_continuation):
                        continue
                    combined = f"{normalized_line} {normalized_continuation}"
                    for label in normalized_labels:
                        match = re.search(re.escape(label), combined)
                        if match is None:
                            continue
                        split = len(normalized_line) - match.start()
                        if (
                            split <= 0
                            or split >= len(label)
                            or not label[split].isspace()
                            or normalized_line[match.start() :].rstrip()
                            != label[:split].rstrip()
                        ):
                            continue
                        label_continuation = label[split:].lstrip()
                        if not normalized_continuation.startswith(label_continuation):
                            continue
                        remainder = normalized_continuation[len(label_continuation) :]
                        if not re.fullmatch(r"[\s€£$(),.+\-0-9]+", remainder):
                            continue
                        if _column_value_matches(remainder, 0, line.value, column):
                            matching_pages.add(page)
                            break
                    if page in matching_pages:
                        break

            anchor = UNLABELLED_TOTAL_ANCHORS.get(line.line_item)
            if not anchor:
                continue
            anchor_match = re.search(
                re.escape(" ".join(anchor.casefold().split())), normalized_line
            )
            if anchor_match is None:
                continue
            for total_line in page_lines[index + 1 : index + 3]:
                if _column_value_matches(total_line, 0, line.value, column):
                    matching_pages.add(page)
                    break
    return sorted(matching_pages)


def ground_items(
    pages: Sequence[Tuple[int, str]],
    lines: Sequence[StatementLine],
) -> Tuple[List[StatementLine], List[StatementLine]]:
    """Split lines into (grounded, ungrounded) using document text and parser data.

    A value is grounded only when its label and the figure in the requested
    numeric column appear together in document text. The model's page is only a
    hint: a unique textual match determines the page.
    """
    grounded: List[StatementLine] = []
    ungrounded: List[StatementLine] = []
    for line in lines:
        statement_pages = _statement_pages_for_line(pages, line)
        if line.page in statement_pages:
            resolved_page = line.page
        elif len(statement_pages) == 1:
            resolved_page = statement_pages[0]
        elif not statement_pages:
            resolved_page = None
            claimed_text = next((text for page, text in pages if page == line.page), "")
            reason = "value_not_found_on_claimed_page"
            if any(
                candidate in claimed_text
                for candidate in _value_candidates(line.value)
            ):
                reason = "label_value_column_not_found"
            line.meta["grounding_failure"] = reason
        else:
            resolved_page = None
            line.meta["grounding_failure"] = "ambiguous_text_match"
        ok = resolved_page is not None
        if ok and resolved_page != line.page:
            line.meta["claimed_page"] = line.page
            line.meta["page_recovered"] = True
            line.meta["grounding_source"] = "label_value_column_text"
            line.page = resolved_page
        line.grounded = ok
        (grounded if ok else ungrounded).append(line)
    return grounded, ungrounded


# --------------------------------------------------------------------------- #
# LLM call
# --------------------------------------------------------------------------- #
COLUMN_PROMPT = """You are a financial statements extractor. You are given the text of a
company report with financial statements and page markers like [p.N].

Extract EVERY printed line item, including subtotals, zero values and cash-flow
rows. Preserve each label exactly as printed; do not calculate, infer or rename.
Use ONLY the {column_name} column. Ignore {other_name}. Keep negative signs.

Return ONLY a JSON object with this exact schema:
{
  "pnl":           [{"line_item": "<original label>", "value": <number>, "page": <int>, "column": {column_number}}],
  "balance_sheet": [ ... same shape ... ],
  "cash_flow":     [ ... same shape ... ]
}

Use a plain numeric value in euros. Do not include source_text.
Document:
{document}"""


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
        template = template.replace("{column_number}", str(column))
    else:
        template = COLUMN_PROMPT.replace(
            "{column_name}", "only the requested period"
        ).replace("{other_name}", "all other columns").replace(
            "{column_number}", "0"
        )
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
    finish_reason = getattr(response.choices[0], "finish_reason", None)
    truncated = finish_reason == "length"
    usage = getattr(response, "usage", None)
    response_metadata = {
        "finish_reason": finish_reason,
        "truncated": truncated,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }
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
                **response_metadata,
                "warning": (
                    "LLM respondió de forma truncada; se usará solo regex."
                    if truncated
                    else "LLM no disponible: la respuesta estaba vacía; se usará solo regex."
                ),
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
                **response_metadata,
                "warning": (
                    "LLM respondió de forma truncada (finish_reason=length) y el JSON "
                    "no se pudo leer; se usará solo regex."
                    if truncated
                    else "LLM no disponible: JSON inválido; se usará solo regex."
                ),
            },
        )
    normalized, normalization_discarded = normalize_payload_with_discarded(
        data,
        expected_column=column,
    )
    grounded, ungrounded = ground_items(pages, normalized)
    ground_discarded = [
        {
            "statement": line.statement,
            "line_item": line.line_item,
            "value": line.value,
            "page": line.page,
            "reason": line.meta.get("grounding_failure") or (
                "claimed_page_not_in_document"
                if line.page not in {page for page, _text in pages}
                else "value_not_found_on_claimed_page"
            ),
        }
        for line in ungrounded
    ]
    discarded = normalization_discarded + ground_discarded
    trace = {
        "status": (
            "truncated" if grounded and truncated else "ok" if grounded else "regex_only"
        ),
        "api_result": "ok",
        "model": settings.GROQ_MODEL,
        "error_code": None,
        "latency_ms": elapsed_ms,
        "rate_limit_wait_ms": rate_wait_ms,
        "attempts": attempts,
        "estimated_input_tokens": estimated_input_tokens,
        "max_tokens": completion_tokens,
        "estimated_request_tokens": request_budget,
        **response_metadata,
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
                "claimed_page": line.meta.get("claimed_page", line.page),
                "page_recovered": line.meta.get("page_recovered", False),
                "column": line.column,
                "source_text": line.source_text,
            }
            for line in grounded
        ],
        "discarded_lines": discarded,
        "raw_response": raw_response,
    }
    if not grounded:
        trace["warning"] = (
            "LLM respondió de forma truncada y no devolvió cifras fundamentadas; "
            "se usará solo regex."
            if truncated
            else "LLM no disponible: no devolvió cifras fundamentadas; se usará solo regex."
        )
    elif expected_count is not None and len(grounded) < expected_count:
        missing_count = max((expected_count or 0) - len(grounded), 0)
        trace["status"] = "truncated" if truncated else "partial"
        coverage_warning = (
            f"LLM devolvió {len(grounded)} partidas fundamentadas de "
            f"{expected_count if expected_count is not None else len(normalized)} esperadas; "
            f"{missing_count} ausentes."
        )
        trace["warning"] = (
            f"LLM respondió de forma truncada (finish_reason=length). {coverage_warning}"
            if truncated
            else coverage_warning
        )
    elif truncated:
        trace["warning"] = (
            "LLM respondió de forma truncada (finish_reason=length); "
            "la extracción podría estar incompleta."
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
