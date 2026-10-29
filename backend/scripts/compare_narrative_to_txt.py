"""Compare the narrative extracted from the PDF with a reference TXT of the same release.

Usage: python scripts/compare_narrative_to_txt.py [pdf] [txt] [--json]
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.ingest import read_pages  # noqa: E402
from app.services.narrative import _norm, extract_narrative  # noqa: E402

DEFAULT_PDF = ROOT / "Senus_HalfYearResultsDec2025_PR_V19032026-FINAL-clean.pdf"
DEFAULT_TXT = ROOT / "Otros" / "Senus_HalfYearResultsDec2025.txt"

TXT_SECTIONS = {
    "Highlights": "HIGHLIGHTS",
    "Chairman's Statement": "CHAIRMAN'S STATEMENT",
    "Notes to Editors": "NOTES TO EDITORS",
}


def txt_section_bodies(txt: str) -> dict[str, str]:
    """Split the reference TXT on its own heading lines (underlined with dashes)."""
    bodies: dict[str, list[str]] = {}
    current = "Introduction"
    lines = txt.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if re.fullmatch(r"={3,}", line.strip()):
            end = next((j for j in range(i + 1, min(i + 6, len(lines))) if re.fullmatch(r"={3,}", lines[j].strip())), None)
            if end is not None:
                current = _norm(" ".join(lines[i + 1:end])).title().replace("'S", "'s")
                i = end + 1
                continue
        elif re.fullmatch(r"-{3,}", nxt.strip()) and line.strip():
            current = _norm(line).rstrip(":")
            i += 2
            continue
        elif not re.fullmatch(r"-{3,}", line.strip()):
            bodies.setdefault(current, []).append(line)
        i += 1
    return {k: re.sub(r"(^|\s)- ", r"\1", _norm(" ".join(v))) for k, v in bodies.items()}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pdf = Path(args[0]) if args else DEFAULT_PDF
    txt_path = Path(args[1]) if len(args) > 1 else DEFAULT_TXT
    result = extract_narrative(read_pages(str(pdf)))
    if "--json" in sys.argv:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    txt = txt_path.read_text(encoding="utf-8")
    ref = {k: v for k, v in txt_section_bodies(txt).items()}
    ref_norm_all = _norm(txt)
    worst = 1.0
    for name, section in result["sections"].items():
        got = _norm(" ".join(section["bullets"] + section["paragraphs"]))
        want = next((v for k, v in ref.items() if _norm(k).lower() == name.lower()), None)
        if want is None:
            print(f"{name:55s} (no matching TXT section)")
            continue
        ratio = difflib.SequenceMatcher(None, got, want, autojunk=False).ratio()
        worst = min(worst, ratio)
        print(f"{name:55s} {ratio:6.3f}  got={len(got)} want={len(want)}")
    print("people:", [p["name"] for p in result["people"]])
    print("customers:", [c["name"] for c in result["commercial"]["customers"]])
    print("milestones:", [m["key"] for m in result["milestones"]])
    print("aggregates:", {k: v["value"] for k, v in result["commercial"]["aggregates"].items()})
    print("company:", {k: v["value"] for k, v in result["company"].items() if isinstance(v, dict)})
    missing = [
        fact["quote"]
        for group in (result["milestones"], result["commercial"]["customers"], result["drivers"], result["people"])
        for fact in group
        if fact["quote"] not in ref_norm_all
    ]
    print("quotes not in TXT:", len(missing), missing[:3])
    print(f"worst section similarity: {worst:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
