from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class OwnProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    brief: str = Field(..., min_length=1, max_length=4000)
    is_default: bool = False


class OwnProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    brief: Optional[str] = Field(None, min_length=1, max_length=4000)
    is_default: Optional[bool] = None


class OwnProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    brief: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
