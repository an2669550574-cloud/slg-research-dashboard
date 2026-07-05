"""测试夹具：每个测试用独立 SQLite 文件，绕过 alembic（直接用 SQLAlchemy 建表）。"""
import os
import pathlib
import pytest
import pytest_asyncio

# 在 import 应用之前，重置环境变量到测试隔离值
os.environ["USE_MOCK_DATA"] = "true"
os.environ["API_KEY"] = ""
os.environ["CORS_ORIGINS"] = "*"
os.environ["SENSOR_TOWER_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
# 本地 backend/.env 配了真实看板址时，digest 会拼「看板定位」深链，污染不 patch
# 该项的用例（test_newcomer_log）。统一清空给确定基线；需测深链的用例自行 monkeypatch。
os.environ["DASHBOARD_BASE_URL"] = ""
# 钉钉 webhook：本地 backend/.env 若配了真 webhook，测试触发的告警（如 LLM 预算触顶）
# 会真发到运维群（偶发失败源）。测试统一清空 → dingtalk.send_* no-op；需测发送的
# 用例自行 monkeypatch settings（test_dingtalk_alerts 即如此）。
os.environ["DINGTALK_WEBHOOK_URL"] = ""
os.environ["DINGTALK_SECRET"] = ""
os.environ["DINGTALK_WEBHOOK_URL_LEADER"] = ""
os.environ["DINGTALK_SECRET_LEADER"] = ""


@pytest.fixture(autouse=True)
def _reset_slg_index():
    """每个测试前把 slg_publishers 内存索引重置回种子态。

    is_slg 查的是模块级全局索引；publisher 写端点 / load_index_from_db 会就地
    覆盖它且不还原，导致跨文件污染（如 test_newcomers 跑后 test_slg_publishers
    全红，但各文件单独跑都绿）。在此统一给每个用例一个确定的种子基线。
    import 放函数内：app fixture 会清 sys.modules 重导入模块，必须拿当前实例。"""
    from app.services import slg_publishers
    slg_publishers.reset_index_to_seed()
    yield


@pytest.fixture
def tmp_db_url(tmp_path: pathlib.Path) -> str:
    db_file = tmp_path / "test.db"
    return f"sqlite+aiosqlite:///{db_file}"


@pytest_asyncio.fixture
async def app(tmp_db_url, monkeypatch):
    """每个测试装载一个全新的 app（独立 DB、不跑 alembic、不启动 scheduler）。"""
    monkeypatch.setenv("DATABASE_URL", tmp_db_url)

    # 把已被缓存的应用模块清理掉，确保用新的 DATABASE_URL 重新初始化 engine
    import importlib
    import sys
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]

    from app import database, scheduler
    from app import main as app_main
    fastapi_app = app_main.app

    # 用 SQLAlchemy 直接建表（更轻量；alembic 已在另外的迁移单测覆盖）
    async with database.engine.begin() as conn:
        from app.models import game, history, material, product, quota  # noqa: F401
        await conn.run_sync(database.Base.metadata.create_all)

    # 屏蔽 lifespan 中的 alembic 调用与 scheduler 启动。
    # 注意：main.py 用 `from app.database import init_db` 把名字绑到自己 namespace，
    # 所以必须 patch app.main.* 而不是 app.database.*，否则 monkeypatch 不影响调用点。
    monkeypatch.setattr(app_main, "init_db", _noop_async)
    monkeypatch.setattr(app_main, "start_scheduler", lambda: None)
    monkeypatch.setattr(app_main, "shutdown_scheduler", lambda: None)
    monkeypatch.setattr(app_main, "sync_seed_games_if_empty", _noop_async)
    monkeypatch.setattr(app_main, "seed_tag_dimensions_if_empty", _noop_async)
    monkeypatch.setattr(app_main, "seed_publishers_if_empty", _noop_async)
    monkeypatch.setattr(app_main, "seed_wechat_accounts_if_empty", _noop_async)

    yield fastapi_app

    await database.engine.dispose()
    importlib.invalidate_caches()


async def _noop_async(*_args, **_kwargs):
    return None


@pytest_asyncio.fixture
async def client(app):
    from httpx import AsyncClient, ASGITransport
    from asgi_lifespan import LifespanManager

    async with LifespanManager(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
