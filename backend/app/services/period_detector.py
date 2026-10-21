from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

import dateutil.parser as dparser

PERIOD_CODE_RE = re.compile(r"\b(HY|FY|H1|H2|Q[1-4])\s*(\d{4})\b", re.I)
DATE_TOKEN_RE = re.compile(
    r"(\d{1,2}[-/\s](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/\s]\d{4})"
    r"|(\d{1,2}[-/\s]\d{1,2}[-/\s]\d{4})"
    r"|((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})"
    r"|(\d{4}[-/\s]\d{1,2}[-/\s]\d{1,2})",
    re.I,
)

@dataclass
class PeriodCandidate:
    label: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    score: float
    page: int
    source: str
    context: str

def _try_parse_date(token: str) -> Optional[date]:
    try:
        dt = dparser.parse(token, dayfirst=True, yearfirst=False)
        return dt.date()
    except (ValueError, OverflowError, TypeError):
        return None

def _infer_start(end: date, scope: str) -> Optional[date]:
    s = scope.lower()
    is_six = 'six months' in s or '6 months' in s or 'h1' in s or 'half' in s
    if is_six:
        if end.month == 12:
            return date(end.year, 7, 1)
        if end.month == 6:
            return date(end.year, 1, 1)
        if end.month >= 7:
            return date(end.year, 7, 1)
        return date(end.year - 1, 7, 1)
    is_year = 'year' in s or 'twelve' in s or '12 months' in s
    if is_year:
        if end.month == 6:
            return date(end.year - 1, 7, 1)
        return date(end.year - 1, 7, 1)
    if 'quarter' in s or '3 months' in s:
        return date(end.year, ((end.month - 1)//3)*3 + 1, 1)
    if end.month == 12:
        return date(end.year, 7, 1)
    if end.month == 6:
        return date(end.year, 1, 1)
    return date(end.year - 1, 7, 1)

def _scope_for(text: str, pos: int) -> str:
    window = text[max(0, pos - 80):pos].lower()
    if 'six months' in window or '6 months' in window or 'half year' in window:
        return 'six months'
    if 'twelve months' in window or '12 months' in window or 'full year' in window:
        return 'year'
    if 'quarter' in window or '3 months' in window:
        return 'quarter'
    return 'six months'

def _label_for(end: date, scope: str) -> Optional[str]:
    sl = scope.lower()
    is_six = 'six months' in sl or '6 months' in sl or 'half' in sl
    if is_six:
        if end.month == 12:
            return f"HY{end.year + 1}"
        if end.month == 6:
            return f"H1{end.year}"
        return f"HY{end.year + 1}" if end.month >= 7 else f"HY{end.year}"
    is_year = 'year' in sl or 'twelve' in sl or '12 months' in sl
    if is_year:
        if end.month == 6:
            return f"FY{end.year}"
    return None

def detect_periods(pages: List[Tuple[int, str]]) -> List[PeriodCandidate]:
    """Detect all period candidates in the document.

    Returns every date found with its inferred label/score. The caller decides
    which one matches the uploaded document.
    """
    candidates: List[PeriodCandidate] = []
    for pnum, text in pages:
        if not text:
            continue
        t = text
        tl = t.lower()
        ctx = t[:200]

        for m in PERIOD_CODE_RE.finditer(t):
            kind, yr = m.group(1), m.group(2)
            label_c = f"{kind.upper()}{yr}"
            candidates.append(PeriodCandidate(label_c, None, None, 45.0, pnum, 'code', ctx))

        for m in DATE_TOKEN_RE.finditer(t):
            token = m.group(0)
            end_d = _try_parse_date(token)
            if not end_d:
                continue
            scope = _scope_for(t, m.start())
            start_d = _infer_start(end_d, scope)
            label = _label_for(end_d, scope)
            score = 40.0
            if 'statement' in tl or 'income' in tl or 'balance' in tl or 'cash' in tl or 'consolidated' in tl:
                score += 30
            if 'condensed' in tl or 'interim' in tl:
                score += 15
            if 'half year' in tl or 'interim results' in tl:
                score += 10
            if score < 0:
                score = 1
            candidates.append(PeriodCandidate(label, start_d, end_d, score, pnum, 'date', ctx))

    dedup: List[PeriodCandidate] = []
    seen = set()
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        key = str(c.end_date) if c.end_date else c.label
        if key not in seen:
            seen.add(key)
            dedup.append(c)
    return dedup

def find_matching_period(doc_label: Optional[str], doc_start, doc_end, pages: List[Tuple[int, str]]):
    """Check whether the user-submitted dates exist in the document.

    The label is intentionally NOT compared -- it's just a user-chosen name.
    Only start_date/end_date must exist in the document.
    Returns (ok, message, details) where details lists all detected periods.
    """
    cands = detect_periods(pages)

    # Collect all detected end dates
    detected_dates = [(c.label, c.start_date, c.end_date) for c in cands if c.end_date]

    # Check if user's dates match any detected date
    date_matched = False
    matched_label = None
    for label, start_d, end_d in detected_dates:
        if doc_start and doc_end and start_d == doc_start and end_d == doc_end:
            date_matched = True
            matched_label = label
            break

    if date_matched:
        return True, f"Period ({doc_start} to {doc_end}) found in document.", cands

    # Build helpful error message
    details = []
    for label, start_d, end_d in detected_dates[:5]:
        details.append(f"{label or 'unknown'}: {start_d} to {end_d}")
    details_str = "; ".join(details) if details else "no dates detected"

    return False, f"Dates not found in document. Detected periods: {details_str}", cands

def validate_against(doc_label: Optional[str], doc_start, doc_end, pages: List[Tuple[int, str]], min_conf: float = 70.0):
    """Validate user-submitted dates against periods found in the document.

    The label is intentionally NOT compared -- it's just a user-chosen name.
    Only start_date/end_date must exist in the document.
    Returns (ok, message, candidates).
    """
    ok, msg, cands = find_matching_period(doc_label, doc_start, doc_end, pages)
    return ok, msg, cands
