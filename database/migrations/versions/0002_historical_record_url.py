"""Repair R3 — persist the cited source URL of historical records.

Revision ID: 0002_historical_record_url
Revises: 0001_initial_schema
Create Date: 2026-10-26

The curation charter requires every record to cite a source URL, and the
seed loader already validates one — but the original schema had no column
to store it, so every URL was dropped at persistence and neither
``HistoricalMatch.url`` nor ``EvidenceItem.url`` could ever be populated.

Purely additive and nullable: existing rows (and records that genuinely
have no URL) keep ``NULL``; nothing is invented or backfilled.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_historical_record_url"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "historical_records",
        sa.Column("url", sa.String(1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("historical_records", "url")