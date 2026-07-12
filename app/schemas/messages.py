from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageCreate(BaseModel):
    text: str = Field(min_length=1)


class MessageUpdate(BaseModel):
    text: str = Field(min_length=1)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    sender_id: int
    sender_username: str | None = None
    text: str
    created_at: datetime
    updated_at: datetime | None
    deleted_at: datetime | None


class MessageSearch(BaseModel):
    query: str = Field(min_length=1)
