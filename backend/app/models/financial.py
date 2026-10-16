import json
from typing import Any, Dict, Optional
from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, ForeignKey, UniqueConstraint, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


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


class PnLItem(Base):
    __tablename__ = "pnl_items"

    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), primary_key=True)
    line_item = Column(String(100), primary_key=True)  # 'Revenue', 'Cost of sales', 'Gross Profit', etc.
    value_eur = Column(Numeric(15, 2), nullable=False)

    period = relationship("Period", back_populates="pnl_items")

    __table_args__ = (UniqueConstraint("period_id", "line_item", name="uq_pnl_period_item"),)


class BalanceSheetItem(Base):
    __tablename__ = "balance_sheet_items"

    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), primary_key=True)
    line_item = Column(String(100), primary_key=True)  # 'Goodwill', 'Cash', 'Creditors < 1yr', etc.
    value_eur = Column(Numeric(15, 2), nullable=False)

    period = relationship("Period", back_populates="bs_items")

    __table_args__ = (UniqueConstraint("period_id", "line_item", name="uq_bs_period_item"),)


class CashFlowItem(Base):
    __tablename__ = "cash_flow_items"

    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), primary_key=True)
    line_item = Column(String(150), primary_key=True)
    value_eur = Column(Numeric(15, 2), nullable=False)

    period = relationship("Period", back_populates="cf_items")

    __table_args__ = (UniqueConstraint("period_id", "line_item", name="uq_cf_period_item"),)


class KPI(Base):
    __tablename__ = "kpis"

    period_id = Column(Integer, ForeignKey("periods.id", ondelete="CASCADE"), primary_key=True)
    metric = Column(String(50), primary_key=True)  # 'gross_margin', 'ebitda_margin', 'dscr', 'runway_months', 'roce'
    value = Column(Numeric(15, 4), nullable=False)

    period = relationship("Period", back_populates="kpis")

    __table_args__ = (UniqueConstraint("period_id", "metric", name="uq_kpi_period_metric"),)


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    period_label = Column(String(50), nullable=True)
    extracted_at = Column(DateTime(timezone=True), server_default=func.now())
    raw_json = Column(Text, nullable=True)  # JSON string of extracted data

    periods = relationship("Period", backref="source_document")