"""
Alembic Migration Environment

Connects Alembic to SQLAlchemy models for auto-generated migrations.
Run: alembic revision --autogenerate -m "description"
     alembic upgrade head
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Load models so Alembic can detect schema changes
from instaflow.storage.models import Base

config = context.config

# Override sqlalchemy.url from env (so we don't hardcode credentials)
# Try environment variables first, fall back to config file values
postgres_user = os.environ.get('POSTGRES_USER', 'instaflow')
postgres_password = os.environ.get('POSTGRES_PASSWORD', 'instaflow_dev')
postgres_host = os.environ.get('POSTGRES_HOST', 'localhost')
postgres_port = os.environ.get('POSTGRES_PORT', '5432')
postgres_db = os.environ.get('POSTGRES_DB', 'instaflow')

pg_url = (
    f"postgresql+psycopg2://"
    f"{postgres_user}:{postgres_password}"
    f"@{postgres_host}:{postgres_port}/"
    f"{postgres_db}"
)
config.set_main_option("sqlalchemy.url", pg_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
