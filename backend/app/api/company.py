import json
import os
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.financial import Document, Period, User
from app.services import company_ai, lifecycle
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


async def _narrative_for(db: AsyncSession, doc: Document) -> Dict[str, Any]:
    if doc.narrative_json is None and doc.stored_path and os.path.exists(doc.stored_path):
        doc.narrative_json = json.dumps(
            extract_narrative(read_pages(doc.stored_path)), ensure_ascii=False
        )
        await db.commit()
    if doc.narrative_json is None:
        raise HTTPException(status_code=404, detail="No narrative has been extracted for this document")
    return json.loads(doc.narrative_json)


async def _ai_context(db: AsyncSession, doc: Document) -> str:
    narrative = await _narrative_for(db, doc)
    figures: Dict[str, Dict[str, float]] = {}
    if doc.period_label:
        period = (
            await db.execute(
                select(Period)
                .where(Period.label == doc.period_label)
                .options(selectinload(Period.pnl_items), selectinload(Period.bs_items), selectinload(Period.cf_items))
            )
        ).scalar_one_or_none()
        if period is not None:
            figures = {
                "income_statement": {i.line_item: float(i.value_eur) for i in period.pnl_items},
                "balance_sheet": {i.line_item: float(i.value_eur) for i in period.bs_items},
                "cash_flow": {i.line_item: float(i.value_eur) for i in period.cf_items},
            }
    return company_ai.build_context(narrative, figures)


async def _require_doc(db: AsyncSession, period: Optional[str]) -> Document:
    doc = await _document_for(db, period)
    if doc is None:
        raise HTTPException(status_code=404, detail="No document available")
    return doc


@router.get("/insights")
async def get_company_insights(
    period: Optional[str] = Query(None),
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Three AI-written interpretations of the report (not quotations)."""
    doc = await _require_doc(db, period)
    context = await _ai_context(db, doc)
    try:
        insights = await company_ai.generate_insights(f"{doc.id}:{len(context)}", context, force)
    except company_ai.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"insights": insights, "generated_by": "ai"}


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CompanyChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1500)
    history: List[ChatTurn] = Field(default_factory=list, max_length=20)
    period: Optional[str] = None


@router.post("/chat")
async def company_chat(
    body: CompanyChatRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dict[str, str]:
    doc = await _require_doc(db, body.period)
    context = await _ai_context(db, doc)
    try:
        answer = await company_ai.chat(context, body.message, [t.model_dump() for t in body.history])
    except company_ai.AIUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"message": answer}


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
