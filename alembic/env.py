import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Ensure project root is on sys.path so ``import src`` works regardless of
# how alembic is invoked (CLI, IDE, Docker, etc.)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import context
from sqlalchemy import pool, create_engine
from src.db.models import Base
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
def get_url():
    return os.environ.get("REGS_DATABASE_URL", config.get_main_option("sqlalchemy.url"))
def run_migrations_online():
    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
run_migrations_online()
