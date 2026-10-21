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
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import settings

STATEMENTS = ("pnl", "balance_sheet", "cash_flow")


@dataclass
class StatementLine:
    statement: str            # pnl | balance_sheet | cash_flow
    line_item: str
    value: float
    page: int
    source_text: str = ""
    grounded: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)


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


def normalize_payload(data: Any) -> List[StatementLine]:
    """Turn the model's JSON into StatementLine objects, whatever the shape."""
    lines: List[StatementLine] = []
    if not isinstance(data, dict):
        return lines

    # The model sometimes wraps the statements under a key.
    for wrapper in ("statements", "data", "result", "financials"):
        inner = data.get(wrapper)
        if isinstance(inner, dict) and any(k in inner for k in STATEMENTS):
            data = inner
            break

    for statement in STATEMENTS:
        for entry in data.get(statement) or []:
            if not isinstance(entry, dict):
                continue
            value = parse_value(entry.get("value", entry.get("amount")))
            label = entry.get("line_item") or entry.get("label") or entry.get("name")
            if value is None or not label:
                continue
            try:
                page = int(entry.get("page") or entry.get("page_number") or 0)
            except (TypeError, ValueError):
                page = 0
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
) -> List[StatementLine]:
    """Ask the model for the statements and return the grounded lines.

    If ``column`` is provided, the model is instructed to extract only from that
    table column (0 = current/leftmost, 1 = comparative, etc.) and ignore the
    other columns.
    """
    client = client or build_client()
    if client is None:
        return []

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
            raise last_error
        return []

    data = json.loads(response.choices[0].message.content)
    lines = normalize_payload(data)
    grounded, _ungrounded = ground_items(pages, lines)
    return grounded
