import logging
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from app.config import settings
from app.database import Base
# 注册所有 model 让 autogenerate 能识别表
from app.models import game, history, material  # noqa: F401

config = context.config

# 把异步驱动 URL 转成同步驱动（alembic 同步执行迁移）
def _sync_url(url: str) -> str:
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")

config.set_main_option("sqlalchemy.url", _sync_url(settings.DATABASE_URL))

# 仅在 alembic CLI 独立运行时按 alembic.ini 配日志。应用内 init_db() 跑迁移时
# app.main 已 import → configure_logging()/init_sentry() 已给 root 挂好 handler；
# 此处再 fileConfig（disable_existing_loggers 默认 True）会清掉应用的 JSON 日志
# **和 Sentry 的 LoggingIntegration handler**，导致生产日志与告警双双静默。
# 用「root 已有 handler」判别在进程内运行，跳过即可（CLI 时 root 为空，正常配）。
if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
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
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
