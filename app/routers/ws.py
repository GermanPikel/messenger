import json

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ALGORITHM, SECRET_KEY
from app.database import async_session_maker
from app.models.chat_members import ChatMember
from app.models.messages import Message
from app.models.users import User

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, set[WebSocket]] = {}

    async def connect(self, chat_id: int, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(chat_id, set()).add(websocket)

    def disconnect(self, chat_id: int, websocket: WebSocket):
        connections = self.active_connections.get(chat_id)
        if connections is None:
            return

        connections.discard(websocket)
        if not connections:
            self.active_connections.pop(chat_id, None)

    async def broadcast(self, chat_id: int, message: dict):
        connections = list(self.active_connections.get(chat_id, set()))
        for connection in connections:
            await connection.send_json(message)


manager = ConnectionManager()


async def get_user_from_token(db: AsyncSession, token: str | None) -> User | None:
    if token is None:
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return None

    email: str | None = payload.get("sub")
    token_type: str | None = payload.get("token_type")
    if email is None or token_type != "access":
        return None

    result = await db.scalars(select(User).where(User.email == email))
    return result.first()


async def is_active_chat_member(db: AsyncSession, chat_id: int, user_id: int) -> bool:
    result = await db.scalars(
        select(ChatMember.id).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id,
            ChatMember.left_at.is_(None),
        )
    )
    return result.first() is not None


def parse_message_text(raw_data: str) -> str | None:
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        return None

    text = data.get("text")
    if not isinstance(text, str):
        return None

    text = text.strip()
    return text or None


@router.websocket("/ws/chats/{chat_id}")
async def chat_websocket(websocket: WebSocket, chat_id: int):
    async with async_session_maker() as db:
        token = websocket.query_params.get("token")
        user = await get_user_from_token(db, token)
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if not await is_active_chat_member(db, chat_id, user.id):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await manager.connect(chat_id, websocket)

        try:
            while True:
                raw_data = await websocket.receive_text()
                text = parse_message_text(raw_data)
                if text is None:
                    await websocket.send_json({
                        "type": "error",
                        "detail": "Message payload must be JSON with non-empty text field",
                    })
                    continue

                if not await is_active_chat_member(db, chat_id, user.id):
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

                message = Message(chat_id=chat_id, sender_id=user.id, text=text)
                db.add(message)
                await db.commit()
                await db.refresh(message)

                await manager.broadcast(
                    chat_id,
                    {
                        "type": "message",
                        "id": message.id,
                        "chat_id": message.chat_id,
                        "sender_id": message.sender_id,
                        "sender_username": user.username,
                        "text": message.text,
                        "created_at": message.created_at.isoformat(),
                    },
                )
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(chat_id, websocket)
