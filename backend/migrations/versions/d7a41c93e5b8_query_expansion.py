"""query_expansion: caché de la expansión de consulta por LLM (app/scout/query.py)

Revision ID: d7a41c93e5b8
Revises: a15374d5a60f
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = 'd7a41c93e5b8'
down_revision: Union[str, Sequence[str], None] = 'a15374d5a60f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'queryexpansion',
        sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('keywords', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('variants', sa.JSON(), nullable=True),
        sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('queryexpansion')
