from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db_depends import get_async_db
from app.models.chat_members import ChatMember
from app.models.chats import Chat, ChatType
from app.models.messages import Message
from app.models.users import User
from app.schemas.chat_members import ChatMemberInvite, ChatMemberRead
from app.schemas.chats import ChatDetail, ChatListItem, GroupChatCreate, PrivateChatCreate
from app.schemas.messages import MessageRead

router = APIRouter(prefix="/chats", tags=["chats"])


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.scalars(select(User).where(User.username == username))
    return result.first()


async def get_active_membership(
    db: AsyncSession,
    chat_id: int,
    user_id: int,
) -> ChatMember | None:
    result = await db.scalars(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id,
            ChatMember.left_at.is_(None),
        )
    )
    return result.first()


async def require_active_membership(
    db: AsyncSession,
    chat_id: int,
    user_id: int,
) -> ChatMember:
    membership = await get_active_membership(db, chat_id, user_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not an active member of this chat",
        )
    return membership


async def require_group_admin(
    db: AsyncSession,
    chat: Chat,
    user_id: int,
) -> ChatMember:
    if chat.chat_type != ChatType.group:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This action is available only for group chats",
        )

    membership = await require_active_membership(db, chat.id, user_id)
    if not membership.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only chat admin can perform this action",
        )
    return membership


async def get_chat_or_404(db: AsyncSession, chat_id: int) -> Chat:
    chat = await db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return chat


async def get_private_chat_between_users(
    db: AsyncSession,
    first_user_id: int,
    second_user_id: int,
) -> Chat | None:
    first_user_chat_ids = (
        select(ChatMember.chat_id)
        .where(
            ChatMember.user_id == first_user_id,
            ChatMember.left_at.is_(None),
        )
        .subquery()
    )

    result = await db.scalars(
        select(Chat)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(
            Chat.chat_type == ChatType.private,
            Chat.id.in_(select(first_user_chat_ids.c.chat_id)),
            ChatMember.user_id == second_user_id,
            ChatMember.left_at.is_(None),
        )
    )
    return result.first()


async def get_private_chat_title(db: AsyncSession, chat_id: int, current_user_id: int) -> str:
    result = await db.scalars(
        select(User.username)
        .join(ChatMember, ChatMember.user_id == User.id)
        .where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id != current_user_id,
            ChatMember.left_at.is_(None),
        )
    )
    return result.first() or "Личный чат"


async def get_chat_title(db: AsyncSession, chat: Chat, current_user_id: int) -> str:
    if chat.chat_type == ChatType.private:
        return await get_private_chat_title(db, chat.id, current_user_id)
    return chat.title or "Групповой чат"


async def get_last_message(db: AsyncSession, chat_id: int) -> Message | None:
    result = await db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id, Message.deleted_at.is_(None))
        .order_by(desc(Message.id))
        .limit(1)
    )
    return result.first()


async def serialize_chat_for_user(
    db: AsyncSession,
    chat: Chat,
    membership: ChatMember,
    current_user_id: int,
) -> ChatListItem:
    last_message = await get_last_message(db, chat.id)
    return ChatListItem(
        id=chat.id,
        title=await get_chat_title(db, chat, current_user_id),
        chat_type=chat.chat_type,
        is_admin=membership.is_admin,
        last_message_text=last_message.text if last_message else None,
        last_message_at=last_message.created_at if last_message else None,
    )


def serialize_message(message: Message, sender_username: str | None = None) -> MessageRead:
    return MessageRead(
        id=message.id,
        chat_id=message.chat_id,
        sender_id=message.sender_id,
        client_message_id=message.client_message_id,
        sender_username=sender_username,
        text=message.text,
        created_at=message.created_at,
        updated_at=message.updated_at,
        deleted_at=message.deleted_at,
    )


