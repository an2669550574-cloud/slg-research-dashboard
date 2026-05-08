from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class MaterialCreate(BaseModel):
    app_id: str
    title: str
    url: str
    platform: Optional[str] = None
    material_type: str = "video"
    tags: list[str] = []
    notes: Optional[str] = None


class MaterialUpdate(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None
    platform: Optional[str] = None
    material_type: Optional[str] = None
    tags: Optional[list[str]] = None
    notes: Optional[str] = None


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app_id: str
    title: str
    url: str
    platform: Optional[str] = None
    material_type: str = "video"
    tags: list[str] = []
    notes: Optional[str] = None
    created_at: datetime
