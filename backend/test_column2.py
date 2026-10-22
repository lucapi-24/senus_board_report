import asyncio
from datetime import date
from app.services import period_detector
from app.services.deterministic import parse_tables
from app.services.ingest import read_pages

async def test():
    pdf_path = '/app/test_user_pdf.pdf'
    pages = read_pages(pdf_path)
    
    # Get column mapping
    col_map = period_detector.period_to_column_map(pages)
    print("Column map (labeled periods only):")
    for (sd, ed), col in col_map.items():
        print(f"  {sd} to {ed} -> column {col}")
    print()
    
    # Extract column 0 (HY2026)
    tables_col0 = parse_tables(pages, column=0)
    rev0 = tables_col0["pnl"].get("Revenue")
    gp0 = tables_col0["pnl"].get("Gross Profit")
    admin0 = tables_col0["pnl"].get("Administrative expenses")
    cash0 = tables_col0["balance_sheet"].get("Cash and cash equivalents")
    print("=== Column 0 (HY2026 - 01/07/2025 to 31/12/2025) ===")
    print("  Revenue:", rev0)
    print("  Gross Profit:", gp0)
    print("  Admin Expenses:", admin0)
    print("  BS Cash:", cash0)
    print()
    
    # Extract column 1 (HY2025)
    tables_col1 = parse_tables(pages, column=1)
    rev1 = tables_col1["pnl"].get("Revenue")
    gp1 = tables_col1["pnl"].get("Gross Profit")
    admin1 = tables_col1["pnl"].get("Administrative expenses")
    cash1 = tables_col1["balance_sheet"].get("Cash and cash equivalents")
    print("=== Column 1 (HY2025 - 01/07/2024 to 31/12/2024) ===")
    print("  Revenue:", rev1)
    print("  Gross Profit:", gp1)
    print("  Admin Expenses:", admin1)
    print("  BS Cash:", cash1)
    print()
    
    print("Column 0 Revenue:", rev0)
    print("Column 1 Revenue:", rev1)
    print("Different:", rev0 != rev1)

asyncio.run(test())
