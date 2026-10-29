import json
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.financial import Document, Period, User
from app.services import lifecycle
from app.services.ingest import read_pages
from app.services.narrative import extract_narrative

router = APIRouter(prefix="/api/company", tags=["company"])


async def _document_for(db: AsyncSession, period_label: Optional[str]) -> Optional[Document]:
    if period_label:
        period = (
            await db.execute(select(Period).where(Period.label == period_label))
        ).scalar_one_or_none()
        if period is None:
            raise HTTPException(status_code=404, detail=f"Unknown period: {period_label}")
        return await db.get(Document, period.source_doc_id) if period.source_doc_id else None
    docs = (
        await db.execute(select(Document).order_by(Document.period_end.desc().nullslast(), Document.id.desc()))
    ).scalars().all()
    published = [d for d in docs if d.status == lifecycle.PUBLISHED]
    return next(iter(published or docs), None)


@router.get("")
async def get_company(
    period: Optional[str] = Query(None, description="Period label; defaults to the latest document"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Company profile, leadership, customers, milestones and strategy read from the source document.

    Everything returned carries its source page and a literal quote from the PDF.
    """
    doc = await _document_for(db, period)
    if doc is None:
        raise HTTPException(status_code=404, detail="No document available")
    if doc.narrative_json is None and doc.stored_path and os.path.exists(doc.stored_path):
        doc.narrative_json = json.dumps(
            extract_narrative(read_pages(doc.stored_path)), ensure_ascii=False
        )
        await db.commit()
    if doc.narrative_json is None:
        raise HTTPException(status_code=404, detail="No narrative has been extracted for this document")
    return {
        "document": {"id": doc.id, "filename": doc.filename, "status": doc.status, "period_label": doc.period_label},
        **json.loads(doc.narrative_json),
    }
