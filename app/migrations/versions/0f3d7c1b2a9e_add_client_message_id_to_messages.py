"""Add client_message_id to messages

Revision ID: 0f3d7c1b2a9e
Revises: bbd3a0836a3a
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0f3d7c1b2a9e"
down_revision: Union[str, Sequence[str], None] = "bbd3a0836a3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "messages",
        sa.Column("client_message_id", sa.String(length=36), nullable=True),
    )
    op.create_unique_constraint(
        "uq_messages_sender_id_client_message_id",
        "messages",
        ["sender_id", "client_message_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_messages_sender_id_client_message_id",
        "messages",
        type_="unique",
    )
    op.drop_column("messages", "client_message_id")
