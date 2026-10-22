from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

import dateutil.parser as dparser

from app.services.llm_extract import build_client, annotate_pages

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

PERIOD_LLM_PROMPT = """You are reviewing a financial document. Extract the reporting period information.

Look for:
- Period label (e.g. "HY2026", "FY2025", "H1 2026")
- Start date of the reporting period
- End date of the reporting period

Focus on the MAIN reporting period, NOT comparative periods, publication dates, or admission dates.

Return ONLY a JSON object:
{
  "period_label": "HY2026 or null",
  "start_date": "YYYY-MM-DD or null",
  "end_date": "YYYY-MM-DD or null",
  "confidence": 0.0 to 1.0
}

Document:
---
{document}
---"""

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

def _is_period_end(end: date) -> bool:
    """Heuristic: period-end dates are typically the last day of a month
    (day >= 28), while publication/admission dates can be any day."""
    return end.day >= 28


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
    label: str
    start_date: date
    end_date: date
    column: Optional[int]       # Table column index (0 = leftmost/most recent)
    pages: List[int]            # Pages where this period was detected
    source: str                 # 'regex' or 'llm'
    score: float
    is_primary: bool            # Most likely main period (first/most recent)


def detect_all_periods(pages: List[Tuple[int, str]]) -> List[DetectedPeriod]:
    """Detect all reporting periods present in a document.

    Scans table header rows (lines with multiple dates side-by-side) to find
    each column's period, then derives a label for each.  Returns periods
    sorted left-to-right (column 0 = most recent).

    If no table headers are found, falls back to individual date detection.
    Periods with ambiguous dates (e.g. "Current period") are excluded — the
    caller should mark the document as pending manual confirmation.
    """
    mapping = period_to_column_map(pages)

    detected: List[DetectedPeriod] = []
    seen_keys: set = set()

    for (start_d, end_d), column in sorted(mapping.items(), key=lambda x: x[1]):
        key = (start_d, end_d)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        label = derive_label(start_d, end_d)

        # Find pages where this period's end date appears
        pages_found: List[int] = []
        for pnum, text in pages:
            if not text:
                continue
            tl = text.lower()
            end_str = end_d.strftime("%d-%b-%Y").replace(" ", "-")
            # Try several date formats
            patterns = [
                end_d.strftime("%d %B %Y"),
                end_d.strftime("%d-%b-%y"),
                end_d.strftime("%d-%b-%Y"),
                end_d.strftime("%d %b %Y"),
                f"{end_d.day}-{end_d.strftime('%b')}-{end_d.year}",
            ]
            if any(p.lower() in tl for p in patterns):
                pages_found.append(pnum)

        detected.append(DetectedPeriod(
            label=label,
            start_date=start_d,
            end_date=end_d,
            column=column,
            pages=pages_found,
            source='regex',
            score=50.0,
            is_primary=(column == 0),
        ))

    # If no table headers found, fall back to individual date candidates
    if not detected:
        cands = detect_periods(pages)
        for c in cands:
            if c.start_date and c.end_date and c.label:
                key = (c.start_date, c.end_date)
                if key not in seen_keys:
                    seen_keys.add(key)
                    detected.append(DetectedPeriod(
                        label=c.label or derive_label(c.start_date, c.end_date),
                        start_date=c.start_date,
                        end_date=c.end_date,
                        column=None,
                        pages=[c.page] if c.page else [],
                        source=c.source,
                        score=c.score,
                        is_primary=False,
                    ))

    return detected
    """Detect all period candidates in the document using regex/dateutil.

    Returns every date found with its inferred label/score.
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
            # Exclude future-looking periods ("year ending X") - only accept
            # periods that have already ended ("year ended X", "six months ended X")
            window_before = t[max(0, m.start() - 80):m.start()].lower()
            if 'ending' in window_before and 'ended' not in window_before:
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

async def detect_period_with_llm(pages: List[Tuple[int, str]]) -> Optional[PeriodCandidate]:
    """Ask the LLM to identify the reporting period from the document.

    Used as a fallback when regex detection is uncertain.
    """
    client = build_client()
    if client is None:
        return None

    import asyncio, json

    document = annotate_pages(pages)
    prompt = PERIOD_LLM_PROMPT.replace("{document}", document)

    response = None
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            response = await client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
            break
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(1.5 * (attempt + 1))

    if response is None:
        return None

    try:
        data = json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, AttributeError):
        return None

    label = data.get("period_label")
    start_s = data.get("start_date")
    end_s = data.get("end_date")
    confidence = float(data.get("confidence", 0.5))

    start_d = None
    end_d = None
    if start_s:
        try:
            start_d = date.fromisoformat(start_s)
        except (ValueError, TypeError):
            start_d = _try_parse_date(str(start_s))
    if end_s:
        try:
            end_d = date.fromisoformat(end_s)
        except (ValueError, TypeError):
            end_d = _try_parse_date(str(end_s))

    if not end_d:
        return None

    return PeriodCandidate(
        label=label,
        start_date=start_d,
        end_date=end_d,
        score=confidence * 100.0,
        page=0,
        source='llm',
        context='',
    )

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
    """Detect table column header dates by finding lines that contain multiple
    dates side by side (typical of financial statement column headers).

    Returns a list of (start_date, end_date) tuples sorted left-to-right
    (column 0 = leftmost = most recent).
    """
    header_periods: List[Tuple[date, date]] = []
    seen = set()

    for pnum, text in pages:
        if not text:
            continue
        # Find all dates on each line
        for line in text.split("\n"):
            line_dates: List[date] = []
            for m in DATE_TOKEN_RE.finditer(line):
                d = _try_parse_date(m.group(0))
                if d and _is_period_end(d):
                    line_dates.append(d)
            # A table header line has 2+ dates side by side
            if len(line_dates) >= 2:
                # Sort descending (leftmost = most recent)
                line_dates.sort(reverse=True)
                for d in line_dates:
                    start_d = _infer_start(d, "six months")
                    if start_d:
                        key = (start_d, d)
                        if key not in seen:
                            seen.add(key)
                            header_periods.append(key)

    return header_periods


def period_to_column_map(pages: List[Tuple[int, str]]) -> Dict[Tuple[date, date], int]:
    """Map each detected (start_date, end_date) to a table column number.

    Uses table header rows (lines with multiple dates side by side) to
    identify which period corresponds to which column. Falls back to
    sorting all labeled period-end dates by end_date descending if no
    table headers are found.

    Publication dates (day < 28) are excluded.
    """
    # First try table header detection
    header_periods = _detect_table_column_headers(pages)
    if header_periods:
        return {key: idx for idx, key in enumerate(header_periods)}

    # Fallback: sort all labeled period-end dates by end_date descending
    cands = detect_periods(pages)
    dated = [(c.start_date, c.end_date) for c in cands
             if c.start_date and c.end_date and c.label and _is_period_end(c.end_date)]
    seen = set()
    unique = []
    for sd, ed in dated:
        key = (sd, ed)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    unique.sort(key=lambda x: x[1], reverse=True)
    return {key: idx for idx, key in enumerate(unique)}


def get_column_for_period(doc_start: Optional[date], doc_end: Optional[date], pages: List[Tuple[int, str]]) -> Optional[int]:
    """Return the table column number for the given period, or None if not found."""
    if not doc_start or not doc_end:
        return None
    mapping = period_to_column_map(pages)
    return mapping.get((doc_start, doc_end))


async def validate_against(doc_label: Optional[str], doc_start, doc_end, pages: List[Tuple[int, str]], min_conf: float = 70.0):
    """Validate user-submitted dates against periods found in the document.

    The label is intentionally NOT compared -- it's just a user-chosen name.
    Only start_date/end_date must exist in the document.

    Strategy:
    1. Try regex/dateutil detection first (fast, free)
    2. If regex finds a confident match (score >= min_conf), use it
    3. If regex is uncertain, fall back to LLM detection

    Returns (ok, message, candidates).
    """
    # Step 1: regex detection
    ok, msg, cands = find_matching_period(doc_label, doc_start, doc_end, pages)
    if ok:
        return ok, msg, cands

    # Step 2: check if we have any confident regex candidates
    primary = max(cands, key=lambda x: x.score) if cands else None
    if primary and primary.score >= min_conf:
        # Regex found something confident but it didn't match user's dates
        detected = f"{primary.label or 'unknown'} {primary.start_date}-{primary.end_date} (score {primary.score:.1f})"
        return False, f"Dates not found in document. Detected: {detected}", cands

    # Step 3: fallback to LLM
    llm_cand = await detect_period_with_llm(pages)
    if llm_cand and llm_cand.end_date:
        if doc_start and doc_end and llm_cand.start_date == doc_start and llm_cand.end_date == doc_end:
            return True, f"Period ({doc_start} to {doc_end}) found in document (LLM).", cands
        detected = f"{llm_cand.label or 'unknown'} {llm_cand.start_date}-{llm_cand.end_date} (score {llm_cand.score:.1f})"
        return False, f"Dates not found in document. LLM detected: {detected}", cands

    # Step 4: nothing found at all
    return False, "Dates not found in document. Could not detect any period from the document content.", cands
