import json
from uuid import UUID

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


def serialize_message(message: Message, sender: User) -> dict:
    return {
        "id": message.id,
        "chat_id": message.chat_id,
        "sender_id": message.sender_id,
        "client_message_id": message.client_message_id,
        "sender_username": sender.username,
        "text": message.text,
        "created_at": message.created_at.isoformat(),
    }


async def get_message_by_client_id(
    db: AsyncSession,
    sender_id: int,
    client_message_id: str,
) -> Message | None:
    result = await db.scalars(
        select(Message).where(
            Message.sender_id == sender_id,
            Message.client_message_id == client_message_id,
        )
    )
    return result.first()


def parse_message_payload(raw_data: str) -> tuple[str, str] | None:
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        return None

    if data.get("type") != "message.send":
        return None

    client_message_id = data.get("client_message_id")
    if not isinstance(client_message_id, str):
        return None

    try:
        UUID(client_message_id)
    except ValueError:
        return None

    text = data.get("text")
    if not isinstance(text, str):
        return None

    text = text.strip()
    if not text:
        return None

    return client_message_id, text


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
                parsed_payload = parse_message_payload(raw_data)
                if parsed_payload is None:
                    await websocket.send_json({
                        "type": "error",
                        "code": "invalid_payload",
                        "detail": "Payload must be message.send with client_message_id UUID and non-empty text",
                    })
                    continue
                client_message_id, text = parsed_payload

                if not await is_active_chat_member(db, chat_id, user.id):
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return

                existing_message = await get_message_by_client_id(db, user.id, client_message_id)
                if existing_message is not None:
                    await websocket.send_json({
                        "type": "message.ack",
                        "client_message_id": client_message_id,
                        "message_id": existing_message.id,
                        "status": "duplicate",
                        "message": serialize_message(existing_message, user),
                    })
                    continue

                message = Message(
                    chat_id=chat_id,
                    sender_id=user.id,
                    client_message_id=client_message_id,
                    text=text,
                )
                db.add(message)
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    existing_message = await get_message_by_client_id(db, user.id, client_message_id)
                    if existing_message is None:
                        await websocket.send_json({
                            "type": "error",
                            "code": "message_save_failed",
                            "detail": "Message could not be saved",
                        })
                        continue
                    await websocket.send_json({
                        "type": "message.ack",
                        "client_message_id": client_message_id,
                        "message_id": existing_message.id,
                        "status": "duplicate",
                        "message": serialize_message(existing_message, user),
                    })
                    continue

                await db.refresh(message)
                serialized_message = serialize_message(message, user)

                await websocket.send_json({
                    "type": "message.ack",
                    "client_message_id": client_message_id,
                    "message_id": message.id,
                    "status": "saved",
                    "message": serialized_message,
                })

                await manager.broadcast(
                    chat_id,
                    {
                        "type": "message",
                        **serialized_message,
                    },
                )
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(chat_id, websocket)
