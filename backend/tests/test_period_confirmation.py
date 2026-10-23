import asyncio
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api.documents import confirm_document_periods, process_document
from app.schemas.documents import ConfirmPeriodsRequest
from app.services.ingest import read_pages
from app.services.period_detector import (
    derive_label,
    detect_all_periods,
    period_to_column_map,
    statement_header_periods,
    validate_detected_periods,
)


def test_hy2026_pdf_detects_two_columns_and_matching_statement_dates():
    pdf = Path(__file__).resolve().parents[1] / "data" / "Senus_HY2026_Interim_Results.pdf"
    pages = read_pages(str(pdf))
    periods = detect_all_periods(pages)
    statement_periods, _ = statement_header_periods(pages)

    assert [(p.label, p.column) for p in periods] == [("HY2026", 0), ("HY2025", 1)]
    assert periods[0].start_date == date(2025, 7, 1)
    assert periods[0].end_date == date(2025, 12, 31)
    assert periods[1].start_date == date(2024, 7, 1)
    assert periods[1].end_date == date(2024, 12, 31)
    assert periods[0].pages == [5, 6]
    assert period_to_column_map(pages) == {
        (date(2025, 7, 1), date(2025, 12, 31)): 0,
        (date(2024, 7, 1), date(2024, 12, 31)): 1,
    }
    assert validate_detected_periods(
        [
            {"column": p.column, "start_date": p.start_date, "end_date": p.end_date}
            for p in periods
        ],
        statement_periods,
    ) == []


def test_ambiguous_current_prior_headers_require_manual_dates():
    periods = detect_all_periods(
        [(1, "Consolidated Profit and Loss Account\nCurrent period    Prior period")]
    )

    assert len(periods) == 2
    assert all(period.source == "ambiguous" for period in periods)
    assert all(period.start_date is None and period.end_date is None for period in periods)
    assert any("ambiguous dates" in error for error in validate_detected_periods(
        [{"column": period.column, "start_date": period.start_date, "end_date": period.end_date}
         for period in periods]
    ))


def test_different_comparable_durations_are_rejected():
    errors = validate_detected_periods(
        [
            {"column": 0, "start_date": "2025-07-01", "end_date": "2025-12-31"},
            {"column": 1, "start_date": "2024-01-01", "end_date": "2024-12-31"},
        ]
    )

    assert "Comparable columns have different period durations." in errors
    assert any("exactly 12 months" in error for error in errors)


def test_manual_date_change_recalculates_label_and_is_audited():
    document = SimpleNamespace(
        id=9,
        filename="periods.pdf",
        status="uploaded",
        period_label="HY2026",
        period_start=date(2025, 7, 1),
        period_end=date(2025, 12, 31),
        periods_detected_json=json.dumps(
            {
                "periods": [
                    {
                        "column": 0,
                        "label": "HY2026",
                        "start_date": "2025-07-01",
                        "end_date": "2025-12-31",
                        "pages": [1],
                        "source": "regex",
                    }
                ],
                "statement_periods": {},
                "confirmed": False,
                "validation_errors": [],
            }
        ),
        periods_manually_edited=False,
        edited_by=None,
        edited_at=None,
        doc_type="interim_results",
        page_count=1,
        error=None,
        uploaded_at=None,
        extracted_at=None,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=document),
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    user = SimpleNamespace(email="admin@example.test")
    request = ConfirmPeriodsRequest(
        periods=[{"column": 0, "start_date": "2024-07-01", "end_date": "2025-06-30"}]
    )

    response = asyncio.run(confirm_document_periods(9, request, db, user))
    audits = [call.args[0] for call in db.add.call_args_list]
    edit_audit = next(audit for audit in audits if audit.action == "edit_period_dates")

    assert derive_label(date(2024, 7, 1), date(2025, 6, 30)) == "FY2025"
    assert response.detected_periods[0].label == "FY2025"
    assert response.periods_confirmed is True
    assert document.periods_manually_edited is True
    assert edit_audit.user_email == user.email
    assert json.loads(edit_audit.before_json)["label"] == "HY2026"
    assert json.loads(edit_audit.after_json)["label"] == "FY2025"


def test_processing_is_blocked_until_periods_are_confirmed():
    document = SimpleNamespace(
        id=9,
        periods_detected_json=json.dumps(
            {"periods": [], "confirmed": False, "validation_errors": ["missing dates"]}
        ),
    )
    db = SimpleNamespace(get=AsyncMock(return_value=document))

    with pytest.raises(HTTPException) as error:
        asyncio.run(process_document(9, db, SimpleNamespace(email="admin@example.test")))

    assert error.value.status_code == 409
    assert "Confirm all reporting periods" in error.value.detail


def test_processing_existing_periods_requires_explicit_confirmation():
    document = SimpleNamespace(
        id=9,
        status="uploaded",
        periods_detected_json=json.dumps(
            {
                "periods": [
                    {
                        "column": 0,
                        "start_date": "2025-07-01",
                        "end_date": "2025-12-31",
                    }
                ],
                "confirmed": True,
                "validation_errors": [],
            }
        ),
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=document),
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: ["HY2026"])
            )
        ),
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            process_document(
                9,
                db,
                SimpleNamespace(email="admin@example.test"),
            )
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "period_replacement_confirmation_required"
    assert error.value.detail["periods"] == ["HY2026"]
    assert document.status == "uploaded"
