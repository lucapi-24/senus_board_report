"""Adapter that exposes the deterministic regex parser to the app package.

The regex parser (and the KPI calculation that consumes its output) has lived
in ``scripts/extract_pdf.py`` since the start. The ingestion service now needs
it at runtime, so we import it once here — by path — rather than duplicating the
patterns, keeping a single source of truth.
"""

import sys
from pathlib import Path
from typing import Any, Dict

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from extract_pdf import clean_number, compute_kpis, extract_from_text  # noqa: E402

__all__ = ["clean_number", "compute_kpis", "extract_from_text"]


def parse_tables(pages) -> Dict[str, Any]:
    """Run the deterministic parser over page-marked text."""
    full_text = "".join(text for _, text in pages)
    return extract_from_text(full_text, column=0, pages=pages)
