"""Run three fresh HY2026/HY2025 extraction pairs on the isolated review copy.

Run inside the API container:
python scripts/repeat_extraction_review_copy.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.financial import Period
from app.services.ingest import ingest_document

DATABASE_NAME = "senus_board_review_copy"
DOCUMENT_ID = 6
PERIOD_COLUMNS = (("HY2026", 0), ("HY2025", 1))
RUN_COUNT = 3


def _coverage_count(trace: Dict[str, Any]) -> int:
    return sum(
        len(labels)
        for labels in (trace.get("missing_expected") or {}).values()
    )


async def main() -> None:
    url = make_url(settings.DATABASE_URL).set(database=DATABASE_NAME)
    if url.database != DATABASE_NAME:
        raise RuntimeError("Refusing to run outside senus_board_review_copy.")

    engine = create_async_engine(url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with session_factory() as db:
            periods = (
                await db.scalars(
                    select(Period)
                    .where(Period.source_doc_id == DOCUMENT_ID)
                    .order_by(Period.label)
                )
            ).all()
            period_by_label = {period.label: period for period in periods}
            missing_periods = {
                label for label, _column in PERIOD_COLUMNS
            } - period_by_label.keys()
            if missing_periods:
                raise RuntimeError(
                    f"Document {DOCUMENT_ID} is missing review-copy periods: "
                    f"{sorted(missing_periods)}"
                )

            for run_number in range(1, RUN_COUNT + 1):
                for label, column in PERIOD_COLUMNS:
                    period = period_by_label[label]
                    proposal = await ingest_document(
                        document_id=DOCUMENT_ID,
                        db=db,
                        column=column,
                        period_label=label,
                        start_date=period.start_date,
                        end_date=period.end_date,
                        replace_existing=True,
                    )
                    trace = proposal.get("llm_trace") or {}
                    expected = trace.get("expected_count")
                    missing_count = _coverage_count(trace)
                    print(
                        json.dumps(
                            {
                                "run": run_number,
                                "period": label,
                                "coverage": (
                                    f"{expected - missing_count}/{expected}"
                                    if isinstance(expected, int)
                                    else None
                                ),
                                "expected_count": expected,
                                "missing_expected": trace.get("missing_expected", {}),
                                "accepted_count": trace.get("accepted_count"),
                                "discarded_count": trace.get("discarded_count"),
                                "discarded_lines": trace.get("discarded_lines", []),
                                "prompt_tokens": trace.get("prompt_tokens"),
                                "completion_tokens": trace.get("completion_tokens"),
                                "total_tokens": trace.get("total_tokens"),
                                "latency_ms": trace.get("latency_ms"),
                                "finish_reason": trace.get("finish_reason"),
                                "status": trace.get("status"),
                                "warning": trace.get("warning"),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
