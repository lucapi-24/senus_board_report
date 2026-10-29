"""Narrative extraction from the real HY2026 press release, checked against the reference TXT."""

import difflib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from app.services.ingest import read_pages  # noqa: E402
from app.services.narrative import _norm, extract_narrative  # noqa: E402

PDF = ROOT / "Senus_HalfYearResultsDec2025_PR_V19032026-FINAL-clean.pdf"
TXT = ROOT / "Otros" / "Senus_HalfYearResultsDec2025.txt"

pytestmark = pytest.mark.skipif(not PDF.exists(), reason="source PDF not available")


@pytest.fixture(scope="module")
def narrative():
    return extract_narrative(read_pages(str(PDF)))


def _value(group, key):
    return group[key]["value"]


def test_company_profile(narrative):
    company = narrative["company"]
    assert _value(company, "registration_number") == "605800"
    assert _value(company, "isin") == "IE000O0F49R3"
    assert _value(company, "ticker") == "SENUS"
    assert _value(company, "issued_share_capital") == 2561332
    assert _value(company, "admission_date") == "22 December 2025"
    assert _value(company, "sponsor") == "Assiduous"
    assert _value(company, "fiscal_year_end") == "30 June"
    assert _value(company, "fy25_customer_accounts") == 138
    assert _value(company, "registered_office").startswith("Block D, Daneswell Business Centre")


def test_people_and_roles(narrative):
    roles = {p["name"]: p["roles"] for p in narrative["people"]}
    assert "Chairman" in roles["Gerard Keenan"]
    assert "Managing Director" in roles["Brendan Allen"]
    assert "General Manager" in roles["Stephen Coen"]
    assert roles["Hugh Sturrock"] == ["Head of Research"]
    assert roles["Jonathan Smith"] == ["Head of Software Engineering"]
    assert {"Eoghan Finneran", "Joe Desbonnet"} <= set(roles)


def test_commercial_aggregates_and_customers(narrative):
    agg = narrative["commercial"]["aggregates"]
    assert _value(agg, "closed_pipeline_eur") == 700_000
    assert _value(agg, "enterprise_customers_closed") == 21
    assert _value(agg, "open_pipeline_eur") == 500_000
    assert _value(agg, "recent_deals_count") == 10
    assert _value(agg, "recent_deals_value_eur") == 425_000
    customers = {c["name"]: c for c in narrative["commercial"]["customers"]}
    assert {"First Milk", "Bank of Ireland", "Arla", "Scottish Habitat", "Tirlan",
            "Lakeland Dairies", "North Cork Creameries", "University of Limerick"} <= set(customers)
    assert customers["First Milk"]["status"] == "secured"
    assert customers["Bank of Ireland"]["status"] == "expanded"
    assert customers["Department of Agriculture, Food and the Marine"]["value_eur"] == 300_000


def test_milestones_and_strategy(narrative):
    milestones = {m["key"]: m for m in narrative["milestones"]}
    assert milestones["loamin"]["details"]["goodwill_eur"] == 669_500
    assert milestones["loamin"]["details"]["contingent_consideration_eur"] == 850_000
    assert milestones["senus_2030"]["details"]["min_cagr_pct"] == 50
    assert milestones["senus_2030"]["details"]["ebitda_positive_fy"] == "FY2028"
    assert narrative["strategy"]["min_cagr_pct"] == 50
    assert {d["topic"] for d in narrative["drivers"]} >= {"weather", "pipeline", "ai_integration"}


def test_every_fact_is_grounded_in_the_pdf(narrative):
    pages = {n: _norm(t) for n, t in read_pages(str(PDF))}
    facts = (
        narrative["milestones"]
        + narrative["commercial"]["customers"]
        + narrative["people"]
        + narrative["drivers"]
        + list(narrative["commercial"]["aggregates"].values())
    )
    assert facts
    for fact in facts:
        assert fact["quote"] in pages[fact["page"]], fact


@pytest.mark.skipif(not TXT.exists(), reason="reference TXT not available")
def test_sections_match_reference_text(narrative):
    reference = _norm(TXT.read_text(encoding="utf-8"))
    reference = re.sub(r"[=-]{3,}", " ", reference)
    for name in ("Chairman's Statement", "Commercial Progress", "Pipeline & Outlook",
                 "Loamin Acquisition & Technology Integration", "Senus 2030", "Notes to Editors"):
        got = _norm(" ".join(narrative["sections"][name]["paragraphs"]))
        matcher = difflib.SequenceMatcher(None, got, reference, autojunk=False)
        covered = sum(block.size for block in matcher.get_matching_blocks())
        assert covered / len(got) > 0.99, name
