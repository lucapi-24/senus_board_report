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

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.llm_extract import STATEMENTS, StatementLine, parse_value, value_candidates
from app.services.deterministic import normalize_line_sign
from app.services.validation import check_results

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
    confidence_factors: Dict[str, Optional[bool]] = field(default_factory=dict)

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
            "confidence_factors": self.confidence_factors,
        }


_IDENTITY_RULES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "pnl": {
        "Revenue": ("revenue_minus_cogs_eq_gross_profit",),
        "Turnover": ("revenue_minus_cogs_eq_gross_profit",),
        "Cost of sales": ("revenue_minus_cogs_eq_gross_profit",),
        "Gross Profit": ("revenue_minus_cogs_eq_gross_profit",),
        "Group operating loss": ("operating_result_plus_depreciation_eq_ebitda",),
        "Operating loss": ("operating_result_plus_depreciation_eq_ebitda",),
    },
    "balance_sheet": {
        "Goodwill": ("subtotal:Total Fixed Assets",),
        "Development Costs": ("subtotal:Total Fixed Assets",),
        "Tangible Assets": ("subtotal:Total Fixed Assets",),
        "Total Fixed Assets": (
            "subtotal:Total Fixed Assets",
            "subtotal:Total Assets Less Current Liabilities",
            "assets_minus_liabilities_eq_equity",
        ),
        "Debtors": ("subtotal:Total Current Assets",),
        "Cash and cash equivalents": ("subtotal:Total Current Assets",),
        "Total Current Assets": (
            "subtotal:Total Current Assets",
            "subtotal:Net Current Assets",
            "assets_minus_liabilities_eq_equity",
        ),
        "Creditors < 1yr": (
            "subtotal:Net Current Assets",
            "assets_minus_liabilities_eq_equity",
        ),
        "Contingent consideration": (
            "subtotal:Net Current Assets",
            "assets_minus_liabilities_eq_equity",
        ),
        "Net Current Assets": (
            "subtotal:Net Current Assets",
            "subtotal:Total Assets Less Current Liabilities",
            "assets_minus_liabilities_eq_equity",
        ),
        "Total Assets Less Current Liabilities": (
            "subtotal:Total Assets Less Current Liabilities",
            "subtotal:Net Assets",
            "assets_minus_liabilities_eq_equity",
        ),
        "Creditors > 1yr": ("subtotal:Net Assets", "assets_minus_liabilities_eq_equity"),
        "Net Assets": ("subtotal:Net Assets", "assets_minus_liabilities_eq_equity"),
        "Equity": ("assets_minus_liabilities_eq_equity",),
    },
    "cash_flow": {
        "Depreciation": ("operating_result_plus_depreciation_eq_ebitda",),
        "EBITDA": ("operating_result_plus_depreciation_eq_ebitda",),
        "Cash at start": ("cash_open_plus_net_change_eq_cash_close",),
        "Net change in cash": ("cash_open_plus_net_change_eq_cash_close",),
        "Cash at end": ("cash_open_plus_net_change_eq_cash_close",),
    },
}


def _regex_map(statement: str, deterministic: Dict[str, Any]) -> Dict[str, tuple]:
    out: Dict[str, tuple] = {}
    provenance = (deterministic.get("provenance") or {}).get(statement, {})
    for label, raw in (deterministic.get(statement, {}) or {}).items():
        canon = canonical_label(statement, label)
        page = provenance.get(label) or provenance.get(canon)
        out[_key(canon)] = (canon, parse_value(raw), page)
    return out


def _llm_map(statement: str, llm_lines: Sequence[StatementLine]) -> Dict[str, tuple]:
    out: Dict[str, tuple] = {}
    for line in llm_lines:
        if line.statement != statement:
            continue
        canon = canonical_label(statement, line.line_item)
        raw_value = line.value
        line.value = float(normalize_line_sign(statement, canon, line.value))
        if line.value != raw_value:
            line.meta["raw_value"] = raw_value
            line.meta["sign_normalized"] = True
        out[_key(canon)] = (canon, line)
    return out


