"""轻量缓存抽象 + 内存 TTL 实现。

设计：
- AsyncCache 是 Protocol，业务代码只依赖它
- InMemoryTTLCache 是单进程默认实现（协程安全 + single-flight）
- 多副本部署需要替换为 Redis，可以加一个 RedisCache 实现同 Protocol
"""
import asyncio
import time
from typing import Any, Awaitable, Callable, Optional, Protocol


class AsyncCache(Protocol):
    """所有缓存实现需要满足的协议。"""
    async def get(self, key: str) -> Optional[Any]: ...
    async def set(self, key: str, value: Any, ttl_seconds: float) -> None: ...
    async def invalidate(self, key: str) -> None: ...
    async def invalidate_matching(self, predicate: Callable[[str], bool]) -> int: ...
    async def get_or_set(self, key: str, ttl_seconds: float, loader: Callable[[], Awaitable[Any]]) -> Any: ...
    def stats(self) -> dict[str, int]: ...


class InMemoryTTLCache:
    """进程内 TTL 缓存。

    线程/协程安全；带 single-flight：缓存未命中时同一 key 的并发请求只调用 loader 一次。
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Future] = {}

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at < time.time():
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        async with self._lock:
            self._store[key] = (time.time() + ttl_seconds, value)

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def invalidate_matching(self, predicate: Callable[[str], bool]) -> int:
        """清除所有满足 predicate 的 key，返回清除条数。L2 snapshot 不受影响。"""
        async with self._lock:
            victims = [k for k in self._store if predicate(k)]
            for k in victims:
                self._store.pop(k, None)
            return len(victims)

    async def get_or_set(self, key: str, ttl_seconds: float, loader: Callable[[], Awaitable[Any]]) -> Any:
        cached = await self.get(key)
        if cached is not None:
            return cached

        async with self._lock:
            inflight = self._inflight.get(key)
            if inflight is None:
                inflight = asyncio.get_running_loop().create_future()
                self._inflight[key] = inflight
                spawn_loader = True
            else:
                spawn_loader = False

        if spawn_loader:
            try:
                value = await loader()
                await self.set(key, value, ttl_seconds)
                inflight.set_result(value)
                return value
            except Exception as e:
                inflight.set_exception(e)
                raise
            finally:
                async with self._lock:
                    self._inflight.pop(key, None)
        else:
            return await inflight

    def stats(self) -> dict[str, int]:
        now = time.time()
        live = sum(1 for exp, _ in self._store.values() if exp >= now)
        return {"entries": len(self._store), "live": live, "inflight": len(self._inflight)}


# 默认导出供业务侧使用；切换到 Redis 时改这里即可
sensor_tower_cache: AsyncCache = InMemoryTTLCache()


# 保留旧名以避免外部 import 破坏
TTLCache = InMemoryTTLCache
