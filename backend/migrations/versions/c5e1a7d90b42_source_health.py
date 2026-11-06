"""source_health: salud por fuente de empleo

Revision ID: c5e1a7d90b42
Revises: 74c9a6c587a0
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'c5e1a7d90b42'
down_revision: Union[str, Sequence[str], None] = '74c9a6c587a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sourcehealth',
        sa.Column('source', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('last_found', sa.Integer(), nullable=False),
        sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('note', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('consecutive_zero', sa.Integer(), nullable=False),
        sa.Column('consecutive_errors', sa.Integer(), nullable=False),
        sa.Column('runs', sa.Integer(), nullable=False),
        sa.Column('last_latency_ms', sa.Integer(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_ok_at', sa.DateTime(), nullable=True),
        sa.Column('last_canary_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('source'),
    )


def downgrade() -> None:
    op.drop_table('sourcehealth')
