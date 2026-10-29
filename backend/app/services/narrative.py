"""Deterministic extraction of the narrative (non-tabular) content of a results PR.

Complements ``deterministic.parse_tables`` (statements) by reading the prose:
company profile, leadership, commercial highlights, named customers, milestones,
strategy and notes. Every fact carries the page it came from and a literal quote;
facts whose quote cannot be found in the page text are discarded, so nothing in
the output is invented.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

Page = Tuple[int, str]

SECTION_HEADINGS = {
    "highlights": "Highlights",
    "management presentation and further information": "Management Presentation and Further Information",
    "enquiries": "Enquiries",
    "listing statistics": "Listing Statistics",
    "chairman's statement": "Chairman's Statement",
    "financial results": "Financial Results",
    "commercial progress": "Commercial Progress",
    "pipeline & outlook": "Pipeline & Outlook",
    "loamin acquisition & technology integration": "Loamin Acquisition & Technology Integration",
    "senus 2030": "Senus 2030",
    "notes to editors": "Notes to Editors",
}
# Headings that open tabular blocks; their content belongs to the statements parser.
STATEMENT_HEADINGS = (
    "consolidated profit and loss",
    "consolidated balance sheet",
    "consolidated cash flow",
    "notes to the half year report",
)
NOTE_HEADING = re.compile(r"^(\d{1,2})\.\s+([A-Z][A-Za-z &/-]+)$")
SENTENCE_END = re.compile(r"[.!?:\"')]$")


def _norm(text: str) -> str:
    text = (
        text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
        .replace("\u00a0", " ").replace("–", "-").replace("—", "-")
    )
    return re.sub(r"\s+", " ", text).strip()


def _key(line: str) -> str:
    return _norm(line).rstrip(":").lower()


def _is_page_header(line: str, page_no: int) -> bool:
    key = _key(line)
    if page_no == 1:
        return False
    return key == "senus plc" or key.startswith("half year results for the 6 months ended")


def _heading_of(line: str) -> Optional[str]:
    key = _key(line)
    if key in SECTION_HEADINGS:
        return SECTION_HEADINGS[key]
    return None


def _split_sentences(text: str) -> List[str]:
    protected = re.sub(r"\b(Dr|approx|Ltd|No|vs)\.", lambda m: m.group(1) + "\u0000", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", protected)
    return [p.replace("\u0000", ".").strip() for p in parts if p.strip()]


def _blocks(pages: List[Page]) -> List[Dict[str, Any]]:
    """Group lines into blocks: heading / bullet / paragraph / line, with page numbers."""
    lines: List[Tuple[int, str]] = []
    for page_no, text in pages:
        for raw in text.splitlines():
            line = _norm(raw)
            if line and not _is_page_header(line, page_no):
                lines.append((page_no, line))

    blocks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    lengths = sorted(len(line) for _p, line in lines if "http" not in line)
    wrap_width = lengths[int(len(lengths) * 0.98) - 1] if lengths else 100

    def flush() -> None:
        nonlocal current
        if current:
            current["text"] = _norm(" ".join(current.pop("parts")))
            blocks.append(current)
        current = None

    for idx, (page_no, line) in enumerate(lines):
        heading = _heading_of(line)
        is_note = NOTE_HEADING.match(line)
        is_statement = any(_key(line).startswith(s) for s in STATEMENT_HEADINGS)
        if heading or is_note or is_statement:
            flush()
            kind = "heading" if heading or is_note else "statement_heading"
            blocks.append({"kind": kind, "text": heading or line, "page": page_no})
            continue
        if line.startswith("●"):
            flush()
            current = {"kind": "bullet", "page": page_no, "parts": [line.lstrip("● ").strip()]}
        elif current is None:
            current = {"kind": "paragraph", "page": page_no, "parts": [line]}
        else:
            current["parts"].append(line)
        next_line = lines[idx + 1][1] if idx + 1 < len(lines) else ""
        next_word = next_line.split(" ", 1)[0] if next_line else ""
        # A wrapped line is full: if the next word would have fitted, the author ended the paragraph.
        if SENTENCE_END.search(line) and len(line) + 1 + len(next_word) <= wrap_width:
            flush()
    flush()
    return blocks


def _sections(blocks: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    sections: Dict[str, Dict[str, Any]] = {}
    current = "Introduction"
    sections[current] = {"page": blocks[0]["page"] if blocks else 1, "paragraphs": [], "bullets": []}
    skipping = False
    for block in blocks:
        if block["kind"] == "statement_heading":
            skipping = not block["text"].lower().startswith("notes to the half year")
            if not skipping:
                current = "Notes"
                sections.setdefault(current, {"page": block["page"], "paragraphs": [], "bullets": []})
            continue
        if block["kind"] == "heading":
            skipping = False
            current = block["text"]
            sections.setdefault(current, {"page": block["page"], "paragraphs": [], "bullets": []})
            continue
        if skipping:
            continue
        target = "bullets" if block["kind"] == "bullet" else "paragraphs"
        sections[current][target].append({"text": block["text"], "page": block["page"]})
    return {name: s for name, s in sections.items() if s["paragraphs"] or s["bullets"]}


def _fact(page: int, quote: str, page_texts: Dict[int, str], **data: Any) -> Optional[Dict[str, Any]]:
    """Keep a fact only if its quote literally appears on the stated page."""
    needle = _norm(quote)
    for candidate in (page, page - 1, page + 1):
        if needle in page_texts.get(candidate, ""):
            return {**data, "page": candidate, "quote": needle}
    return None


def _search(pattern: str, text: str, flags: int = 0) -> Optional[re.Match]:
    return re.search(pattern, text, flags)


def _money(value: str) -> Optional[float]:
    match = re.fullmatch(r"€?\s*([\d,]*\.?\d+)\s*([kKmM]?)", value.strip())
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    return number * {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()]


def _key_values(paragraphs: List[Dict[str, Any]]) -> Dict[str, str]:
    labels = ("Trading Market and Venue", "Issued Share Capital", "ISIN", "Ticker", "Admission", "Sponsor")
    text = " ".join(p["text"] for p in paragraphs)
    text = re.sub(r"\s*\S+@\S+", "", text)
    alternation = "|".join(labels)
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(rf"({alternation})\s+(.+?)(?=\s+(?:{alternation})\s|$)", text)
    }


def _profile(
    sections: Dict[str, Dict[str, Any]], full: str, pages: Dict[int, str], raw_pages: List[Page]
) -> Dict[str, Any]:
    profile: Dict[str, Any] = {"name": "Senus PLC"}

    def put(key: str, value: Any, quote: str) -> None:
        if value is None:
            return
        for page_no, text in pages.items():
            if _norm(quote) in text:
                profile[key] = {"value": value, "page": page_no, "quote": _norm(quote)}
                return

    intro = full
    if m := _search(r"the (Irish headquartered provider of [^,]+?), today announces", intro):
        put("description", m.group(1), m.group(0).split(", today")[0])
    if m := _search(r"registered number is (\d+)", full):
        put("registration_number", m.group(1), m.group(0))
    if m := _search(r"public limited company incorporated and registered in Ireland", full):
        put("legal_form", "Public limited company, incorporated in Ireland", m.group(0))
    for page_no, raw in raw_pages:
        lines = _address_lines(raw)
        if lines:
            profile["registered_office"] = {"value": ", ".join(lines), "page": page_no, "quote": " ".join(lines)}
            break
    if m := _search(r"Founded by (.+?) in (\d{4})", full):
        put("founded", int(m.group(2)), m.group(0))
    if m := _search(r"Companies Act 2014 and FRS 102", full):
        put("accounting_framework", "Companies Act 2014 and FRS 102", m.group(0))
    if m := _search(r"year ending (\d{1,2} \w+) \d{4}", full):
        put("fiscal_year_end", m.group(1), m.group(0))
    if m := _search(r"approved by the Board of Directors on (\d{1,2} \w+ \d{4})", full):
        put("approved_on", m.group(1), m.group(0))
    if m := _search(r"Dublin, Ireland, (\d{1,2} \w+ \d{4})", full):
        put("published_on", m.group(1), m.group(0))
    if m := _search(r"(Ireland, increasingly in the UK, and has an initial presence in mainland Europe)", full):
        put("markets", ["Ireland", "United Kingdom", "Mainland Europe (initial presence)"], m.group(1))
    if m := _search(r"revenue of €([\d,]+), serving (\d+) customer accounts", full):
        put("fy25_revenue_eur", float(m.group(1).replace(",", "")), m.group(0))
        put("fy25_customer_accounts", int(m.group(2)), m.group(0))
    if m := _search(r"Tel:\s*(\+[\d ]+\d)", full):
        put("phone", m.group(1), m.group(0))
    if m := _search(r"Email:\s*([\w.]+@[\w.]+)", full):
        put("ir_email", m.group(1), m.group(0))
    if m := _search(r"Website:\s*(www\.[\w.]+)", full):
        put("website", m.group(1), m.group(0))
    if m := _search(r"(https://www\.senus\.com/investors)", full):
        put("investor_site", m.group(1), m.group(0))

    kv = _key_values(sections.get("Listing Statistics", {}).get("paragraphs", []))
    mapping = {
        "Trading Market and Venue": "market",
        "Issued Share Capital": "issued_share_capital",
        "ISIN": "isin",
        "Ticker": "ticker",
        "Admission": "admission_date",
        "Sponsor": "sponsor",
    }
    for label, key in mapping.items():
        if label in kv:
            value: Any = kv[label]
            if key == "issued_share_capital":
                value = int(value.replace(",", ""))
            put(key, value, f"{label} {kv[label]}")
    if m := _search(r"(\S+@assiduous\.tech)", full):
        put("sponsor_email", m.group(1), m.group(1))
    return profile


def _address_lines(page_text: str) -> List[str]:
    lines = [_norm(ln) for ln in page_text.splitlines()]
    try:
        start = next(i for i, ln in enumerate(lines) if ln == "Block D")
    except StopIteration:
        return []
    out = []
    for ln in lines[start:]:
        if re.match(r"^\d+\.\s", ln):
            break
        out.append(ln)
    return out


def _people(full: str, pages: Dict[int, str]) -> List[Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}

    def add(name: str, role: str, group: str, quote: str, title: str = "") -> None:
        name = _norm(name)
        for page_no, text in pages.items():
            if _norm(quote) in text:
                person = found.setdefault(
                    name, {"name": name, "title": title, "roles": [], "groups": [], "page": page_no, "quote": _norm(quote)}
                )
                if role and role not in person["roles"]:
                    person["roles"].append(role)
                if group not in person["groups"]:
                    person["groups"].append(group)
                if title and not person["title"]:
                    person["title"] = title
                return

    name_re = r"((?:Dr\. )?[A-Z][a-z]+(?: [A-Z][a-z]+)+)"
    for m in re.finditer(name_re + r", (Managing Director|General Manager)(?: of Senus)?", full):
        add(m.group(1).replace("Dr. ", ""), m.group(2), "management", m.group(0), "Dr." if "Dr." in m.group(1) else "")
    if m := _search(name_re + r" Chairman\b", full):
        add(m.group(1), "Chairman", "board", m.group(0))
    if m := _search(r"Founded by ([A-Z][\w ,]+?) in (\d{4})", full):
        for founder in re.split(r",\s*|\s+and\s+", m.group(1)):
            add(founder, f"Founder ({m.group(2)})", "founder", m.group(0))
    if m := _search(
        r"Loamin's founders ([A-Z][a-z]+ [A-Z][a-z]+) and ([A-Z][a-z]+ [A-Z][a-z]+) have assumed senior leadership roles within Senus, as (Head of [A-Za-z ]+?) and (Head of [A-Za-z ]+?) respectively",
        full,
    ):
        add(m.group(1), m.group(3), "management", m.group(0))
        add(m.group(2), m.group(4), "management", m.group(0))
        for name in (m.group(1), m.group(2)):
            found[_norm(name)]["groups"].append("loamin_founder")
    return list(found.values())


def _customers(sections: Dict[str, Dict[str, Any]], pages: Dict[int, str]) -> List[Dict[str, Any]]:
    commercial = sections.get("Commercial Progress", {}).get("paragraphs", [])
    highlights = sections.get("Highlights", {}).get("bullets", [])
    sentences = [(p["page"], s) for p in commercial for s in _split_sentences(p["text"])]
    sentences += [(b["page"], s) for b in highlights for s in _split_sentences(b["text"])]
    out: Dict[str, Dict[str, Any]] = {}

    def add(name: str, status: str, page: int, quote: str, **extra: Any) -> None:
        fact = _fact(page, quote, pages, name=name, status=status, **extra)
        if fact:
            out.setdefault(name, fact)

    for page, sentence in sentences:
        if m := re.match(r"^(.+?) and (.+?) projects were executed on and commenced in this period", sentence):
            for name in (m.group(1), m.group(2)):
                add(name, "executed", page, sentence)
        if m := re.search(r"began in \w+ with (.+?) signing up", sentence):
            names = re.split(r",\s*|\s+and\s+", m.group(1))
            for name in names:
                if name.lower().startswith("direct farmers"):
                    add("Direct farmers", "signed_up", page, sentence, segment="farmers", product="Soil sampling")
                else:
                    add(name, "signed_up", page, sentence, product="Soil sampling")
        if m := re.search(r"project has been secured with ([A-Z][\w ]+?) in the (United Kingdom)(?:, which will deploy (Senus \w+))?", sentence):
            add(m.group(1), "secured", page, sentence, country="United Kingdom", product=m.group(3),
                detail="Full supply chain project")
        if m := re.search(r"(Bank of Ireland) are to continue their engagement with Senus and have doubled the contract value in (\d{4})", sentence):
            add(m.group(1), "expanded", page, sentence, segment="financial institution",
                detail=f"Contract value doubled in {m.group(2)}")
        if m := re.search(r"technology partner for a (Department of Agriculture,? Food and the Marine)", sentence):
            add("Department of Agriculture, Food and the Marine", "confirmed", page, sentence,
                segment="government", detail="Irish State sponsored Biochar project", value_eur=_biochar_value(commercial))
        if m := re.search(r"two critical nature restoration projects in Ireland have adopted (Senus \w+)", sentence):
            add("Nature restoration projects (2)", "adopted", page, sentence, country="Ireland", product=m.group(1),
                unnamed=True)
        if m := re.search(r"research grant led by the (University of Limerick) in Biochar", sentence):
            add(m.group(1), "partner", page, sentence, segment="academia", detail="Leads the Biochar research grant")
    return list(out.values())


def _biochar_value(paragraphs: List[Dict[str, Any]]) -> Optional[float]:
    for para in paragraphs:
        if m := re.search(r"valued at (€[\d,]+) to Senus", para["text"]):
            return _money(m.group(1))
    return None


def _aggregates(full: str, pages: Dict[int, str]) -> Dict[str, Any]:
    agg: Dict[str, Any] = {}

    def put(key: str, value: Any, quote: str) -> None:
        for page_no, text in pages.items():
            if _norm(quote) in text:
                agg[key] = {"value": value, "page": page_no, "quote": _norm(quote)}
                return

    if m := _search(r"approx\. (€[\d.,]+[km]?) across (\d+) enterprise customers closed in the period", full):
        put("closed_pipeline_eur", _money(m.group(1)), m.group(0))
        put("enterprise_customers_closed", int(m.group(2)), m.group(0))
    if m := _search(r"further approx\. (€[\d.,]+[km]?) of open pipeline", full):
        put("open_pipeline_eur", _money(m.group(1)), m.group(0))
    if m := _search(r"closed (\d+) commercial deals in the final two months of (\d{4}), with a combined estimated value of approx\. (€[\d.,]+[km]?)", full):
        put("recent_deals_count", int(m.group(1)), m.group(0))
        put("recent_deals_value_eur", _money(m.group(3)), m.group(0))
    if m := _search(r"raised (€[\d.,]+[km]?) in new capital", full):
        put("new_equity_eur", _money(m.group(1)), m.group(0))
    if m := _search(r"Goodwill of (€[\d,]+) was recognised on the acquisition of Loamin Limited", full):
        put("loamin_goodwill_eur", _money(m.group(1)), m.group(0))
    if m := _search(r"contingent consideration of (€[\d,]+) has been recognised", full):
        put("loamin_contingent_consideration_eur", _money(m.group(1)), m.group(0))
    return agg


def _milestones(full: str, sections: Dict[str, Dict[str, Any]], pages: Dict[int, str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def add(key: str, title: str, kind: str, status: str, quote: Optional[str], **values: Any) -> None:
        if not quote:
            return
        for page_no, text in pages.items():
            if _norm(quote) in text:
                out.append({"key": key, "title": title, "type": kind, "status": status,
                            "details": {k: v for k, v in values.items() if v is not None},
                            "page": page_no, "quote": _norm(quote)})
                return

    def first(pattern: str) -> Optional[str]:
        m = _search(pattern, full)
        return m.group(0) if m else None

    add("loamin", "Loamin Ltd. acquisition and integration", "acquisition", "integrated",
        first(r"We completed our first acquisition when we acquired UK based Geospatial AI business Loamin Ltd\."),
        goodwill_eur=_money(m.group(1)) if (m := _search(r"Goodwill of (€[\d,]+) was recognised", full)) else None,
        contingent_consideration_eur=_money(m.group(1)) if (m := _search(r"contingent consideration of (€[\d,]+)", full)) else None)
    add("equity_raise", "€1.1m new equity funding", "funding", "completed",
        first(r"We raised €1\.1m in new capital from a mix of existing and new investors\."),
        amount_eur=1_100_000)
    admission = _search(r"Admission (\d{1,2} \w+ \d{4})", full)
    add("direct_listing", "Direct Listing on Euronext Access Dublin", "listing", "completed",
        first(r"We became a public company and completed a Direct Listing of Senus on the Euronext Access market in Dublin\."),
        admission_date=admission.group(1) if admission else None)
    value = _biochar_value(sections.get("Commercial Progress", {}).get("paragraphs", []))
    add("biochar", "Department of Agriculture, Food and the Marine - Biochar", "contract", "confirmed",
        first(r"The contract is multiannual in nature and is valued at €[\d,]+ to Senus\."),
        value_eur=value)
    add("first_milk", "First Milk full supply chain project", "contract", "secured",
        first(r"A full supply chain project has been secured with First Milk in the United Kingdom[^.]*\."))
    cagr = _search(r"compound annual growth rate of no less than (\d+)%", full)
    ebitda = _search(r"EBITDA positive during (FY\d{4})", full)
    add("senus_2030", "Senus 2030 strategy", "strategy", "plan",
        first(r"Senus 2030 is the Company's 5-year \(2026 - 2030\) strategy[^.]*\."),
        period="2026-2030", min_cagr_pct=int(cagr.group(1)) if cagr else None,
        ebitda_positive_fy=ebitda.group(1) if ebitda else None)
    return out


def _strategy(sections: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    section = sections.get("Senus 2030")
    if not section:
        return None
    text = " ".join(p["text"] for p in section["paragraphs"])
    cagr = _search(r"compound annual growth rate of no less than (\d+)%", text)
    ebitda = _search(r"EBITDA positive during (FY\d{4})", text)
    return {
        "name": "Senus 2030",
        "page": section["page"],
        "period": "2026-2030",
        "min_cagr_pct": int(cagr.group(1)) if cagr else None,
        "ebitda_positive_fy": ebitda.group(1) if ebitda else None,
        "paragraphs": [p["text"] for p in section["paragraphs"]],
    }


def _drivers(sections: Dict[str, Dict[str, Any]], pages: Dict[int, str]) -> List[Dict[str, Any]]:
    """Operating context sentences the board cares about (weather, AI, pipeline)."""
    rules = {
        "weather": r"\bwet\b|weather",
        "ai_integration": r"AI processes|geospatial AI",
        "pipeline": r"pipeline",
        "product_consolidation": r"consolidate our product portfolio",
    }
    out, seen = [], set()
    for name in ("Chairman's Statement", "Commercial Progress", "Pipeline & Outlook",
                 "Loamin Acquisition & Technology Integration"):
        for para in sections.get(name, {}).get("paragraphs", []):
            for sentence in _split_sentences(para["text"]):
                for topic, pattern in rules.items():
                    if re.search(pattern, sentence, re.I) and (topic, sentence) not in seen:
                        fact = _fact(para["page"], sentence, pages, topic=topic, text=sentence)
                        if fact:
                            out.append(fact)
                            seen.add((topic, sentence))
    return out


def extract_narrative(pages: List[Page]) -> Dict[str, Any]:
    page_texts = {n: _norm(t) for n, t in pages}
    full = _norm(" ".join(t for _n, t in pages))
    blocks = _blocks(pages)
    sections = _sections(blocks)

    result: Dict[str, Any] = {
        "company": _profile(sections, full, page_texts, pages),
        "people": _people(full, page_texts),
        "highlights": [{"text": b["text"], "page": b["page"]} for b in sections.get("Highlights", {}).get("bullets", [])],
        "commercial": {
            "aggregates": _aggregates(full, page_texts),
            "customers": _customers(sections, page_texts),
        },
        "milestones": _milestones(full, sections, page_texts),
        "strategy": _strategy(sections),
        "drivers": _drivers(sections, page_texts),
        "sections": {
            name: {"page": s["page"], "paragraphs": [p["text"] for p in s["paragraphs"]],
                   "bullets": [b["text"] for b in s["bullets"]]}
            for name, s in sections.items()
        },
        "notes": _notes(blocks),
    }
    return result


def _notes(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    notes: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    in_notes = False
    for block in blocks:
        if block["kind"] == "statement_heading":
            in_notes = block["text"].lower().startswith("notes to the half year")
            continue
        if block["kind"] == "heading":
            m = NOTE_HEADING.match(block["text"])
            if m and in_notes:
                current = {"number": int(m.group(1)), "title": m.group(2), "page": block["page"], "paragraphs": []}
                notes.append(current)
            else:
                current = None
                in_notes = False if not m else in_notes
            continue
        if current is not None and in_notes:
            current["paragraphs"].append(block["text"])
    return notes