@router.get("/", response_model=list[ChatListItem])
async def get_my_chats(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Chat, ChatMember)
        .join(ChatMember, ChatMember.chat_id == Chat.id)
        .where(
            ChatMember.user_id == current_user.id,
            ChatMember.left_at.is_(None),
        )
        .order_by(desc(Chat.id))
    )

    return [
        await serialize_chat_for_user(db, chat, membership, current_user.id)
        for chat, membership in result.all()
    ]


@router.post("/private", response_model=ChatDetail, status_code=status.HTTP_201_CREATED)
async def create_private_chat(
    chat_data: PrivateChatCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    recipient = await get_user_by_username(db, chat_data.recipient_username)
    if recipient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if recipient.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot create private chat with yourself",
        )

    chat = await get_private_chat_between_users(db, current_user.id, recipient.id)
    if chat is None:
        chat = Chat(chat_type=ChatType.private, created_by_id=current_user.id)
        db.add(chat)
        await db.flush()

        db.add_all(
            [
                ChatMember(chat_id=chat.id, user_id=current_user.id),
                ChatMember(chat_id=chat.id, user_id=recipient.id),
            ]
        )
        await db.flush()

    message = Message(
        chat_id=chat.id,
        sender_id=current_user.id,
        client_message_id=str(uuid4()),
        text=chat_data.first_message,
    )
    db.add(message)
    await db.commit()
    await db.refresh(chat)

    membership = await require_active_membership(db, chat.id, current_user.id)
    chat_item = await serialize_chat_for_user(db, chat, membership, current_user.id)
    return ChatDetail(
        **chat_item.model_dump(),
        created_by_id=chat.created_by_id,
        created_at=chat.created_at,
    )


@router.post("/group", response_model=ChatDetail, status_code=status.HTTP_201_CREATED)
async def create_group_chat(
    chat_data: GroupChatCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    usernames = {username.strip() for username in chat_data.member_usernames if username.strip()}
    usernames.discard(current_user.username)

    members: list[User] = []
    for username in sorted(usernames):
        user = await get_user_by_username(db, username)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{username}' not found",
            )
        members.append(user)

    chat = Chat(
        title=chat_data.title,
        chat_type=ChatType.group,
        created_by_id=current_user.id,
    )
    db.add(chat)
    await db.flush()

    db.add(ChatMember(chat_id=chat.id, user_id=current_user.id, is_admin=True))
    db.add_all(
        ChatMember(chat_id=chat.id, user_id=member.id)
        for member in members
    )
    db.add(
        Message(
            chat_id=chat.id,
            sender_id=current_user.id,
            client_message_id=str(uuid4()),
            text=chat_data.first_message,
        )
    )

    await db.commit()
    await db.refresh(chat)

    membership = await require_active_membership(db, chat.id, current_user.id)
    chat_item = await serialize_chat_for_user(db, chat, membership, current_user.id)
    return ChatDetail(
        **chat_item.model_dump(),
        created_by_id=chat.created_by_id,
        created_at=chat.created_at,
    )


