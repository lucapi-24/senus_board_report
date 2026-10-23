from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

import dateutil.parser as dparser
from dateutil.relativedelta import relativedelta

# Senus PLC fiscal year ends 30 June.  Label derivation:
#   FY{year}  = 1 Jul {year-1} to 30 Jun {year}
#   HY{year}  = 1 Jul {year-1} to 31 Dec {year-1}   (first half of FY{year})
#   H1{year}  = 1 Jan {year} to 30 Jun {year}        (calendar half that coincides with FY end)
FISCAL_YEAR_END_MONTH = 6   # June
FISCAL_YEAR_END_DAY = 30

PERIOD_CODE_RE = re.compile(r"\b(HY|FY|H1|H2|Q[1-4])\s*(\d{4})\b", re.I)
DATE_TOKEN_RE = re.compile(
    r"(\d{1,2}[-/\s](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/\s]\d{4})"
    r"|(\d{1,2}[-/\s](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/\s]\d{2})"
    r"|(\d{1,2}[-/\s]\d{1,2}[-/\s]\d{4})"
    r"|(\d{1,2}[-/\s]\d{1,2}[-/\s]\d{2})"
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
    window = text[max(0, pos - 240):pos].lower()
    if 'six months' in window or '6 months' in window or 'half year' in window:
        return 'six months'
    if 'twelve months' in window or '12 months' in window or 'full year' in window or 'year ended' in window:
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

def _is_period_end(end: date) -> bool:
    """Heuristic: period-end dates are typically the last day of a month
    (day >= 28), while publication/admission dates can be any day."""
    return end.day >= 28


def period_duration_months(start_date: date, end_date: date) -> int:
    return (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1


def derive_label(start_date: date, end_date: date) -> str:
    """Derive a period label (HY2026, FY2025, ...) from start/end dates.

    Senus PLC fiscal year ends 30 June:
    * FY{year}   = 1 Jul {year-1} to 30 Jun {year}
    * HY{year}   = 1 Jul {year-1} to 31 Dec {year-1}   (H1 of FY{year})
    * H1{year}   = 1 Jan {year} to 30 Jun {year}        (calendar half aligned with FY end)

    If the dates don't match a standard pattern, fall back to a descriptive label.
    """
    # Twelve-month period ending 30 June -> FY{end.year}
    if end_date.month == FISCAL_YEAR_END_MONTH and end_date.day == FISCAL_YEAR_END_DAY:
        months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month) + 1
        if months >= 11:
            return f"FY{end_date.year}"
        # Short year ending 30 June -> still FY{end.year} but could be H1
        return f"H1{end_date.year}"

    # Six-month period ending 31 December -> HY{end.year + 1}
    if end_date.month == 12 and end_date.day == 31:
        months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month) + 1
        if months <= 7:
            return f"HY{end_date.year + 1}"
        # Twelve months ending 31 Dec doesn't match Senus FY; label by year
        return f"FY{end_date.year}"

    # Six-month period ending 30 June -> H1{end.year}
    if end_date.month == 6 and end_date.day == 30:
        months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month) + 1
        if months <= 7:
            return f"H1{end_date.year}"
        return f"FY{end_date.year}"

    # Fallback: generic label based on end year and approximate length
    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month) + 1
    if months <= 7:
        return f"HY{end_date.year}"
    return f"FY{end_date.year}"


@dataclass
class DetectedPeriod:
    """A period detected in a document, ready to be presented for confirmation."""
    label: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    column: Optional[int]       # Table column index (0 = leftmost/most recent)
    pages: List[int]            # Pages where this period was detected
    source: str                 # 'regex' or 'ambiguous'
    score: float
    is_primary: bool            # Most likely main period (first/most recent)
    duration_months: Optional[int] = None

STATEMENT_HEADING_RE = {
    "pnl": re.compile(r"profit and loss|income statement", re.I),
    "balance_sheet": re.compile(r"balance sheet|financial position", re.I),
    "cash_flow": re.compile(r"cash ?flows? (?:statement|from)|cash flow statement", re.I),
}


