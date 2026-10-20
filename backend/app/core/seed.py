"""Load extracted financial JSON files into the database.

Used both by the API on first boot (so a fresh `docker compose up` shows a
populated dashboard) and by the CLI loader in `scripts/load_data.py`.
"""

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import delete, func, select

from app.core.database import async_session_maker
from app.core.security import hash_password
from app.models.financial import (
    BalanceSheetItem,
    CashFlowItem,
    Document,
    KPI,
    PnLItem,
    Period,
    User,
    Validation,
)
from app.services.validation import validate_period

EXTRACTED_DIR = Path(__file__).resolve().parents[2] / "scripts" / "extracted"

# Demo accounts seeded on first boot. Password is intentionally simple; this is
# a local assessment environment, not a production deployment.
DEMO_USERS = [
    {"email": "board@senus.com", "role": "admin", "password": "senus123"},
    {"email": "viewer@senus.com", "role": "viewer", "password": "senus123"},
]


async def seed_users() -> List[str]:
    """Create the demo admin/viewer accounts if they don't exist yet."""
    created: List[str] = []
    async with async_session_maker() as session:
        for spec in DEMO_USERS:
            exists = (
                await session.execute(select(User).where(User.email == spec["email"]))
            ).scalars().first()
            if exists:
                continue
            session.add(
                User(
                    email=spec["email"],
                    role=spec["role"],
                    hashed_password=hash_password(spec["password"]),
                )
            )
            created.append(spec["email"])
        await session.commit()
    return created


async def load_extracted(data: Dict[str, Any]) -> str:
    """Insert (or replace) a single extracted period. Returns its label."""
    label = data["period_label"]

    async with async_session_maker() as session:
        existing = (
            (await session.execute(select(Period).where(Period.label == label)))
            .scalars()
            .all()
        )
        for old in existing:
            await session.delete(old)
        await session.execute(delete(Document).where(Document.period_label == label))
        await session.flush()

        doc = Document(
            filename=data.get("source_file", "unknown.pdf"),
            period_label=label,
            doc_type="interim_results",
            status="published",
        )
        session.add(doc)
        await session.flush()

        metadata = data.get("metadata")
        provenance = data.get("provenance")
        period = Period(
            label=label,
            start_date=date.fromisoformat(data["start_date"]),
            end_date=date.fromisoformat(data["end_date"]),
            source_doc_id=doc.id,
            meta_json=json.dumps(metadata) if metadata else None,
            provenance_json=json.dumps(provenance) if provenance else None,
        )
        session.add(period)
        await session.flush()

        for line_item, value in data.get("pnl", {}).items():
            session.add(
                PnLItem(period_id=period.id, line_item=line_item, value_eur=Decimal(str(value)))
            )
        for line_item, value in data.get("balance_sheet", {}).items():
            session.add(
                BalanceSheetItem(
                    period_id=period.id, line_item=line_item, value_eur=Decimal(str(value))
                )
            )
        for line_item, value in data.get("cash_flow", {}).items():
            session.add(
                CashFlowItem(
                    period_id=period.id, line_item=line_item, value_eur=Decimal(str(value))
                )
            )
        for metric, value in data.get("kpis", {}).items():
            session.add(KPI(period_id=period.id, metric=metric, value=Decimal(str(value))))

        # Use validations from the extraction output when present, otherwise run
        # the rules here so committed fixtures still get checked on load.
        issues = data.get("validations")
        if issues is None:
            issues = [
                issue.as_dict()
                for issue in validate_period(
                    data.get("pnl", {}), data.get("balance_sheet", {}), data.get("cash_flow", {})
                )
            ]
        for issue in issues:
            session.add(
                Validation(
                    period_id=period.id,
                    rule=issue["rule"],
                    severity=issue["severity"],
                    message=issue["message"],
                    expected=issue.get("expected"),
                    actual=issue.get("actual"),
                )
            )

        doc.raw_json = json.dumps(data.get("narrative_insights", {}))
        await session.commit()

    return label


async def seed_from_disk(force: bool = False) -> List[str]:
    """Load every JSON in scripts/extracted when the database is empty.

    Set ``force=True`` to reload regardless of existing data. The schema is
    expected to exist already (applied by Alembic).

    Set env ``SEED_FROM_DISK=false`` to skip seeding (useful for demos where
    the database should start empty).
    """
    if os.environ.get("SEED_FROM_DISK", "true").lower() == "false":
        return []

    if not EXTRACTED_DIR.exists():
        return []

    async with async_session_maker() as session:
        count = await session.scalar(select(func.count()).select_from(Period))
    if count and not force:
        return []

    loaded: List[str] = []
    for path in sorted(EXTRACTED_DIR.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        loaded.append(await load_extracted(data))
    return loaded
