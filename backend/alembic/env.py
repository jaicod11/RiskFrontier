"""Alembic environment: pulls the DB URL and metadata from the app itself."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import _normalise_driver, settings
from app.models import Base  # noqa: F401  (imports every model onto Base.metadata)


def migration_url() -> str:
    """Connection string for migrations.

    Prefers DATABASE_URL_UNPOOLED: Neon's pooled endpoint uses transaction
    pooling, which does not preserve the session state Alembic needs. Falls
    back to DATABASE_URL, which is what local development uses — docker-compose
    runs a single Postgres with no pooling distinction, so no extra config.
    """
    if settings.database_url_unpooled:
        return _normalise_driver(settings.database_url_unpooled)
    return settings.sqlalchemy_database_uri


config = context.config
config.set_main_option("sqlalchemy.url", migration_url())

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
