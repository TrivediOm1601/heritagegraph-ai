"""R7 — accounts, sessions, analysis ownership and personal history.

Revision ID: 0005_users_sessions_history
Revises: 0004_analysis_briefing
Create Date: 2026-10-29

Adds the authentication and history layer:

* ``users`` — one row per account. Passwords are stored only as a salted
  PBKDF2-HMAC-SHA256 hash. The email is normalised (lowercased) and unique.
* ``user_sessions`` — server-side sessions keyed by the **SHA-256 digest**
  of the cookie token, with a hard expiry. Deleting the row logs the
  session out immediately.
* ``analysis_runs.user_id`` — nullable owner. Existing rows keep ``NULL``
  (no data is deleted or reassigned); such legacy rows are simply not
  exposed to any account.
* ``history_entries`` — one chronological list covering text searches and
  image analyses, owned by a user.
* The ``analysis_runs.status`` CHECK constraint gains ``partial``, so a run
  whose non-critical stage degraded can be recorded truthfully instead of
  being mislabelled ``completed``.

Everything is additive. ``downgrade`` drops only what this revision
created; it never touches pre-existing analysis data.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_users_sessions_history"
down_revision: Union[str, None] = "0004_analysis_briefing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ID = sa.String(128)

#: Lifecycle states after this revision (see app.schemas.analysis).
_ANALYSIS_STATES = ("queued", "processing", "completed", "partial", "failed")
_HISTORY_KINDS = ("text_search", "image_analysis")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", _ID, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", _ID, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    with op.batch_alter_table("analysis_runs") as batch:
        batch.add_column(sa.Column("user_id", _ID, nullable=True))
        batch.create_foreign_key(
            "fk_analysis_runs_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_analysis_runs_user_id", ["user_id"])
        # The status vocabulary gained 'partial'. Recreating the check is
        # required: the old constraint would reject the new state on
        # SQLite *and* PostgreSQL.
        batch.drop_constraint("ck_analysis_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_analysis_runs_status", f"status IN {_ANALYSIS_STATES!r}"
        )

    op.create_table(
        "history_entries",
        sa.Column("id", _ID, nullable=False),
        sa.Column("user_id", _ID, nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("query", sa.String(512), nullable=True),
        sa.Column("analysis_id", _ID, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["analysis_runs.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            f"kind IN {_HISTORY_KINDS!r}", name="ck_history_entries_kind"
        ),
        sa.CheckConstraint(
            f"status IN {_ANALYSIS_STATES!r}", name="ck_history_entries_status"
        ),
    )
    op.create_index(
        "ix_history_entries_user_created",
        "history_entries",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_history_entries_analysis_id", "history_entries", ["analysis_id"]
    )


def downgrade() -> None:
    """Remove only what this revision created (no user data is migrated)."""
    op.drop_index("ix_history_entries_analysis_id", table_name="history_entries")
    op.drop_index("ix_history_entries_user_created", table_name="history_entries")
    op.drop_table("history_entries")

    with op.batch_alter_table("analysis_runs") as batch:
        batch.drop_constraint("ck_analysis_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_analysis_runs_status",
            f"status IN {('queued', 'processing', 'completed', 'failed')!r}",
        )
        batch.drop_index("ix_analysis_runs_user_id")
        batch.drop_constraint("fk_analysis_runs_user_id_users", type_="foreignkey")
        batch.drop_column("user_id")

    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
