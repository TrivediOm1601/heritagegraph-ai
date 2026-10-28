"""Repair R5 — persist the grounded historical briefing of one analysis.

Revision ID: 0004_analysis_briefing
Revises: 0003_analysis_interpretation
Create Date: 2026-10-27

An analysis now carries a historical explanation built from the sources
retrieved for the identified entities: sentences quoted from those
sources with citation indexes. It must survive polling *and* a restart,
so the run row stores the briefing JSON verbatim instead of re-fetching
every source on each GET.

Purely additive and nullable: existing rows and runs whose retrieval
found nothing keep ``NULL``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_analysis_briefing"
down_revision: Union[str, None] = "0003_analysis_interpretation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("briefing", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_runs", "briefing")
