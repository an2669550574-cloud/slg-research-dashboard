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

    # Sensor Tower 真实接口缓存 TTL（秒）。榜单变化频率不高，半小时已足够新鲜
    SENSOR_TOWER_CACHE_TTL: int = 1800

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
