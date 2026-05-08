async def test_no_api_key_required_when_unset(client):
    # 默认 conftest 把 API_KEY 设为空，应放行
    r = await client.get("/api/games/")
    assert r.status_code == 200


async def test_api_key_enforced_when_set(monkeypatch, tmp_db_url):
    """单独构造 app 让 API_KEY 生效，验证 401 与 200 两条路径。"""
    monkeypatch.setenv("API_KEY", "secret-key")
    monkeypatch.setenv("DATABASE_URL", tmp_db_url)

    import importlib, sys
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]

    from app import database, scheduler
    from app.main import app as fastapi_app

    async with database.engine.begin() as conn:
        from app.models import game, history, material  # noqa
        await conn.run_sync(database.Base.metadata.create_all)
    monkeypatch.setattr(database, "init_db", _noop)
    monkeypatch.setattr(scheduler, "start_scheduler", lambda: None)
    monkeypatch.setattr(scheduler, "shutdown_scheduler", lambda: None)
    monkeypatch.setattr(scheduler, "sync_seed_games_if_empty", _noop)

    from httpx import AsyncClient, ASGITransport
    from asgi_lifespan import LifespanManager

    async with LifespanManager(fastapi_app):
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as c:
            no_key = await c.get("/api/games/")
            assert no_key.status_code == 401

            wrong_key = await c.get("/api/games/", headers={"X-API-Key": "wrong"})
            assert wrong_key.status_code == 401

            good_key = await c.get("/api/games/", headers={"X-API-Key": "secret-key"})
            assert good_key.status_code == 200

            # health 端点未挂依赖，应一直放行
            health = await c.get("/api/health")
            assert health.status_code == 200

    await database.engine.dispose()
    importlib.invalidate_caches()


async def _noop(*_a, **_k):
    return None
