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
    # SQLite 持久化快照"新鲜窗口"（小时）。内存缓存 miss 时若 SQLite 里已有
    # 不超过这个时长的快照，直接返回不消耗配额。设成跟 CACHE_TTL 一致即可。
    SENSOR_TOWER_SNAPSHOT_FRESH_HOURS: int = 24

    # Sentry：留空时不上报。生产环境填入 DSN 即开启
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.05

    @property
    def cors_origin_list(self) -> list[str]:
        if not self.CORS_ORIGINS or self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    class Config:
        env_file = ".env"

settings = Settings()
