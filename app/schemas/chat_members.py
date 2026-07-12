from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChatMemberCreate(BaseModel):
    user_id: int
    is_admin: bool = False


class ChatMemberUpdate(BaseModel):
    is_admin: bool


class ChatMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    user_id: int
    is_admin: bool
    joined_at: datetime
    left_at: datetime | None
