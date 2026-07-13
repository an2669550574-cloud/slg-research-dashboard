from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from alembic import command
from alembic.config import Config
from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# SQLite 并发护栏：scheduler 是同进程 AsyncIOScheduler（见 scheduler.py），多个同步 job
# 与 API 读写共用这一个 engine。默认 journal_mode=delete 下写者独占、读者被踢，且无
# busy_timeout 时锁冲突立即抛 SQLITE_BUSY——03:00 UTC digest 重读若与某同步 job 写入交叠
# 就可能吞掉当日领导卡。WAL 让读写并发（单写多读）、busy_timeout 让锁冲突等待而非立即失败。
# 仅对 SQLite 生效（dialect 判断），对 :memory: 测试库 WAL 静默降级为 memory、无副作用。
# 不动 foreign_keys（默认 OFF）：那是数据完整性语义，与本护栏正交，改动需单独验证测试基线。
@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _connection_record) -> None:
    if engine.dialect.name != "sqlite":
        return
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


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
