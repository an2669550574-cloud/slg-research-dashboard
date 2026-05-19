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

    # 排行榜接口 /v1/{os}/ranking 参数。本项目是 SLG 竞品监控：看「策略子类 ×
    # 畅销榜」才对得上目的（SLG 重收入重买量；全游戏×免费榜会全是休闲消除）。
    # iOS 7017 = App Store 游戏/策略子类；chart_type 畅销榜。Android 类目串。
    SENSOR_TOWER_RANKING_CHART_TYPE_IOS: str = "topgrossingapplications"
    SENSOR_TOWER_RANKING_CHART_TYPE_ANDROID: str = "topgrossing"
    SENSOR_TOWER_RANKING_CATEGORY_IOS: str = "7017"
    SENSOR_TOWER_RANKING_CATEGORY_ANDROID: str = "game_strategy"
    SENSOR_TOWER_RANKING_LIMIT: int = 100
    # Android 名字/图标靠抓 Google Play 商品页（无批量接口，一个包名一次请求）。
    # 只补前 N 个：榜尾对竞品监控无意义，限量压低耗时与封 IP 风险。
    SENSOR_TOWER_ANDROID_ENRICH_LIMIT: int = 60
    # 日榜前 N 名补真实下载/收入。sales_report_estimates 的 app_ids 支持逗号
    # 批量 → 一次调用拿全部 N 个，每市场每天仅 +1 次配额。0 = 关闭（日榜
    # 不显示下载收入，去详情页看）。
    SENSOR_TOWER_RANKING_SALES_TOPN: int = 20
    # SQLite 持久化快照"新鲜窗口"（小时）。内存缓存 miss 时若 SQLite 里已有
    # 不超过这个时长的快照，直接返回不消耗配额。设成跟 CACHE_TTL 一致即可。
    SENSOR_TOWER_SNAPSHOT_FRESH_HOURS: int = 24

    # 每日 scheduler 同步的 (country, platform) 组合。逗号分隔 "country:platform"。
    # 每组每天约 2 次月度配额(1 拉榜 + 1 批量销量)，注意 500/月 硬上限。
    # 6 组 ≈ 360/月核心同步；安卓也覆盖 US/JP/KR，与 iOS 对称（详情页两端
    # 都能切国家）。代价：配额吃紧，历史回填会更常被护栏跳过（设计内）。
    SYNC_RANKING_COMBOS: str = "US:ios,US:android,JP:ios,KR:ios,JP:android,KR:android"

    # ── 历史排名回填 ─────────────────────────────────────────────
    # ST 无"某 app 排名历史"接口；只能逐 (combo, 日期) 拉整张品类榜
    # (1 调用/日/组合)，从有序列表里读出名次。粒度用周(rank 长期趋势够看)：
    # 4 组合 × 52 周 ≈ 208 次一次性。每晚日常同步后涓流补，受配额护栏约束，
    # 永不挤占核心日同步；补完自动停（按 game_rankings 已有 rank 行判进度）。
    RANK_BACKFILL_ENABLED: bool = True
    RANK_BACKFILL_WEEKS: int = 52          # 回填多少周历史（每周采 1 个锚点日）
    RANK_BACKFILL_DAILY_BUDGET: int = 5    # 每晚最多消耗多少次配额做回填
    # 当月剩余配额 ≤ 此值则当晚跳过回填，把预算留给核心日同步/仪表盘。
    RANK_BACKFILL_QUOTA_FLOOR: int = 150
    RANK_BACKFILL_LIMIT: int = 400         # 每次拉榜深度（同 1 次配额，多捞深位）

    # 竞品异动告警：每日同步后比对 game_rankings 今日 vs 上一可用日，SLG 行
    # 里出现「新进 TopN / 大幅窜升 / 跌出 TopN / 收入异动」就汇总成一条
    # logger.error（经现有 LoggingIntegration 推送 Sentry，零额外配额/基建）。
    # 只在定时任务路径触发，手动刷新不告警，避免刷屏。
    COMPETITOR_ALERT_ENABLED: bool = True
    # 只关注 TopN 内的异动；榜尾对竞品监控无意义，且收入仅 Top20 有值。
    COMPETITOR_ALERT_TOPN: int = 20
    # 名次环比变化 ≥ 该值才算「窜升/暴跌」，过滤日常抖动。
    COMPETITOR_RANK_JUMP: int = 10
    # 收入环比 |变化%| ≥ 该值才报（两日都需有收入数据）。
    COMPETITOR_REVENUE_PCT: int = 50

    # 素材库上传文件落盘根目录。容器内走已挂载的 ./data:/app/data 卷，
    # 宿主机即 /opt/slg-research-dashboard/data/materials，与 DB 同一备份域。
    MEDIA_ROOT: str = "./data/materials"
    # 单文件大小上限（字节）。默认 200MB，覆盖绝大多数广告视频素材。
    MEDIA_MAX_BYTES: int = 200 * 1024 * 1024
    # 站内播放/预览签名 URL 有效期（秒）。<video src> 带不了请求头，故用
    # HMAC 短时令牌走 query string；列表每次刷新会重签，过期自然失效。
    MEDIA_URL_TTL_SECONDS: int = 6 * 3600

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
