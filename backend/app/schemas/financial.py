from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
from datetime import date
from decimal import Decimal


class PeriodBase(BaseModel):
    label: str
    start_date: date
    end_date: date


class PeriodCreate(PeriodBase):
    source_doc_id: Optional[int] = None


class PeriodResponse(PeriodBase):
    id: int
    source_doc_id: Optional[int] = None

    class Config:
        from_attributes = True


class PnLItemResponse(BaseModel):
    line_item: str
    value_eur: Decimal

    class Config:
        from_attributes = True


class BalanceSheetItemResponse(BaseModel):
    line_item: str
    value_eur: Decimal

    class Config:
        from_attributes = True


class CashFlowItemResponse(BaseModel):
    line_item: str
    value_eur: Decimal

    class Config:
        from_attributes = True


class KPIResponse(BaseModel):
    metric: str
    value: Decimal

    class Config:
        from_attributes = True


class PeriodDetailResponse(PeriodResponse):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    pnl: List[PnLItemResponse] = Field(default_factory=list, validation_alias="pnl_items")
    balance_sheet: List[BalanceSheetItemResponse] = Field(default_factory=list, validation_alias="bs_items")
    cash_flow: List[CashFlowItemResponse] = Field(default_factory=list, validation_alias="cf_items")
    kpis: List[KPIResponse] = Field(default_factory=list, validation_alias="kpis")


class FinancialSummary(BaseModel):
    period: PeriodResponse
    revenue: Optional[Decimal] = None
    gross_profit: Optional[Decimal] = None
    gross_margin: Optional[float] = None
    operating_loss: Optional[Decimal] = None
    cash: Optional[Decimal] = None
    debt: Optional[Decimal] = None
    runway_months: Optional[float] = None


class AIInsight(BaseModel):
    section: str
    insight: str
    confidence: float