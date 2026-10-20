from datetime import date, datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


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

    class Config:
        from_attributes = True


class DocumentDetailResponse(DocumentResponse):
    review: Optional[Dict[str, Any]] = None


class DocumentUpdate(BaseModel):
    period_label: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    doc_type: Optional[str] = None
