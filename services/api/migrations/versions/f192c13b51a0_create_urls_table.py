"""create urls table

Revision ID: f192c13b51a0
Revises: 
Create Date: 2026-07-05 17:34:03.134309

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f192c13b51a0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "urls",
        sa.Column("short_code", sa.String(length=10), primary_key=True, nullable=False),
        sa.Column("long_url", sa.Text() , nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("user_id", sa.BigInteger, nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("urls")
