import sys
sys.path.insert(0, '/app')

import asyncio
import json
from pathlib import Path
from decimal import Decimal
from datetime import date
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models.financial import Period, PnLItem, BalanceSheetItem, CashFlowItem, KPI, Document


async def load_financial_data(json_path: str):
    """Load extracted financial data into PostgreSQL"""
    with open(json_path) as f:
        data = json.load(f)

    engine = create_async_engine(settings.DATABASE_URL, echo=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Idempotent: remove any previous load of this period (cascade removes children)
        label = data["period_label"]
        existing = (await session.execute(select(Period).where(Period.label == label))).scalars().all()
        for old in existing:
            await session.delete(old)
        await session.execute(delete(Document).where(Document.period_label == label))
        await session.flush()

        # Create or get document record
        doc = Document(
            filename=data.get("source_file", "unknown.pdf"),
            period_label=data["period_label"],
        )
        session.add(doc)
        await session.flush()

        # Create period
        period = Period(
            label=data["period_label"],
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            source_doc_id=doc.id,
        )
        session.add(period)
        await session.flush()

        # Load P&L
        for line_item, value_str in data["pnl"].items():
            session.add(PnLItem(
                period_id=period.id,
                line_item=line_item,
                value_eur=Decimal(value_str),
            ))

        # Load Balance Sheet
        for line_item, value_str in data["balance_sheet"].items():
            session.add(BalanceSheetItem(
                period_id=period.id,
                line_item=line_item,
                value_eur=Decimal(value_str),
            ))

        # Load Cash Flow
        for line_item, value_str in data["cash_flow"].items():
            session.add(CashFlowItem(
                period_id=period.id,
                line_item=line_item,
                value_eur=Decimal(value_str),
            ))

        # Load KPIs
        for metric, value in data["kpis"].items():
            session.add(KPI(
                period_id=period.id,
                metric=metric,
                value=Decimal(str(value)),
            ))

        # Store narrative insights in document
        doc.raw_json = json.dumps(data.get("narrative_insights", {}))

        await session.commit()
        print(f"Loaded {data['period_label']} successfully!")
        print(f"  P&L items: {len(data['pnl'])}")
        print(f"  BS items: {len(data['balance_sheet'])}")
        print(f"  CF items: {len(data['cash_flow'])}")
        print(f"  KPIs: {len(data['kpis'])}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python load_data.py <extracted_json_file>")
        sys.exit(1)

    asyncio.run(load_financial_data(sys.argv[1]))