import sys

sys.path.insert(0, "/app")

import asyncio
import json

from app.core.seed import load_extracted


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python load_data.py <extracted_json_file>")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)

    label = asyncio.run(load_extracted(data))
    print(f"Loaded {label} successfully!")
    print(f"  P&L items: {len(data.get('pnl', {}))}")
    print(f"  BS items: {len(data.get('balance_sheet', {}))}")
    print(f"  CF items: {len(data.get('cash_flow', {}))}")
    print(f"  KPIs: {len(data.get('kpis', {}))}")
