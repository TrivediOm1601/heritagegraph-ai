"""India Heritage Explorer — national metadata on ``heritage_sites``.

Revision ID: 0007_india_heritage
Revises: 0006_archive_studio
Create Date: 2026-10-30

The curated map dataset grows from five Gujarat sites to a representative
India-wide set covering every state and Union Territory. The existing
``heritage_sites`` row is extended rather than replaced:

* macro-region is stored in the existing ``region`` column (the six-region
  map is documented in ``app/services/heritage_service.py``; the seed is
  validated against it, so a record cannot claim a mismatched region);
* ``period_group`` is the coarse filter bucket, kept separate from the
  free-text ``period`` so "c. 1026-27 CE" is never flattened into a year;
* ``alternative_names`` and ``source_refs`` are JSON lists (names and
  multiple source references), matching the existing JSON-column style;
* ``district`` / ``city`` / ``architectural_style`` / ``is_union_territory``
  / ``unesco_status`` / ``unesco_year`` complete the display and filter
  fields.

Everything is additive; ``downgrade`` drops only what this revision created.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_india_heritage"
down_revision: Union[str, None] = "0006_archive_studio"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("heritage_sites", sa.Column("alternative_names", sa.JSON(), nullable=True))
    op.add_column("heritage_sites", sa.Column("district", sa.String(120), nullable=True))
    op.add_column("heritage_sites", sa.Column("city", sa.String(120), nullable=True))
    op.add_column(
        "heritage_sites", sa.Column("architectural_style", sa.String(160), nullable=True)
    )
    op.add_column(
        "heritage_sites", sa.Column("period_group", sa.String(32), nullable=True)
    )
    op.add_column(
        "heritage_sites",
        sa.Column(
            "is_union_territory",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "heritage_sites",
        sa.Column(
            "unesco_status", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("heritage_sites", sa.Column("unesco_year", sa.Integer(), nullable=True))
    op.add_column("heritage_sites", sa.Column("source_refs", sa.JSON(), nullable=True))

    op.create_index("ix_heritage_sites_state", "heritage_sites", ["state"])
    op.create_index("ix_heritage_sites_period_group", "heritage_sites", ["period_group"])
    op.create_index("ix_heritage_sites_unesco", "heritage_sites", ["unesco_status"])


def downgrade() -> None:
    op.drop_index("ix_heritage_sites_unesco", table_name="heritage_sites")
    op.drop_index("ix_heritage_sites_period_group", table_name="heritage_sites")
    op.drop_index("ix_heritage_sites_state", table_name="heritage_sites")

    op.drop_column("heritage_sites", "source_refs")
    op.drop_column("heritage_sites", "unesco_year")
    op.drop_column("heritage_sites", "unesco_status")
    op.drop_column("heritage_sites", "is_union_territory")
    op.drop_column("heritage_sites", "period_group")
    op.drop_column("heritage_sites", "architectural_style")
    op.drop_column("heritage_sites", "city")
    op.drop_column("heritage_sites", "district")
    op.drop_column("heritage_sites", "alternative_names")
