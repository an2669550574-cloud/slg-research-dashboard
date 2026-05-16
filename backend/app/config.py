from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    SENSOR_TOWER_API_KEY: Optional[str] = None
    SENSOR_TOWER_BASE_URL: str = "https://api.sensortower.com"
    ANTHROPIC_API_KEY: Optional[str] = None
    DATABASE_URL: str = "sqlite+aiosqlite:///./slg_research.db"
    USE_MOCK_DATA: bool = True

    # 鉴权：留空时跳过校验（开发模式），生产环境必须设置
    API_KEY: Optional[str] = None
    # CORS 白名单：逗号分隔，留空或 "*" 则允许全部（开发模式）
    CORS_ORIGINS: str = "*"
    # 日志级别：DEBUG / INFO / WARNING / ERROR
    LOG_LEVEL: str = "INFO"

    # 限流：留空则禁用整体限流；格式为 "120/minute" / "1000/hour" 等 slowapi 表达式
    RATE_LIMIT_DEFAULT: Optional[str] = None
    # AI 历程同步是高成本端点，独立限制
    RATE_LIMIT_AI_SYNC: str = "10/hour"

    # Sensor Tower 内存级缓存 TTL（秒）。Sensor Tower 数据本身是 T+1 日级，
    # 缓存比源头还短就纯属浪费配额。默认 24 小时。
    SENSOR_TOWER_CACHE_TTL: int = 86400
    # 每月最多调用 Sensor Tower 真实 API 的次数。公司账号 3000/月共享，留 500 给本项目；
    # 超额后自动降级到 sensor_tower_snapshots 表里的最后一次成功响应。
    SENSOR_TOWER_MONTHLY_LIMIT: int = 500
    # 用量越过该百分比时打一条 ERROR（经 Sentry 推送），让维护者在配额耗尽
    # 前就收到主动告警，而不是等线上静默降级到过期快照才发现。
    SENSOR_TOWER_QUOTA_WARN_PCT: int = 80
    # SQLite 持久化快照"新鲜窗口"（小时）。内存缓存 miss 时若 SQLite 里已有
    # 不超过这个时长的快照，直接返回不消耗配额。设成跟 CACHE_TTL 一致即可。
    SENSOR_TOWER_SNAPSHOT_FRESH_HOURS: int = 24

    # 每日 scheduler 同步的 (country, platform) 组合。逗号分隔 "country:platform"。
    # 每组每天消耗 1 次月度配额，注意 500/月 ÷ 30 天 ≈ 16 组上限。
    # 默认 4 组覆盖 SLG 主要市场。
    SYNC_RANKING_COMBOS: str = "US:ios,US:android,JP:ios,KR:ios"

    # Sentry：留空时不上报。生产环境填入 DSN 即开启
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.05

    @property
    def cors_origin_list(self) -> list[str]:
        if not self.CORS_ORIGINS or self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def sync_combos_list(self) -> list[tuple[str, str]]:
        """解析 SYNC_RANKING_COMBOS 成 [(country, platform), ...]。

        坏数据（漏冒号、空 country）跳过并记日志，不要因为一个组合的拼写
        错误把整个 scheduler 拉垮。
        """
        import logging
        logger = logging.getLogger(__name__)
        out: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for raw in (self.SYNC_RANKING_COMBOS or "").split(","):
            raw = raw.strip()
            if not raw:
                continue
            if ":" not in raw:
                logger.warning("Skipping malformed sync combo %r (need country:platform)", raw)
                continue
            country, platform = raw.split(":", 1)
            country = country.strip().upper()
            platform = platform.strip().lower()
            if not country or platform not in ("ios", "android"):
                logger.warning("Skipping invalid sync combo %r", raw)
                continue
            key = (country, platform)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    class Config:
        env_file = ".env"

settings = Settings()