@router.get("/{chat_id}", response_model=ChatDetail)
async def get_chat(
    chat_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    chat = await get_chat_or_404(db, chat_id)
    membership = await require_active_membership(db, chat_id, current_user.id)
    chat_item = await serialize_chat_for_user(db, chat, membership, current_user.id)
    return ChatDetail(
        **chat_item.model_dump(),
        created_by_id=chat.created_by_id,
        created_at=chat.created_at,
    )


@router.get("/{chat_id}/messages", response_model=list[MessageRead])
async def get_chat_messages(
    chat_id: int,
    limit: int = Query(default=15, ge=1, le=50),
    before_id: int | None = Query(default=None, ge=1),
    after_id: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    await require_active_membership(db, chat_id, current_user.id)
    if before_id is not None and after_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use either before_id or after_id, not both",
        )

    conditions = [
        Message.chat_id == chat_id,
        Message.deleted_at.is_(None),
    ]
    if before_id is not None:
        conditions.append(Message.id < before_id)
    if after_id is not None:
        conditions.append(Message.id > after_id)

    order_by = asc(Message.id) if after_id is not None else desc(Message.id)

    result = await db.execute(
        select(Message, User.username)
        .join(User, User.id == Message.sender_id)
        .where(*conditions)
        .order_by(order_by)
        .limit(limit)
    )

    rows = result.all() if after_id is not None else list(reversed(result.all()))
    return [serialize_message(message, username) for message, username in rows]


@router.get("/{chat_id}/messages/search", response_model=list[MessageRead])
async def search_chat_messages(
    chat_id: int,
    query: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    await require_active_membership(db, chat_id, current_user.id)

    result = await db.execute(
        select(Message, User.username)
        .join(User, User.id == Message.sender_id)
        .where(
            Message.chat_id == chat_id,
            Message.deleted_at.is_(None),
            Message.text.ilike(f"%{query}%"),
        )
        .order_by(desc(Message.id))
        .limit(limit)
    )

    return [
        serialize_message(message, username)
        for message, username in result.all()
    ]


@router.get("/{chat_id}/members", response_model=list[ChatMemberRead])
async def get_chat_members(
    chat_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    await require_active_membership(db, chat_id, current_user.id)

    result = await db.execute(
        select(ChatMember, User.username)
        .join(User, User.id == ChatMember.user_id)
        .where(
            ChatMember.chat_id == chat_id,
            ChatMember.left_at.is_(None),
        )
        .order_by(User.username)
    )

    return [
        ChatMemberRead(
            id=member.id,
            chat_id=member.chat_id,
            user_id=member.user_id,
            username=username,
            is_admin=member.is_admin,
            joined_at=member.joined_at,
            left_at=member.left_at,
        )
        for member, username in result.all()
    ]


@router.post("/{chat_id}/members", response_model=ChatMemberRead, status_code=status.HTTP_201_CREATED)
async def invite_chat_member(
    chat_id: int,
    body: ChatMemberInvite,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    chat = await get_chat_or_404(db, chat_id)
    await require_group_admin(db, chat, current_user.id)

    user = await get_user_by_username(db, body.username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    result = await db.scalars(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user.id,
        )
    )
    member = result.first()

    if member is not None and member.left_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this chat",
        )

    if member is None:
        member = ChatMember(chat_id=chat_id, user_id=user.id)
        db.add(member)
    else:
        member.left_at = None
        member.is_admin = False
        member.joined_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(member)

    return ChatMemberRead(
        id=member.id,
        chat_id=member.chat_id,
        user_id=member.user_id,
        username=user.username,
        is_admin=member.is_admin,
        joined_at=member.joined_at,
        left_at=member.left_at,
    )


@router.delete("/{chat_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_chat_member(
    chat_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    chat = await get_chat_or_404(db, chat_id)
    await require_group_admin(db, chat, current_user.id)

    member = await get_active_membership(db, chat_id, user_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    if member.is_admin:
        result = await db.scalars(
            select(func.count(ChatMember.id)).where(
                ChatMember.chat_id == chat_id,
                ChatMember.is_admin.is_(True),
                ChatMember.left_at.is_(None),
            )
        )
        admin_count = result.first() or 0
        if admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last chat admin",
            )

    member.left_at = datetime.now(timezone.utc)
    member.is_admin = False
    await db.commit()
    return None


@router.patch("/{chat_id}/members/{user_id}/admin", response_model=ChatMemberRead)
async def promote_chat_member_to_admin(
    chat_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    chat = await get_chat_or_404(db, chat_id)
    await require_group_admin(db, chat, current_user.id)

    member = await get_active_membership(db, chat_id, user_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    member.is_admin = True
    await db.commit()
    await db.refresh(member)

    user = await db.get(User, user_id)
    return ChatMemberRead(
        id=member.id,
        chat_id=member.chat_id,
        user_id=member.user_id,
        username=user.username if user else None,
        is_admin=member.is_admin,
        joined_at=member.joined_at,
        left_at=member.left_at,
    )
