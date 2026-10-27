"""Deterministic checks for figures repeated in financial statement notes."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

from app.services.llm_extract import parse_value

NOTE_AMOUNT_RE = re.compile(r"(?:€\s*)?(-?\(?\d[\d,]*(?:\.\d+)?\)?)")


def find_note_mismatches(
    pages: Sequence[Tuple[int, str]],
    statement_pages: set[int],
    deterministic: Dict[str, Any],
    tolerance: float = 1.0,
) -> List[Dict[str, Any]]:
    """Report note values that disagree with the parsed statement value."""
    warnings: List[Dict[str, Any]] = []
    for statement in ("pnl", "balance_sheet", "cash_flow"):
        for label, raw_table_value in (deterministic.get(statement) or {}).items():
            table_value = parse_value(raw_table_value)
            if table_value is None:
                continue
            label_re = re.compile(rf"\b{re.escape(label)}\b", re.IGNORECASE)
            for page_number, text in pages:
                if page_number in statement_pages:
                    continue
                for line in text.splitlines():
                    match = label_re.search(line)
                    if not match:
                        continue
                    amount_match = NOTE_AMOUNT_RE.search(line, match.end())
                    if amount_match is None:
                        continue
                    note_value = parse_value(amount_match.group(1))
                    if note_value is None or abs(note_value - table_value) <= tolerance:
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
