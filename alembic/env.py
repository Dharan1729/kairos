import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# kairos/models.py and kairos/directory.py both register tables on the
# same Base.metadata (kairos/db.py) — importing both, not just one, is
# required for autogenerate to see the full schema.
from kairos import directory, models  # noqa: E402,F401
from kairos.db import Base  # noqa: E402

target_metadata = Base.metadata

from kairos.config import load_settings  # noqa: E402

# Same database the app uses: KAIROS_DATABASE_URL, else $KAIROS_HOME/kairos.db.
_url = load_settings().database_url
if _url.startswith("sqlite:///") and _url != "sqlite:///:memory:":
    os.makedirs(os.path.dirname(os.path.abspath(_url.removeprefix("sqlite:///"))), exist_ok=True)
config.set_main_option("sqlalchemy.url", _url)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
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
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
