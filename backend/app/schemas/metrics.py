from typing import Optional

from pydantic import BaseModel


class MetricSpec(BaseModel):
    key: str
    label: str
    unit: str
    category: str
    direction: str
    formula: str
    kind: str = "derived"
    note: Optional[str] = None
