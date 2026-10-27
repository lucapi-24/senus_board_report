"""Compare regex and Groq extraction after renaming one PDF statement label.

Run from the backend container:
python scripts/evaluate_extraction_robustness.py /app/data/uploads/document.pdf

The source PDF is never modified. The altered copy is temporary unless
``--output-pdf`` is provided.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import fitz
import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.deterministic import parse_tables
from app.services.llm_extract import extract_structured, select_statement_pages
from app.services.reconciliation import reconcile


def _read_pages(pdf_path: Path) -> list[tuple[int, str]]:
    with pdfplumber.open(pdf_path) as pdf:
        return [
            (number, page.extract_text() or "")
            for number, page in enumerate(pdf.pages, start=1)
        ]


def _rename_pdf_label(
    source: Path, destination: Path, label: str, replacement: str, page_number: int
) -> None:
    document = fitz.open(source)
    page = document[page_number - 1]
    matches = page.search_for(label)
    if len(matches) != 1:
        document.close()
        raise ValueError(
            f"Expected exactly one {label!r} on PDF page {page_number}, found {len(matches)}."
        )

    rectangle = matches[0]
    page.add_redact_annot(rectangle, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text(
        (rectangle.x0, rectangle.y1 - 1),
        replacement,
        fontsize=max(8, rectangle.height * 0.9),
        fontname="helv",
        color=(0, 0, 0),
    )
    document.save(destination, garbage=4, deflate=True)
    document.close()


async def evaluate(
    source: Path,
    altered: Path,
    label: str,
    replacement: str,
    page_number: int,
    column: int,
) -> dict:
    _rename_pdf_label(source, altered, label, replacement, page_number)
    pages = _read_pages(altered)
    tables = parse_tables(pages, column=column)
    selected_pages = select_statement_pages(pages, tables)
    llm_result = await extract_structured(
        selected_pages,
        "ROBUSTNESS-HY2026",
        column=column,
    )
    reconciled = reconcile(tables, llm_result.lines, pages=pages)
    regex_labels = sorted(
        label
        for statement in ("pnl", "balance_sheet", "cash_flow")
        for label in (tables.get(statement) or {})
    )
    llm_label_matches = [
        {
            "statement": line.statement,
            "label": line.line_item,
            "value": line.value,
            "page": line.page,
        }
        for line in llm_result.lines
        if replacement.casefold() in line.line_item.casefold()
    ]
    llm_only = [
        {
            "statement": item.statement,
            "label": item.line_item,
            "value": item.value,
            "page": item.page,
        }
        for statement in ("pnl", "balance_sheet", "cash_flow")
        for item in reconciled[statement]
        if item.status == "llm_only"
    ]
    return {
        "source_pdf": str(source),
        "altered_label": label,
        "replacement_label": replacement,
        "page": page_number,
        "column": column,
        "regex_item_count": len(regex_labels),
        "regex_has_original_label": any(label.casefold() == key.casefold() for key in regex_labels),
        "regex_labels": regex_labels,
        "llm_item_count": len(llm_result.lines),
        "llm_items_by_statement": {
            statement: sum(line.statement == statement for line in llm_result.lines)
            for statement in ("pnl", "balance_sheet", "cash_flow")
        },
        "llm_replacement_label_matches": llm_label_matches,
        "llm_only_items": llm_only,
        "selected_pages": [number for number, _text in selected_pages],
        "llm_trace": {
            "model": llm_result.trace.get("model"),
            "api_result": llm_result.trace.get("api_result"),
            "status": llm_result.trace.get("status"),
            "prompt_tokens": llm_result.trace.get("prompt_tokens"),
            "completion_tokens": llm_result.trace.get("completion_tokens"),
            "total_tokens": llm_result.trace.get("total_tokens"),
            "latency_ms": llm_result.trace.get("latency_ms"),
            "discarded_lines": llm_result.trace.get("discarded_lines", []),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Path to the original source PDF")
    parser.add_argument("--label", default="Gross Profit")
    parser.add_argument("--replacement", default="Gross margin")
    parser.add_argument("--page", type=int, default=5)
    parser.add_argument("--column", type=int, default=0)
    parser.add_argument(
        "--output-pdf",
        type=Path,
        help="Optional path to keep the altered PDF; temporary by default",
    )
    args = parser.parse_args()
    if not args.pdf.is_file():
        parser.error(f"PDF does not exist: {args.pdf}")

    if args.output_pdf:
        args.output_pdf.parent.mkdir(parents=True, exist_ok=True)
        result = asyncio.run(
            evaluate(
                args.pdf,
                args.output_pdf,
                args.label,
                args.replacement,
                args.page,
                args.column,
            )
        )
    else:
        with tempfile.TemporaryDirectory(prefix="senus-extraction-robustness-") as directory:
            altered = Path(directory) / "renamed-label.pdf"
            result = asyncio.run(
                evaluate(
                    args.pdf,
                    altered,
                    args.label,
                    args.replacement,
                    args.page,
                    args.column,
                )
            )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
