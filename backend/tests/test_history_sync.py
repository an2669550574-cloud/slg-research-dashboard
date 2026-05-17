"""POST /api/history/sync/{app_id}。conftest 重载 app.* —— import 放函数内。

回归点：slowapi(headers_enabled=True) 在 handler 后注入 X-RateLimit-* 时
按名字找 `response: Response` 参数；缺它会抛 500，AI 兜底逻辑都没机会跑。
测试环境 RATE_LIMIT_DEFAULT 未设 → limiter 关闭，复现不了 500，故再加一条
签名守卫精确锁这个坑。
"""
import inspect
import pytest


@pytest.mark.asyncio
async def test_sync_writes_default_history_for_untracked_game(client):
    """ANTHROPIC_API_KEY 为空（测试默认）→ generate_history 走 DEFAULT_HISTORY。
    游戏不在 games 表也不应 500（name 回退用 app_id）。"""
    r = await client.post("/api/history/sync/6477682303")
    assert r.status_code == 200, r.text
    assert "条历程数据" in r.json()["message"]

    got = await client.get("/api/history/6477682303")
    assert got.status_code == 200
    events = got.json()
    assert len(events) >= 1
    assert all(e["source"] == "ai" for e in events)


@pytest.mark.asyncio
async def test_sync_preserves_manual_events(client):
    """AI 重新同步只清 source!=manual，手动录入保留。"""
    await client.post("/api/history/", json={
        "app_id": "appX", "event_date": "2024-01-01", "event_type": "version",
        "title": "手动节点", "description": "x", "source": "manual",
    })
    await client.post("/api/history/sync/appX")
    events = (await client.get("/api/history/appX")).json()
    assert any(e["source"] == "manual" and e["title"] == "手动节点" for e in events)
    assert any(e["source"] == "ai" for e in events)


def test_sync_history_has_response_param_for_slowapi():
    """slowapi headers_enabled 要求 handler 暴露名为 response 的 Response 参数。"""
    from fastapi import Response
    from app.routers.history import sync_history
    sig = inspect.signature(sync_history)
    assert "response" in sig.parameters, "缺 response 参数 → slowapi 注入头时会 500"
    assert sig.parameters["response"].annotation is Response
