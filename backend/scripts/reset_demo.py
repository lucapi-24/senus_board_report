"""Reset the configured database to the checked-in Senus demo fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import delete, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.seed import EXTRACTED_DIR, load_extracted, seed_users
from app.models.financial import (
    AuditLog,
    BalanceSheetItem,
    CashFlowItem,
    Document,
    Insight,
    KPI,
    Period,
    PnLItem,
    User,
    Validation,
)

DEMO_FIXTURES = (
    "hy2026_extracted.json",
    "hy2025_extracted.json",
    "fy2025_extracted.json",
)
UPLOAD_ROOT = (BACKEND_ROOT / settings.UPLOAD_DIR).resolve()


def _read_demo_fixtures() -> list[dict]:
    fixtures = []
    for filename in DEMO_FIXTURES:
        path = EXTRACTED_DIR / filename
        with path.open(encoding="utf-8") as fixture_file:
            fixtures.append(json.load(fixture_file))
    return fixtures


async def reset_demo() -> list[str]:
    fixtures = _read_demo_fixtures()
    async with async_session_maker() as session:
        async with session.begin():
            document_paths = (
                await session.execute(
                    select(Document.stored_path).where(Document.stored_path.is_not(None))
                )
            ).scalars().all()
            resolved_paths = [Path(raw_path).resolve() for raw_path in document_paths]
            if any(not path.is_relative_to(UPLOAD_ROOT) for path in resolved_paths):
                raise RuntimeError(
                    "Refusing to reset because a stored document path is outside the upload directory."
                )
            for model in (
                AuditLog,
                Insight,
                Validation,
                KPI,
                PnLItem,
                BalanceSheetItem,
                CashFlowItem,
                Period,
                Document,
                User,
            ):
                await session.execute(delete(model))

    for path in resolved_paths:
        if path.is_file():
            path.unlink()

    await seed_users()
    return [await load_extracted(fixture) for fixture in fixtures]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete all data in the configured database and reload the demo fixtures."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required confirmation before deleting all configured database records.",
    )
    args = parser.parse_args()
    if not args.confirm:
        parser.error("This reset is destructive; pass --confirm after checking DATABASE_URL.")

    labels = asyncio.run(reset_demo())
    print(f"Demo database restored with periods: {', '.join(labels)}")
    print("Demo admin and viewer accounts were recreated with the repository's demo credentials.")


if __name__ == "__main__":
    main()
