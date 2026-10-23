from pydantic import BaseModel, Field, ConfigDict
from typing import List, Literal, Optional
from datetime import date


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


class LineItemResponse(BaseModel):
    line_item: str
    value_eur: float
    page: Optional[int] = None
    origin: Optional[str] = None
    review_status: Optional[str] = None
    confidence: Optional[float] = None
    original_value: Optional[float] = None

    class Config:
        from_attributes = True


class PnLItemResponse(LineItemResponse):
    pass


class BalanceSheetItemResponse(LineItemResponse):
    pass


class CashFlowItemResponse(LineItemResponse):
    pass


class ItemUpdate(BaseModel):
    statement: Literal["pnl", "balance_sheet", "cash_flow"]
    line_item: str
    action: Literal["confirm", "correct"]
    value: Optional[float] = None


class KPIResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    metric: str
    value: Optional[float] = None
    formula: Optional[str] = None
    kind: Optional[str] = None
    note: Optional[str] = None


class PeriodDetailResponse(PeriodResponse):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    pnl: List[PnLItemResponse] = Field(default_factory=list, validation_alias="pnl_items")
    balance_sheet: List[BalanceSheetItemResponse] = Field(default_factory=list, validation_alias="bs_items")
    cash_flow: List[CashFlowItemResponse] = Field(default_factory=list, validation_alias="cf_items")
    kpis: List[KPIResponse] = Field(default_factory=list, validation_alias="kpis")
    metadata: Optional[dict] = Field(default=None, validation_alias="doc_meta")
    provenance: Optional[dict] = Field(default=None, validation_alias="doc_provenance")


class FinancialSummary(BaseModel):
    period: PeriodResponse
    duration_months: int
    revenue: Optional[float] = None
    revenue_origin: str = "n/d"
    gross_profit: Optional[float] = None
    gross_profit_origin: str = "n/d"
    gross_margin: Optional[float] = None
    gross_margin_origin: str = "n/d"
    operating_loss: Optional[float] = None
    operating_loss_origin: str = "n/d"
    operating_margin: Optional[float] = None
    operating_margin_origin: str = "n/d"
    cash: Optional[float] = None
    cash_origin: str = "n/d"
    debt: Optional[float] = None
    debt_origin: str = "n/d"
    runway_months: Optional[float] = None
    runway_origin: str = "n/d"
    revenue_yoy_change_pct: Optional[float] = None
    revenue_yoy_period_label: Optional[str] = None


class AIInsight(BaseModel):
    section: str
    insight: str
    confidence: float


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    period_label: str
    history: List[ChatMessage] = Field(default_factory=list)


class ChatResponse(BaseModel):
    message: str
    sources: List[str] = Field(default_factory=list)


class ValidationResponse(BaseModel):
    id: int
    rule: str
    severity: str
    message: str
    expected: Optional[float] = None
    actual: Optional[float] = None

    class Config:
        from_attributes = True


class PeriodCompareRequest(BaseModel):
    period_label_a: str
    period_label_b: str


class PeriodCompareResponse(BaseModel):
    label_a: str
    label_b: str
    insight: str
    confidence: float