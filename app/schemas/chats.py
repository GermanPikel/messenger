from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.chats import ChatType


class ChatBase(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    chat_type: ChatType


class ChatCreate(ChatBase):
    member_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_chat_title(self):
        if self.chat_type == ChatType.group and not self.title:
            raise ValueError("Group chat title is required")
        return self


class ChatUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)


class ChatRead(ChatBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_by_id: int | None
    created_at: datetime
