import asyncio
import pytest
from app.cache import InMemoryTTLCache


async def test_get_returns_none_for_missing():
    c = InMemoryTTLCache()
    assert await c.get("nope") is None


async def test_set_then_get():
    c = InMemoryTTLCache()
    await c.set("k", {"v": 1}, ttl_seconds=10)
    assert await c.get("k") == {"v": 1}


async def test_expired_entry_returns_none():
    c = InMemoryTTLCache()
    await c.set("k", "v", ttl_seconds=0.01)
    await asyncio.sleep(0.05)
    assert await c.get("k") is None


async def test_invalidate_removes_entry():
    c = InMemoryTTLCache()
    await c.set("k", "v", ttl_seconds=10)
    await c.invalidate("k")
    assert await c.get("k") is None


async def test_get_or_set_calls_loader_only_on_miss():
    c = InMemoryTTLCache()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return "fresh"

    v1 = await c.get_or_set("k", 10, loader)
    v2 = await c.get_or_set("k", 10, loader)
    assert v1 == v2 == "fresh"
    assert calls == 1, "loader must run exactly once across two reads"


async def test_get_or_set_single_flight_under_concurrency():
    """同 key 并发触发时 loader 只运行一次（防 thundering herd）。"""
    c = InMemoryTTLCache()
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_loader():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return calls

    # 同时发起 5 个请求；loader 一开始就 set started 然后阻塞
    task1 = asyncio.create_task(c.get_or_set("k", 10, slow_loader))
    await started.wait()
    others = [asyncio.create_task(c.get_or_set("k", 10, slow_loader)) for _ in range(4)]

    # 让 loader 完成
    release.set()
    results = await asyncio.gather(task1, *others)

    assert calls == 1, "loader must run exactly once even under 5 concurrent reads"
    assert results == [1, 1, 1, 1, 1]


async def test_get_or_set_propagates_loader_exception():
    c = InMemoryTTLCache()

    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await c.get_or_set("k", 10, boom)

    # 异常之后缓存不应该有残留 inflight，下次调用能重新跑
    async def ok():
        return "ok"
    assert await c.get_or_set("k", 10, ok) == "ok"


async def test_stats_counts_entries():
    c = InMemoryTTLCache()
    await c.set("a", 1, 10)
    await c.set("b", 2, 0.01)
    await asyncio.sleep(0.05)
    stats = c.stats()
    assert stats["entries"] == 2
    assert stats["live"] == 1
    assert stats["inflight"] == 0
