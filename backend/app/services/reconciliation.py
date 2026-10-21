"""Reconciliation of the two independent extractors.

The deterministic regex parser and the LLM extractor (``llm_extract``) read the
same document but neither corrects the other. This module compares them figure
by figure and produces, per line item:

* a status — matched, sign_mismatch, value_mismatch, llm_only, regex_only;
* a confidence score derived from the agreement;
* both raw values whenever they disagree, so a human can adjudicate.

Only disagreements and single-source labels are marked for review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.services.llm_extract import STATEMENTS, StatementLine, parse_value

TOLERANCE = 1.0

# Canonical display labels (as stored by the deterministic parser).
CANONICAL: Dict[str, List[str]] = {
    "pnl": [
        "Revenue",
        "Cost of sales",
        "Gross Profit",
        "Distribution costs",
        "Administrative expenses",
        "Other operating income",
        "Group operating loss",
        "Other gains and losses",
        "Interest payable",
        "Loss before taxation",
        "Tax expense",
        "Loss for the period",
    ],
    "balance_sheet": [
        "Goodwill",
        "Development Costs",
        "Tangible Assets",
        "Total Fixed Assets",
        "Debtors",
        "Cash and cash equivalents",
        "Total Current Assets",
        "Creditors < 1yr",
        "Contingent consideration",
        "Net Current Assets",
        "Total Assets Less Current Liabilities",
        "Creditors > 1yr",
        "Net Assets",
        "Share Capital",
        "Share Premium",
        "Retained Earnings",
        "Equity",
    ],
    "cash_flow": [
        "Loss for the period",
        "Interest payable",
        "Depreciation",
        "Working capital movements",
        "Cash used in operations",
        "Interest paid",
        "Net cash from operations",
        "Capex",
        "Asset disposals",
        "Net cash from investing",
        "Loan repayments",
        "Equity issuance",
        "Net cash from financing",
        "Net change in cash",
        "Cash at start",
        "Cash at end",
    ],
}

# Alternate spellings (mostly the LLM's) mapped to the canonical label above.
ALIASES: Dict[str, Dict[str, str]] = {
    "pnl": {
        "turnover": "Revenue",
        "gross profit": "Gross Profit",
        "cost of sales": "Cost of sales",
        "distribution costs": "Distribution costs",
        "administrative expenses": "Administrative expenses",
        "admin expenses": "Administrative expenses",
        "other operating income": "Other operating income",
        "operating loss": "Group operating loss",
        "other gains and losses": "Other gains and losses",
        "interest payable and similar expenses": "Interest payable",
        "loss before taxation": "Loss before taxation",
        "taxation": "Tax expense",
        "loss for the 6 month period": "Loss for the period",
        "loss for the half year period": "Loss for the period",
    },
    "balance_sheet": {
        "intangible assets": "Development Costs",
        "creditors amounts falling due within one year": "Creditors < 1yr",
        "creditors amounts falling due after more than one year": "Creditors > 1yr",
        "net liabilities assets": "Net Assets",
        "called up share capital presented as equity": "Share Capital",
        "equity attributable to owners of the company": "Equity",
    },
    "cash_flow": {
        "loss for the 6 month period": "Loss for the period",
        "interest payable and similar expenses": "Interest payable",
        "movements in working capital": "Working capital movements",
        "net cash used in operating activities": "Net cash from operations",
        "payments to acquire intangible assets": "Capex",
        "payments to acquire in tangible assets": "Capex",
        "receipts from sales of tangible assets": "Asset disposals",
        "net cash used in investment activities": "Net cash from investing",
        "receipt repayment of loans": "Loan repayments",
        "issue of new shares": "Equity issuance",
        "net cash inflow from financing activities": "Net cash from financing",
        "net decrease increase in cash and cash equivalents": "Net change in cash",
        "cash and cash equivalent at beginning of period": "Cash at start",
        "cash and cash equivalent at end of period": "Cash at end",
    },
}


def _key(label: str) -> str:
    text = label.lower().replace("<", "less").replace(">", "greater")
    return "".join(ch for ch in text if ch.isalnum())


def _lookup() -> Dict[str, Dict[str, str]]:
    table: Dict[str, Dict[str, str]] = {}
    for statement, labels in CANONICAL.items():
        mapping = {_key(label): label for label in labels}
        for alias, target in ALIASES.get(statement, {}).items():
            mapping[_key(alias)] = target
        table[statement] = mapping
    return table


_LOOKUPS = _lookup()


def canonical_label(statement: str, label: str) -> str:
    return _LOOKUPS.get(statement, {}).get(_key(label), label)


@dataclass
class ReconciledItem:
    statement: str
    line_item: str
    value: float
    status: str
    confidence: float
    page: Optional[int] = None
    source: str = "both"  # both | llm | regex
    llm_value: Optional[float] = None
    regex_value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statement": self.statement,
            "line_item": self.line_item,
            "value": self.value,
            "status": self.status,
            "confidence": self.confidence,
            "page": self.page,
            "source": self.source,
            "llm_value": self.llm_value,
            "regex_value": self.regex_value,
        }


def _regex_map(statement: str, deterministic: Dict[str, Any]) -> Dict[str, tuple]:
    out: Dict[str, tuple] = {}
    for label, raw in (deterministic.get(statement, {}) or {}).items():
        canon = canonical_label(statement, label)
        out[_key(canon)] = (canon, parse_value(raw))
    return out


def _llm_map(statement: str, llm_lines: Sequence[StatementLine]) -> Dict[str, tuple]:
    out: Dict[str, tuple] = {}
    for line in llm_lines:
        if line.statement != statement:
            continue
        canon = canonical_label(statement, line.line_item)
        out[_key(canon)] = (canon, line)
    return out


def _reconcile_item(
    statement: str, canon: str, regex: tuple | None, llm: tuple | None, tolerance: float
) -> ReconciledItem:
    page = llm[1].page if llm else None
    rv = regex[1] if regex else None
    lv = llm[1].value if llm else None

    if regex and llm:
        if rv is not None and lv is not None and abs(rv - lv) <= tolerance:
            return ReconciledItem(statement, canon, rv, "matched", 0.98, page, "both", lv, rv)
        if rv is not None and lv is not None and abs(abs(rv) - abs(lv)) <= tolerance:
            return ReconciledItem(statement, canon, rv, "sign_mismatch", 0.60, page, "both", lv, rv)
        return ReconciledItem(statement, canon, rv if rv is not None else lv,
                              "value_mismatch", 0.20, page, "both", lv, rv)
    if regex:
        return ReconciledItem(statement, canon, rv, "regex_only", 0.50, page, "regex", None, rv)
    return ReconciledItem(statement, canon, lv, "llm_only", 0.50, page, "llm", lv, None)


def reconcile(
    deterministic: Dict[str, Any],
    llm_lines: Sequence[StatementLine],
    tolerance: float = TOLERANCE,
) -> Dict[str, Any]:
    """Compare the deterministic and LLM extractions, statement by statement."""
    result: Dict[str, Any] = {}
    counts = {"matched": 0, "flagged": 0, "total": 0}

    for statement in STATEMENTS:
        regex = _regex_map(statement, deterministic)
        llm = _llm_map(statement, llm_lines)
        order = list(regex) + [k for k in llm if k not in regex]
        items: List[ReconciledItem] = []
        for key in order:
            item = _reconcile_item(statement, (regex.get(key) or llm[key])[0],
                                   regex.get(key), llm.get(key), tolerance)
            items.append(item)
            counts["total"] += 1
            if item.status == "matched":
                counts["matched"] += 1
            elif item.status in ("sign_mismatch", "value_mismatch", "llm_only", "regex_only"):
                counts["flagged"] += 1
        result[statement] = items

    result["summary"] = counts
    return result


def review_status_for(status: str) -> str:
    """Map a reconciliation status to a persisted ``review_status`` value."""
    return "reported" if status == "matched" else "flagged"
