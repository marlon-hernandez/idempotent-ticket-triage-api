from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator

from models import TicketStatus


class TicketCreate(BaseModel):
    text: str = Field(..., min_length=1)

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Ticket text cannot be empty or whitespace only.")
        return cleaned


class TicketResponse(BaseModel):
    id: int
    idempotency_key: str
    text: str
    status: TicketStatus
    classification: str | None = None
    created_at: datetime
    processed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
