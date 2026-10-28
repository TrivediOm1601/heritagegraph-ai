"""C1 — migration initializer: create or upgrade the database schema.

Usage::

    cd backend
    DATABASE_URL="postgresql://user:pass@host/heritage" python -m app.db.init_db
    DATABASE_URL="postgresql://user:pass@host/heritage" python -m app.db.init_db --seed

``--seed`` additionally loads the curated development records
(``database/seeds/historical_records.json``) through Module C's
idempotent ``seed_records`` — safe to re-run; existing rows are never
overwritten.

Tests drive the exact same code path against a temporary SQLite URL, so
"migration initialization" is verified rather than assumed.

Configuration rule (shared with B1): the URL is **never fabricated** —
without an explicit argument or ``DATABASE_URL`` this fails with
:class:`~app.core.config.ConfigurationError` instead of guessing a
database.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from ..core.config import ConfigurationError, get_settings

#: backend/ — this file lives at backend/app/db/init_db.py.
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
#: repository root, which holds database/ (sibling of backend/).
REPO_DIR = BACKEND_DIR.parent
ALEMBIC_INI = REPO_DIR / "database" / "alembic.ini"


def alembic_config(database_url: str) -> Config:
    """Build an Alembic config bound to an explicit database URL."""
    if not ALEMBIC_INI.is_file():
        raise ConfigurationError(f"Alembic config not found: {ALEMBIC_INI}")
    config = Config(str(ALEMBIC_INI))
    # '%' must be doubled: the URL passes through configparser interpolation
    # (percent-encoded passwords are legal).
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def init_database(database_url: str | None = None) -> str:
    """Run every pending migration; return the applied head revision id."""
    url = (database_url or get_settings().database_url or "").strip()
    if not url:
        raise ConfigurationError(
            "DATABASE_URL is not set — refusing to guess a database. "
            "Export DATABASE_URL (or pass one explicitly) before running migrations."
        )

    config = alembic_config(url)
    command.upgrade(config, "head")
    return ScriptDirectory.from_config(config).get_current_head()


def main() -> None:
    revision = init_database()
    print(f"schema migrated to head revision {revision}")

    if "--seed" in sys.argv[1:]:
        added = asyncio.run(_seed_development_records())
        print(f"historical seed: {added} new record(s) added (idempotent)")


async def _seed_development_records() -> int:
    """Load the curated development seed through Module C's repository."""
    from ..db.session import session_scope
    from ..repositories.record_repository import load_seed_records, seed_records

    async with session_scope() as session:
        return await seed_records(session, load_seed_records())


if __name__ == "__main__":
    main()
