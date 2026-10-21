from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple

import dateutil.parser as dparser

from app.services.llm_extract import build_client, annotate_pages

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

def detect_periods(pages: List[Tuple[int, str]]) -> List[PeriodCandidate]:
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

def period_to_column_map(pages: List[Tuple[int, str]]) -> Dict[Tuple[date, date], int]:
    """Map each detected (start_date, end_date) to a table column number.

    Columns are assigned left-to-right by end_date descending: the most recent
    period gets column 0, the next most recent gets column 1, etc. This matches
    the typical financial-statement layout where the current period is the
    leftmost column and comparatives follow to the right.
    """
    cands = detect_periods(pages)
    dated = [(c.start_date, c.end_date) for c in cands if c.start_date and c.end_date]
    # Deduplicate
    seen = set()
    unique = []
    for sd, ed in dated:
        key = (sd, ed)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    # Sort by end_date descending (most recent first = column 0)
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
