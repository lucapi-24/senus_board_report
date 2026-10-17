from typing import List

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.financial import User
from app.schemas.metrics import MetricSpec
from app.services.metrics import get_catalog

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/catalog", response_model=List[MetricSpec])
async def get_metric_catalog(user: User = Depends(get_current_user)):
    """Describe every derived metric: label, unit, category, direction, formula."""
    return get_catalog()
