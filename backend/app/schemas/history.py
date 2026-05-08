from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class HistoryCreate(BaseModel):
    app_id: str
    event_date: str
    event_type: str
    title: str
    description: Optional[str] = None
    source: str = "manual"


class HistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app_id: str
    event_date: str
    event_type: str
    title: str
    description: Optional[str] = None
    source: str = "manual"
    created_at: datetime
