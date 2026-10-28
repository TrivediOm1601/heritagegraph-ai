"""Alembic environment for the HeritageGraph AI schema.

URL resolution order (never fabricated):
  1. ``sqlalchemy.url`` set programmatically (``app.db.init_db`` or tests),
  2. the ``DATABASE_URL`` environment variable,
  3. otherwise a clear error — the migrations do not guess a database.
"""

from __future__ import annotations

import os
import pathlib
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make ``app`` importable no matter where alembic was invoked from
# (repo root, backend/, database/ — all work).
_BACKEND_DIR = str(pathlib.Path(__file__).resolve().parents[2] / "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.db.models import Base  # noqa: E402  (path bootstrap must come first)

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: migrating must not disable the
    # application's own loggers (they are asserted in tests).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The single source of truth for the schema: Module C's models.
target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL") or ""
    if not url.strip():
        raise RuntimeError(
            "No database URL configured. Set the DATABASE_URL environment "
            "variable or run migrations through `python -m app.db.init_db`."
        )
    # configparser interpolation: a literal '%' (e.g. in a password) must be doubled.
    return url.replace("%", "%%")


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live connection (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = config.attributes.get("connection", None)

    if connectable is None:
        configuration = config.get_section(config.config_ini_section, {})
        configuration["sqlalchemy.url"] = _database_url()
        engine = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
        with engine.connect() as connection:
            _run(connection)
        engine.dispose()
    else:
        _run(connectable)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Required for SQLite test databases (ALTER-heavy changes later on).
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