def _reconcile_item(
    statement: str, canon: str, regex: tuple | None, llm: tuple | None, tolerance: float
) -> ReconciledItem:
    llm_page = (llm[1].page or None) if llm else None
    page = llm_page or (regex[2] if regex else None)
    rv = regex[1] if regex else None
    lv = llm[1].value if llm else None

    if regex and llm:
        if rv is not None and lv is not None and abs(rv - lv) <= tolerance:
            return ReconciledItem(statement, canon, rv, "matched", 0.0, page, "both", lv, rv)
        if rv is not None and lv is not None and abs(abs(rv) - abs(lv)) <= tolerance:
            return ReconciledItem(statement, canon, rv, "sign_mismatch", 0.0, page, "both", lv, rv)
        return ReconciledItem(statement, canon, rv if rv is not None else lv,
                              "value_mismatch", 0.0, page, "both", lv, rv)
    if regex:
        return ReconciledItem(statement, canon, rv, "regex_only", 0.0, page, "regex", None, rv)
    return ReconciledItem(statement, canon, lv, "llm_only", 0.0, page, "llm", lv, None)


def _apply_confidence(
    item: ReconciledItem,
    regex: Optional[tuple],
    llm: Optional[tuple],
    page_text: Dict[int, str],
    identities: Dict[str, bool],
) -> None:
    regex_value = regex[1] if regex else None
    llm_value = llm[1].value if llm else None
    agreement = regex_value is not None and llm_value is not None and regex_value == llm_value
    page = page_text.get(item.page, "") if item.page is not None else ""
    page_match = bool(page) and any(
        re.search(
            rf"(?<![\d,]){re.escape(candidate)}(?:\.\d+)?(?![\d,])",
            page,
        )
        for candidate in value_candidates(item.value)
    )
    rules = _IDENTITY_RULES.get(item.statement, {}).get(item.line_item, ())
    relevant_results = [identities[rule] for rule in rules if rule in identities]
    identity_consistent = all(relevant_results) if relevant_results else None
    item.confidence_factors = {
        "extractors_agree": agreement,
        "value_found_on_source_page": page_match,
        "accounting_identities_consistent": identity_consistent,
    }
    passed = [value for value in item.confidence_factors.values() if value is not None]
    item.confidence = sum(passed) / len(passed) if passed else 0.0


def reconcile(
    deterministic: Dict[str, Any],
    llm_lines: Sequence[StatementLine],
    tolerance: float = TOLERANCE,
    pages: Sequence[Tuple[int, str]] = (),
) -> Dict[str, Any]:
    """Compare both extractors and score each figure from verifiable evidence.

    Confidence is the equal-weight mean of extractor agreement, source-page
    text matching, and any applicable accounting identity checks.
    """
    result: Dict[str, Any] = {}
    counts = {"matched": 0, "flagged": 0, "total": 0}

    for statement in STATEMENTS:
        regex = _regex_map(statement, deterministic)
        llm = _llm_map(statement, llm_lines)
        order = list(regex) + [k for k in llm if k not in regex]
        items: List[ReconciledItem] = []
        for key in order:
            item = _reconcile_item(
                statement,
                (regex.get(key) or llm[key])[0],
                regex.get(key),
                llm.get(key),
                tolerance,
            )
            items.append(item)
            counts["total"] += 1
            if item.status == "matched":
                counts["matched"] += 1
            elif item.status in ("sign_mismatch", "value_mismatch", "llm_only", "regex_only"):
                counts["flagged"] += 1
        result[statement] = items

    chosen = {
        statement: {item.line_item: item.value for item in result[statement]}
        for statement in STATEMENTS
    }
    identities = check_results(
        chosen["pnl"], chosen["balance_sheet"], chosen["cash_flow"]
    )
    page_text = {page: text for page, text in pages}
    for statement in STATEMENTS:
        regex = _regex_map(statement, deterministic)
        llm = _llm_map(statement, llm_lines)
        for item in result[statement]:
            key = _key(item.line_item)
            _apply_confidence(item, regex.get(key), llm.get(key), page_text, identities)

    result["summary"] = counts
    return result


def review_status_for(status: str) -> str:
    """Map a reconciliation status to a persisted ``review_status`` value."""
    if status == "llm_only":
        return "llm_only"
    return "reported" if status == "matched" else "flagged"
