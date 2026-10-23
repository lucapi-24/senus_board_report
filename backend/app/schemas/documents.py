from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DetectedPeriod(BaseModel):
    """A dated or unresolved statement column awaiting administrator confirmation."""
    label: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    column: Optional[int] = None
    pages: List[int] = Field(default_factory=list)
    source: str = "regex"
    score: float = 50.0
    is_primary: bool = False
    duration_months: Optional[int] = None


class DocumentResponse(BaseModel):
    id: int
    filename: str
    doc_type: Optional[str] = None
    period_label: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    status: str
    page_count: Optional[int] = None
    error: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    extracted_at: Optional[datetime] = None
    # New: auto-detected periods from document headers
    detected_periods: List[DetectedPeriod] = Field(default_factory=list)
    periods_confirmed: bool = False
    period_validation_errors: List[str] = Field(default_factory=list)
    # New: whether the user manually edited period dates/label
    periods_manually_edited: bool = False
    edited_by: Optional[str] = None
    edited_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DocumentDetailResponse(DocumentResponse):
    review: Optional[Dict[str, Any]] = None
    reviews_by_period: Optional[Dict[str, Dict[str, Any]]] = None


class DocumentUpdate(BaseModel):
    doc_type: Optional[str] = None


class PeriodConfirmationInput(BaseModel):
    column: int = Field(ge=0)
    start_date: date
    end_date: date


class ConfirmPeriodsRequest(BaseModel):
    periods: List[PeriodConfirmationInput] = Field(min_length=1)
