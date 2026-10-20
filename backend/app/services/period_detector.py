from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

PERIOD_CODE_RE = re.compile(r"\b(HY|FY|H1|H2|Q[1-4])\s*(\d{4})\b", re.I)
END_RE = re.compile(
    r"(?:for the|period ended|half year ended|year ended)?\s*"
    r"(?:six months|6 months|year|12 months|twelve months|quarter|3 months)?\s*"
    r"(?:ended\s+)?([0-9]{1,2})\s+([A-Za-z]{3,9})\s+([0-9]{4})",
    re.I,
)
END_SCOPE_RE = re.compile(r"(six months|6 months|year|12 months|twelve months|quarter|3 months)", re.I)
AS_AT_RE = re.compile(r"as at\s+([0-9]{1,2})\s+([A-Za-z]{3,9})\s+([0-9]{4})", re.I)
COMPARATIVE_WORDS = ("comparative", "previous", "prior period", "prior year", "restated comparative")

MONTH_MAP = {
    'jan':1,'january':1,'feb':2,'february':2,'mar':3,'march':3,'apr':4,'april':4,'may':5,'jun':6,'june':6,
    'jul':7,'july':7,'aug':8,'august':8,'sep':9,'september':9,'oct':10,'october':10,'nov':11,'november':11,'dec':12,'december':12
}

@dataclass
class PeriodCandidate:
    label: Optional[str]
    start_date: Optional[date]
    end_date: Optional[date]
    score: float
    page: int
    source: str
    context: str

def _parse_date(day: str, mon: str, yr: str) -> date:
    m = MONTH_MAP.get(mon.lower()[:3], MONTH_MAP.get(mon.lower(), 0))
    return date(int(yr), m, int(day))

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
    return None

def _scope_for(text: str, pos: int) -> str:
    window = text[max(0, pos - 60):pos].lower()
    if 'six months' in window or '6 months' in window or 'half year' in window:
        return 'six months'
    if 'twelve months' in window or '12 months' in window or 'year' in window:
        return 'year'
    if 'quarter' in window or '3 months' in window:
        return 'quarter'
    return 'six months'


def detect_periods(pages: List[Tuple[int, str]]) -> List[PeriodCandidate]:
    candidates: List[PeriodCandidate] = []
    for pnum, text in pages:
        if not text:
            continue
        t = text
        tl = t.lower()
        ctx = t[:200]
        is_comp = any(w in tl for w in COMPARATIVE_WORDS)
        for m in END_RE.finditer(t):
            day, mon, yr = m.group(1), m.group(2), m.group(3)
            try:
                end_d = _parse_date(day, mon, yr)
            except Exception:
                continue
            scope = _scope_for(t, m.start())
            start_d = _infer_start(end_d, scope)
            label = None
            sl = scope.lower()
            is_six = 'six months' in sl or '6 months' in sl or 'half' in sl
            if is_six:
                if end_d.month == 12:
                    label = f"HY{end_d.year + 1}"
                elif end_d.month == 6:
                    label = f"H1{end_d.year}"
                else:
                    label = f"HY{end_d.year + 1}" if end_d.month == 12 else f"HY{end_d.year}"
            is_year = 'year' in sl or 'twelve' in sl or '12 months' in sl
            if is_year:
                if end_d.month == 6:
                    label = f"FY{end_d.year}"
            score = 60.0
            if 'statement' in tl or 'income' in tl or 'balance' in tl or 'cash' in tl or 'consolidated' in tl:
                score += 30
            if 'condensed' in tl or 'interim' in tl:
                score += 15
            if is_comp:
                score -= 80
            if score < 0:
                score = 1
            candidates.append(PeriodCandidate(label, start_d, end_d, score, pnum, 'end_statement', ctx))
        for m in PERIOD_CODE_RE.finditer(t):
            kind, yr = m.group(1), m.group(2)
            label_c = f"{kind.upper()}{yr}"
            sc = 30.0 if is_comp else 45.0
            if any(c.label == label_c for c in candidates):
                continue
            candidates.append(PeriodCandidate(label_c, None, None, sc, pnum, 'code', ctx))
    dedup: List[PeriodCandidate] = []
    seen = set()
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        key = c.label if c.label else (str(c.end_date) if c.end_date else str(id(c)))
        if key not in seen:
            seen.add(key)
            dedup.append(c)
    return dedup

def pick_primary(cands: List[PeriodCandidate], min_score: float = 50.0) -> Optional[PeriodCandidate]:
    if not cands:
        return None
    primary = max(cands, key=lambda x: x.score)
    if primary.score < min_score:
        filtered = [c for c in cands if c.score >= 30]
        if not filtered:
            return primary
        primary = max(filtered, key=lambda x: x.score)
    return primary

def validate_against(doc_label: Optional[str], doc_start, doc_end, pages: List[Tuple[int, str]], min_conf: float = 70.0):
    cands = detect_periods(pages)
    primary = pick_primary(cands)
    if primary is None:
        return True, 'No period detected in document', cands
    issues = []
    if doc_label and primary.label:
        if primary.label.upper() != doc_label.upper():
            issues.append(f"Label mismatch: submitted '{doc_label}' vs detected '{primary.label}'")
    if doc_start and primary.start_date and doc_start != primary.start_date:
        issues.append(f"Start date mismatch: {doc_start} vs {primary.start_date}")
    if doc_end and primary.end_date and doc_end != primary.end_date:
        issues.append(f"End date mismatch: {doc_end} vs {primary.end_date}")
    if not issues:
        return True, f"Matched {primary.label or 'period'} (score {primary.score:.1f})", cands
    if primary.score < min_conf:
        return True, 'Low confidence detection; proceeding', cands
    return False, 'Dates not found in document; please check the period label and dates against the document content.', cands
