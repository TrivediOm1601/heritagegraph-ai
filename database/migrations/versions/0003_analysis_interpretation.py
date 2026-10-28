"""Repair R4 — persist the vision model's interpretation of the image.

Revision ID: 0003_analysis_interpretation
Revises: 0002_historical_record_url
Create Date: 2026-10-26

The vision adapter now requests a candidate reading (what is depicted,
possible heritage-site identity, visual clues, alternatives,
uncertainty). It must survive polling, so the run row stores it
verbatim. Purely additive and nullable: older rows and providers that
return no interpretation keep ``NULL``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_analysis_interpretation"
down_revision: Union[str, None] = "0002_historical_record_url"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("interpretation", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_runs", "interpretation")