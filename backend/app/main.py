from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from contextlib import asynccontextmanager
from app.database import init_db
from app.config import settings
from app.security import require_api_key
from app.scheduler import start_scheduler, shutdown_scheduler, sync_seed_games_if_empty
from app.logging_setup import configure_logging, RequestLoggingMiddleware
from app.rate_limit import limiter
from app.observability import init_sentry, deep_health
from app.routers import games, history, materials

configure_logging(settings.LOG_LEVEL)
init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await sync_seed_games_if_empty()
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(title="SLG Research Platform", lifespan=lifespan)

# slowapi：把 limiter 挂到 app.state，并注册 429 异常处理器
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 顺序：RequestLoggingMiddleware 先于 CORSMiddleware 添加 → 实际执行时 CORS 在外、Logging 在内，
# 这样 OPTIONS 预检也会被打 access log（如果想忽略可以在 dispatch 里过滤）
app.add_middleware(RequestLoggingMiddleware)

_origins = settings.cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # 仅在白名单模式下允许 cookies；通配符不能与 credentials=True 同时启用
    allow_credentials=_origins != ["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization", "X-Request-ID"],
    expose_headers=["X-Total-Count", "X-Request-ID"],
)

_protected = [Depends(require_api_key)]
app.include_router(games.router, dependencies=_protected)
app.include_router(history.router, dependencies=_protected)
app.include_router(materials.router, dependencies=_protected)


@app.get("/api/health")
async def health():
    """轻量存活探针：永远返回 ok（用于 LB / Caddy / Docker healthcheck）。"""
    return {"status": "ok"}


@app.get("/api/health/deep", dependencies=_protected)
async def health_deep():
    """深度健康检查：DB 连通性 + Sensor Tower / Anthropic 可达性。"""
    return await deep_health()


@app.get("/api/cache/stats", dependencies=_protected)
async def cache_stats():
    from app.cache import sensor_tower_cache
    return {"sensor_tower": sensor_tower_cache.stats()}
