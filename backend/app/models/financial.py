import json
from typing import Any, Dict, Optional
from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    Float,
    Date,
    DateTime,
    ForeignKey,
    Text,
    Boolean,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


# --- controlled vocabularies (stored as strings for portability) -------------
# Document.status  : uploaded | processing | extracted | in_review | published
# Review status    : reported | derived | confirmed | corrected
# KPI.kind         : reported | derived | na
# Validation.severity : error | warning
# User.role        : admin | viewer


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)          # real uploaded filename
    stored_path = Column(String(500), nullable=True)        # path on disk of the uploaded file
    doc_type = Column(String(50), nullable=True)            # interim_results | annual_report | ...
    period_label = Column(String(50), nullable=True)
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, server_default="uploaded")
    page_count = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)                     # extraction failure detail
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())
    extracted_at = Column(DateTime(timezone=True), nullable=True)
    raw_json = Column(Text, nullable=True)                  # JSON string of extracted data
    periods_detected_json = Column(Text, nullable=True)     # JSON: auto-detected periods from document headers
    periods_manually_edited = Column(Boolean, nullable=False, server_default="false")  # True if user changed dates/label
    edited_by = Column(String(255), nullable=True)          # Email of user who last edited period dates/label
    edited_at = Column(DateTime(timezone=True), nullable=True)  # When period dates/label were last manually edited
    periods = relationship("Period", backref="source_document", cascade="all, delete-orphan")


class Period(Base):
    __tablename__ = "periods"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(50), unique=True, nullable=False, index=True)  # 'HY2026', 'FY2025'
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    source_doc_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    meta_json = Column(Text, nullable=True)          # JSON: currency, period length, audited…
    provenance_json = Column(Text, nullable=True)    # JSON: source page per line item
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    pnl_items = relationship("PnLItem", back_populates="period", cascade="all, delete-orphan")
    bs_items = relationship("BalanceSheetItem", back_populates="period", cascade="all, delete-orphan")
    cf_items = relationship("CashFlowItem", back_populates="period", cascade="all, delete-orphan")
    kpis = relationship("KPI", back_populates="period", cascade="all, delete-orphan")
    insights = relationship("Insight", back_populates="period", cascade="all, delete-orphan")
    validations = relationship("Validation", back_populates="period", cascade="all, delete-orphan")

    @staticmethod
    def _loads(raw: Optional[str]) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    @property
    def doc_meta(self) -> Optional[Dict[str, Any]]:
        return self._loads(self.meta_json)

    @property
    def doc_provenance(self) -> Optional[Dict[str, Any]]:
        return self._loads(self.provenance_json)


class StatementItemMixin:
    """Shared review/provenance columns for the three financial statements."""

    page = Column(Integer, nullable=True)
    review_status = Column(String(20), nullable=False, server_default="reported")
    confidence = Column(Float, nullable=True)
    original_value = Column(Numeric(15, 2), nullable=True)   # value before a manual correction
    updated_by = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class PnLItem(StatementItemMixin, Base):
    __tablename__ = "pnl_items"

    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), primary_key=True)
    line_item = Column(String(100), primary_key=True)
    value_eur = Column(Numeric(15, 2), nullable=False)

    period = relationship("Period", back_populates="pnl_items")


class BalanceSheetItem(StatementItemMixin, Base):
    __tablename__ = "balance_sheet_items"

    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), primary_key=True)
    line_item = Column(String(100), primary_key=True)
    value_eur = Column(Numeric(15, 2), nullable=False)

    period = relationship("Period", back_populates="bs_items")


class CashFlowItem(StatementItemMixin, Base):
    __tablename__ = "cash_flow_items"

    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), primary_key=True)
    line_item = Column(String(150), primary_key=True)
    value_eur = Column(Numeric(15, 2), nullable=False)

    period = relationship("Period", back_populates="cf_items")


class KPI(Base):
    __tablename__ = "kpis"

    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), primary_key=True)
    metric = Column(String(50), primary_key=True)   # gross_margin, dscr, runway_months, …
    value = Column(Numeric(15, 4), nullable=False)

    formula = Column(Text, nullable=True)           # human-readable formula
    kind = Column(String(20), nullable=False, server_default="derived")  # reported | derived | na
    note = Column(Text, nullable=True)              # why it is not available (kind == 'na')

    period = relationship("Period", back_populates="kpis")


class Insight(Base):
    __tablename__ = "insights"

    id = Column(Integer, primary_key=True, index=True)
    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), nullable=False)
    section = Column(String(100), nullable=False)
    text = Column(Text, nullable=False)
    confidence = Column(Float, nullable=True)
    sources_json = Column(Text, nullable=True)      # JSON: cited metrics / pages
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    period = relationship("Period", back_populates="insights")

    @property
    def sources(self) -> Optional[Any]:
        return Period._loads(self.sources_json)


class Validation(Base):
    __tablename__ = "validations"

    id = Column(Integer, primary_key=True, index=True)
    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), nullable=False)
    rule = Column(String(100), nullable=False)          # revenue_minus_cogs_eq_gross_profit, …
    severity = Column(String(20), nullable=False)       # error | warning
    message = Column(Text, nullable=False)
    expected = Column(Numeric(15, 4), nullable=True)
    actual = Column(Numeric(15, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    period = relationship("Period", back_populates="validations")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    role = Column(String(20), nullable=False, server_default="viewer")  # admin | viewer
    hashed_password = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String(255), nullable=True)
    action = Column(String(50), nullable=False)         # upload | extract | confirm | correct | publish | …
    entity = Column(String(50), nullable=True)          # document | pnl_item | kpi | …
    entity_id = Column(Integer, nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
