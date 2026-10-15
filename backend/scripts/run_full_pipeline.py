import asyncio
import subprocess
import sys
from pathlib import Path


async def run_extraction():
    """Run the full extraction pipeline for both PDFs"""
    backend_dir = Path(__file__).parent
    scripts_dir = backend_dir / "scripts"
    pdf_dir = Path(r"C:\Users\lucap\Downloads")

    # PDF 1: Half Year Results Dec 2025
    hy_pdf = pdf_dir / "Senus_HalfYearResultsDec2025_PR_V19032026-FINAL-clean.pdf"
    # PDF 2: Assignment doc (has FY2025 data in text)
    assign_pdf = pdf_dir / "Assiduous_TechGradAssignment.pdf"

    output_dir = scripts_dir / "extracted"
    output_dir.mkdir(exist_ok=True)

    extractions = [
        {
            "pdf": hy_pdf,
            "period": "HY2026",
            "start": "2025-07-01",
            "end": "2025-12-31",
            "output": output_dir / "hy2026_extracted.json"
        },
        # Assignment PDF has FY2025 full year data in the text
        {
            "pdf": assign_pdf,
            "period": "FY2025",
            "start": "2024-07-01",
            "end": "2025-06-30",
            "output": output_dir / "fy2025_extracted.json"
        }
    ]

    for ext in extractions:
        if not ext["pdf"].exists():
            print(f"PDF not found: {ext['pdf']}")
            continue

        print(f"\n=== Extracting {ext['period']} from {ext['pdf'].name} ===")
        cmd = [
            sys.executable, "extract_pdf.py",
            str(ext["pdf"]),
            ext["period"],
            ext["start"],
            ext["end"]
        ]
        result = subprocess.run(cmd, cwd=scripts_dir, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Extraction failed: {result.stderr}")
            continue

        # Save output
        with open(ext["output"], "w") as f:
            f.write(result.stdout)
        print(f"Saved to {ext['output']}")

        # Load into DB
        print(f"Loading {ext['period']} into database...")
        load_cmd = [sys.executable, "load_data.py", str(ext["output"])]
        load_result = subprocess.run(load_cmd, cwd=scripts_dir, capture_output=True, text=True)
        if load_result.returncode != 0:
            print(f"Load failed: {load_result.stderr}")
        else:
            print(load_result.stdout)


if __name__ == "__main__":
    asyncio.run(run_extraction())