def statement_header_periods(
    pages: List[Tuple[int, str]],
) -> Tuple[Dict[str, List[Tuple[date, date]]], Dict[Tuple[date, date], List[int]]]:
    periods: Dict[str, List[Tuple[date, date]]] = {}
    source_pages: Dict[Tuple[date, date], List[int]] = {}
    active_statement: Optional[str] = None

    for page_number, text in pages:
        lines = text.splitlines()
        offsets: List[int] = []
        offset = 0
        for line in lines:
            offsets.append(offset)
            offset += len(line) + 1

        for index, line in enumerate(lines):
            for statement, heading_re in STATEMENT_HEADING_RE.items():
                if heading_re.search(line):
                    active_statement = statement
                    break
            if re.search(r"\bnotes to\b", line, re.I):
                active_statement = None
            if active_statement is None:
                continue

            matches = list(DATE_TOKEN_RE.finditer(line))
            if not matches:
                continue
            context_start = max(0, index - 5)
            context = "\n".join(lines[context_start:index + 1])
            if len(matches) < 2 and not re.search(r"\b(?:to|as at)\b", context, re.I):
                continue

            line_periods: List[Tuple[date, date]] = []
            for match in matches:
                end_date = _try_parse_date(match.group(0))
                if not end_date or not _is_period_end(end_date):
                    continue
                scope = _scope_for(text, offsets[index] + match.start())
                start_date = _infer_start(end_date, scope)
                if start_date:
                    line_periods.append((start_date, end_date))
            if not line_periods:
                continue

            statement_periods = periods.setdefault(active_statement, [])
            for period in line_periods:
                if period not in statement_periods:
                    statement_periods.append(period)
                pages_for_period = source_pages.setdefault(period, [])
                if page_number not in pages_for_period:
                    pages_for_period.append(page_number)

    return periods, source_pages


def _ambiguous_columns(pages: List[Tuple[int, str]]) -> List[DetectedPeriod]:
    active_statement: Optional[str] = None
    for page_number, text in pages:
        for line in text.splitlines():
            for statement, heading_re in STATEMENT_HEADING_RE.items():
                if heading_re.search(line):
                    active_statement = statement
                    break
            if re.search(r"\bnotes to\b", line, re.I):
                active_statement = None
            if active_statement is None:
                continue
            labels = list(re.finditer(r"\b(current|prior|previous|comparative)(?:\s+period)?\b", line, re.I))
            current_positions = [m.start() for m in labels if m.group(1).lower() == "current"]
            prior_positions = [m.start() for m in labels if m.group(1).lower() in {"prior", "previous", "comparative"}]
            if current_positions and prior_positions:
                ordered = sorted((pos, "current" if pos in current_positions else "comparative") for pos in current_positions + prior_positions)
                return [
                    DetectedPeriod(
                        label=None,
                        start_date=None,
                        end_date=None,
                        column=column,
                        pages=[page_number],
                        source="ambiguous",
                        score=0.0,
                        is_primary=column == 0,
                    )
                    for column, _ in enumerate(ordered)
                ]
    return []


def detect_all_periods(pages: List[Tuple[int, str]]) -> List[DetectedPeriod]:
    """Detect dated statement columns, or return unresolved columns for manual dates."""
    by_statement, source_pages = statement_header_periods(pages)
    if not by_statement:
        return _ambiguous_columns(pages) or [
            DetectedPeriod(None, None, None, 0, [], "ambiguous", 0.0, True)
        ]

    reference_statement = next(iter(by_statement))
    reference_periods = by_statement[reference_statement]
    detected = [
        DetectedPeriod(
            label=derive_label(start_date, end_date),
            start_date=start_date,
            end_date=end_date,
            column=column,
            pages=source_pages[(start_date, end_date)],
            source="regex",
            score=50.0,
            is_primary=column == 0,
            duration_months=period_duration_months(start_date, end_date),
        )
        for column, (start_date, end_date) in enumerate(reference_periods)
    ]
    return detected or _ambiguous_columns(pages)


