from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from alembic import command
from alembic.config import Config
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def utcnow_naive() -> datetime:
    """Naive-UTC datetime for SQLAlchemy DateTime defaults.

    Python 3.12 deprecated datetime.utcnow(). Columns here are DateTime (not
    DateTime(timezone=True)), so we strip tzinfo to keep storage naive-UTC.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """启动时通过 Alembic 把数据库 schema 升级到 head。"""
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    command.upgrade(cfg, "head")
