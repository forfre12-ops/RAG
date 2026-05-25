from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Grade(str, Enum):
    TS = "TS"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"


class GradeDefinition(BaseModel):
    code: str
    name: str
    order: int
    description: Optional[str] = None
    color: Optional[str] = None


class Actor(BaseModel):
    user_id: str
    role: str = Field(pattern=r"^(admin|reviewer|system|kl_backend)$")
    tenant_id: Optional[str] = None
    ip: Optional[str] = None


class Error(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None
    details: Optional[dict] = None
