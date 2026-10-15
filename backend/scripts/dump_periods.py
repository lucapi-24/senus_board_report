"""Export every period in the database back to JSON.

Useful to regenerate the canonical fixtures in `scripts/extracted/` after the
data has been fixed directly in the database.
"""

import sys

sys.path.insert(0, "/app")

import asyncio
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session_maker
from app.core.seed import EXTRACTED_DIR
from app.models.financial import Document, Period


async def dump_periods() -> None:
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
    async with async_session_maker() as session:
        periods = (
            (
                await session.execute(
                    select(Period)
                    .options(
                        selectinload(Period.pnl_items),
                        selectinload(Period.bs_items),
                        selectinload(Period.cf_items),
                        selectinload(Period.kpis),
                    )
                    .order_by(Period.end_date.desc())
                )
            )
            .scalars()
            .all()
        )

        for period in periods:
            docs = (
                (
                    await session.execute(
                        select(Document).where(Document.period_label == period.label)
                    )
                )
                .scalars()
                .all()
            )
            narrative = {}
            for doc in docs:
                if doc.raw_json:
                    try:
                        narrative = json.loads(doc.raw_json)
                    except json.JSONDecodeError:
                        narrative = {}
                    break

            payload = {
                "period_label": period.label,
                "start_date": period.start_date.isoformat(),
                "end_date": period.end_date.isoformat(),
                "pnl": {i.line_item: str(i.value_eur) for i in period.pnl_items},
                "balance_sheet": {i.line_item: str(i.value_eur) for i in period.bs_items},
                "cash_flow": {i.line_item: str(i.value_eur) for i in period.cf_items},
                "kpis": {k.metric: float(k.value) for k in period.kpis},
                "narrative_insights": narrative,
                "source_file": docs[0].filename if docs else "unknown.pdf",
            }

            out = EXTRACTED_DIR / f"{period.label.lower()}_extracted.json"
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            print(f"Wrote {out}")


if __name__ == "__main__":
    asyncio.run(dump_periods())
