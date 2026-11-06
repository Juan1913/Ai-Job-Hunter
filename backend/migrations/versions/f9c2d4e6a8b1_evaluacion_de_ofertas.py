"""evaluacion de ofertas: JobRow.evaluation (informe JSON con puntaje 1-5, vetos y dimensiones)

Revision ID: f9c2d4e6a8b1
Revises: e8b3f5a17c26
Create Date: 2026-11-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9c2d4e6a8b1'
down_revision: Union[str, Sequence[str], None] = 'e8b3f5a17c26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('jobrow') as batch:
        batch.add_column(sa.Column('evaluation', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('jobrow') as batch:
        batch.drop_column('evaluation')
