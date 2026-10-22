import asyncio
from datetime import date
from app.services import period_detector
from app.services.deterministic import parse_tables
from app.services.ingest import read_pages
import pdfplumber

async def test():
    pdf_path = '/app/test_user_pdf.pdf'
    pages = read_pages(pdf_path)
    
    # Get column mapping
    col_map = period_detector.period_to_column_map(pages)
    print("Column map:")
    for (sd, ed), col in col_map.items():
        print(f"  {sd} to {ed} -> column {col}")
    print()
    
    # Extract column 0 (HY2026)
    tables_col0 = parse_tables(pages, column=0)
    print("=== Column 0 (most recent - HY2026) ===")
    print(f"  PnL Revenue: {tables_col0['pnl'].get('Revenue')}")
    print(f"  PnL Gross Profit: {tables_col0['pnl'].get('Gross Profit')}")
    print(f"  BS Cash: {tables_col0['balance_sheet'].get('Cash and cash equivalents')}")
    print()
    
    # Extract column 1 (HY2025)
    tables_col1 = parse_tables(pages, column=1)
    print("=== Column 1 (comparative - HY2025) ===")
    print(f"  PnL Revenue: {tables_col1['pnl'].get('Revenue')}")
    print(f"  PnL Gross Profit: {tables_col1['pnl'].get('Gross Profit')}")
    print(f"  BS Cash: {tables_col1['balance_sheet'].get('Cash and cash equivalents')}")
    print()
    
    # Verify they're different
    col0_rev = tables_col0['pnl'].get('Revenue')
    col1_rev = tables_col1['pnl'].get('Revenue')
    print(f"Column 0 Revenue: {col0_rev}")
    print(f"Column 1 Revenue: {col1_rev}")
    print(f"Different: {col0_rev != col1_rev}")

asyncio.run(test())