def validate_detected_periods(
    periods: List[dict],
    statement_periods: Optional[Dict[str, List[Tuple[date, date]]]] = None,
) -> List[str]:
    """Validate column durations, comparative offsets and statement date headers."""
    errors: List[str] = []
    ordered = sorted(periods, key=lambda p: p.get("column", 0))
    if not ordered:
        return ["No reporting period was detected; enter its start and end dates."]

    dates: List[Tuple[int, date, date]] = []
    for period in ordered:
        column = period.get("column")
        start = period.get("start_date")
        end = period.get("end_date")
        if not start or not end:
            errors.append(f"Column {column} has ambiguous dates; enter the start and end dates.")
            continue
        if isinstance(start, str):
            start = date.fromisoformat(start)
        if isinstance(end, str):
            end = date.fromisoformat(end)
        if end < start:
            errors.append(f"Column {column} ends before it starts.")
            continue
        dates.append((column, start, end))

    if len(dates) == len(ordered):
        unique = {(start, end) for _, start, end in dates}
        if len(unique) != len(dates):
            errors.append("Each column must represent a different reporting period.")
        durations = {period_duration_months(start, end) for _, start, end in dates}
        if len(durations) > 1:
            errors.append("Comparable columns have different period durations.")
        if len(dates) == 2:
            current = next((item for item in dates if item[0] == 0), dates[0])
            comparative = next((item for item in dates if item[0] != current[0]), dates[1])
            expected_start = current[1] - relativedelta(years=1)
            expected_end = current[2] - relativedelta(years=1)
            if comparative[1] != expected_start or comparative[2] != expected_end:
                errors.append("The comparative period must be exactly 12 months before the current period.")

    if statement_periods:
        signatures = {
            statement: tuple(values)
            for statement, values in statement_periods.items()
            if values
        }
        if signatures and any(value != next(iter(signatures.values())) for value in signatures.values()):
            errors.append("P&L, balance sheet and cash flow period headers do not match.")
    return errors


def detect_periods(pages: List[Tuple[int, str]]) -> List[PeriodCandidate]:
    """Find explicit period-end dates as deterministic candidates."""
    candidates: List[PeriodCandidate] = []
    for pnum, text in pages:
        if not text:
            continue
        tl = text.lower()
        ctx = text[:200]
        for m in DATE_TOKEN_RE.finditer(text):
            token = m.group(0)
            end_d = _try_parse_date(token)
            if not end_d or not _is_period_end(end_d):
                continue
            window_before = text[max(0, m.start() - 240):m.start()].lower()
            if 'ending' in window_before and 'ended' not in window_before:
                continue
            scope = _scope_for(text, m.start())
            start_d = _infer_start(end_d, scope)
            label = derive_label(start_d, end_d) if start_d else None
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

    detected_dates = [(c.label, c.start_date, c.end_date) for c in cands if c.end_date]

    date_matched = False
    for label, start_d, end_d in detected_dates:
        if doc_start and doc_end and start_d == doc_start and end_d == doc_end:
            date_matched = True
            break

    if date_matched:
        return True, f"Period ({doc_start} to {doc_end}) found in document.", cands

    details = []
    for label, start_d, end_d in detected_dates[:5]:
        details.append(f"{label or 'unknown'}: {start_d} to {end_d}")
    details_str = "; ".join(details) if details else "no dates detected"

    return False, f"Dates not found in document. Detected periods: {details_str}", cands

def _detect_table_column_headers(pages: List[Tuple[int, str]]) -> List[Tuple[date, date]]:
    statement_periods, _ = statement_header_periods(pages)
    return next(iter(statement_periods.values()), [])


def period_to_column_map(pages: List[Tuple[int, str]]) -> Dict[Tuple[date, date], int]:
    """Map each detected (start_date, end_date) to a table column number.

    Uses dated statement header rows in printed column order. It does not infer
    columns from dates in the document body.
    """
    header_periods = _detect_table_column_headers(pages)
    return {key: idx for idx, key in enumerate(header_periods)}


def get_column_for_period(doc_start: Optional[date], doc_end: Optional[date], pages: List[Tuple[int, str]]) -> Optional[int]:
    """Return the table column number for the given period, or None if not found."""
    if not doc_start or not doc_end:
        return None
    mapping = period_to_column_map(pages)
    return mapping.get((doc_start, doc_end))


async def validate_against(doc_label: Optional[str], doc_start, doc_end, pages: List[Tuple[int, str]], min_conf: float = 70.0):
    """Validate dates against explicit statement headers, without guessing."""
    ok, msg, cands = find_matching_period(doc_label, doc_start, doc_end, pages)
    return ok, msg, cands
