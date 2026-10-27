"""Deterministic checks for figures repeated in financial statement notes."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Sequence, Tuple

from app.services.llm_extract import parse_value

NOTE_HEADING_RE = re.compile(r"^\s*\d+\.\s+[A-Z]", re.IGNORECASE)
NOTES_EDITOR_RE = re.compile(r"^\s*notes to editors\b", re.IGNORECASE)
NOTE_AMOUNT_RE = re.compile(r"€\s*(-?\(?\d[\d,]*(?:\.\d+)?\)?)\s*([kKmM])?")


def find_note_mismatches(
    pages: Sequence[Tuple[int, str]],
    statement_pages: set[int],
    deterministic: Dict[str, Any],
    tolerance: float = 1.0,
    period_end: date | None = None,
) -> List[Dict[str, Any]]:
    """Report note values that disagree with the parsed statement value."""
    warnings: List[Dict[str, Any]] = []
    last_statement_page = max(statement_pages, default=0)
    period_date = (
        re.compile(
            rf"\b0?{period_end.day}\s+{period_end.strftime('%B')}\s+{period_end.year}\b",
            re.IGNORECASE,
        )
        if period_end
        else None
    )
    note_pages: List[Tuple[int, str]] = []
    in_notes = False
    for page_number, text in pages:
        if page_number <= last_statement_page:
            continue
        lines = text.splitlines()
        if any(NOTES_EDITOR_RE.search(line) for line in lines):
            in_notes = False
            continue
        if any(NOTE_HEADING_RE.search(line) for line in lines):
            in_notes = True
        if in_notes and (
            period_date is None or period_date.search(text)
        ):
            note_pages.append((page_number, text))

    for statement in ("pnl", "balance_sheet", "cash_flow"):
        for label, raw_table_value in (deterministic.get(statement) or {}).items():
            table_value = parse_value(raw_table_value)
            if table_value is None:
                continue
            label_re = re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE)
            for page_number, text in note_pages:
                for line in text.splitlines():
                    match = label_re.search(line)
                    if not match:
                        continue
                    amount_match = NOTE_AMOUNT_RE.search(line, match.end())
                    if amount_match is None:
                        continue
                    note_value = parse_value(amount_match.group(1))
                    suffix = (amount_match.group(2) or "").lower()
                    multiplier = 1000 if suffix == "k" else 1_000_000 if suffix == "m" else 1
                    if note_value is None:
                        continue
                    note_value *= multiplier
                    if abs(abs(note_value) - abs(table_value)) <= tolerance:
                        continue
                    warnings.append(
                        {
                            "statement": statement,
                            "line_item": label,
                            "table_value": table_value,
                            "note_value": note_value,
                            "page": page_number,
                            "quote": line.strip(),
                            "message": (
                                f"{label} is {table_value:,.0f} in the statement but "
                                f"{note_value:,.0f} in the note on page {page_number}."
                            ),
                        }
                    )
    return warnings
