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
    # 每月最多调用 Sensor Tower 真实 API 的次数（硬上限）。公司账号 3000/月共享。
    # 配额分级后稳态自动同步 ≈ 39/月，留余量给手动刷新/详情页按需取 → 设 50 封顶。
    # 超额后自动降级到 sensor_tower_snapshots 表里的最后一次成功响应（不报错、不断站）。
    # 注意：此值低于 RANK_BACKFILL_QUOTA_FLOOR(150) → 历史回填默认被该上限挡停
    #（设计内：回填是一次性活，补完即可停；要补未完的历史临时把本值调高再跑）。
    SENSOR_TOWER_MONTHLY_LIMIT: int = 50
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

    # 拉 ST /v1/api_usage（公司账户级用量）的缓存窗口。⚠️ 本接口每次大概率让
    # 服务端 organization.usage +1 且**绕过本地月度计数**（裸 httpx，见 quota
    # ._fetch_account_usage_live）——它是隐形的公司池消费者，不受 MONTHLY_LIMIT
    # 约束，只能靠这个 TTL 限频。本项目自身用量本地实时计数、无需靠它；它只为
    # 看「跨团队公司池水位」。故设 7 天：≈4 次/月，周级刷新够用，几乎不吃池。
    SENSOR_TOWER_ACCOUNT_USAGE_TTL_HOURS: int = 168
    # 公司账户池软预留：池剩余 ≤ 此值时本项目 try_consume 主动返 False，让出最后
    # 几次给其他团队（避免我们一夜把池子拼光，导致他们的业务全断）。3000 池里留
    # 30 缓冲，本项目日均同步约 12 次，相当于 2~3 天的安全余量。设 0 = 仅硬限。
    SENSOR_TOWER_ORG_RESERVE: int = 30
    # 公司账户池"黄色警示"门槛：池剩余 ≤ 此值时前端弹全局黄条提示用户。比
    # ORG_RESERVE 高一档：给操作者预警窗口，"快没了"≠"已主动停"。
    SENSOR_TOWER_ORG_LOW_THRESHOLD: int = 100

    # 需要同步的 (country, platform) 组合全集。逗号分隔 "country:platform"。
    # 安卓也覆盖 US/JP/KR，与 iOS 对称（详情页两端都能切国家）。
    # 实际月配额由下方"配额分级"压低：主市场每日全量、次市场隔日、销量周级解耦
    # → 6 组从约 360/月降到约 140/月（详见 SYNC_RANKING_COMBOS_PRIMARY 等）。
    SYNC_RANKING_COMBOS: str = "US:ios,US:android,JP:ios,KR:ios,JP:android,KR:android"

    # ── 配额分级（主/次市场 + 销量解耦）────────────────────────────
    # 目标：稳态 ≤50 次/月（公司池 3000 共享）。三个间隔旋钮按 UTC 日序号取模
    # 判定"今天是否到点"，纯函数、跨重启/多副本一致，无需持久化游标。
    #
    # SYNC_RANKING_COMBOS_PRIMARY: 主市场 combo（用 PRIMARY 间隔，可调得比次市场勤）。
    # 不在此列但在 SYNC_RANKING_COMBOS 内的 = 次市场（用 SECONDARY 间隔）。
    SYNC_RANKING_COMBOS_PRIMARY: str = "US:ios,US:android"
    # 主/次市场拉榜间隔（天）。1=每天，2=隔日，7=每周。默认都按周——rank 长期
    # 趋势走本地库读，周级足够；6 组 × 周级 ≈ 26/月拉榜。要 US 更勤就把 PRIMARY 调小。
    SYNC_PRIMARY_INTERVAL_DAYS: int = 7
    SYNC_SECONDARY_INTERVAL_DAYS: int = 7
    # 日榜销量(下载/收入)抓取间隔（天）。ST 销量估算本身 T-1/T-2、日间波动小，
    # 双周刷新对竞品研究足够。非抓取日榜行 dl/rev 落 NULL（库内诚实），日榜
    # 读路径用该 app 上次已知值兜底显示，详情页趋势自然退化成稀疏数据点。
    # 默认 14：与周级拉榜相交后 ≈ 13/月销量调用。1 = 每天抓（等于不解耦）。
    SALES_FETCH_INTERVAL_DAYS: int = 14

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

    # ── 太石 LLM 网关 ────────────────────────────────────────────
    # 公司统一大模型网关，OpenAI 兼容协议。审批通过后钉钉发 key。
    # 不直连 Anthropic/OpenAI（合规）。文档：reference_taishi_gateway memory。
    TAISHI_API_KEY: Optional[str] = None
    TAISHI_BASE_URL: str = "https://relay.tuyoo.com/v1"
    # 视频/图片分析用：视觉模型。Claude sonnet/opus 与 Gemini 系列支持图，
    # GLM 系列只支持 text，不能用于素材帧分析。
    TAISHI_VISION_MODEL: str = "claude-sonnet-4.5"
    # 单次调用超时（秒）。视频分析单次 8-12 帧 + 长 prompt，宽点。
    TAISHI_TIMEOUT_SECONDS: int = 120
    # 日成本软上限（美元）。超过则 /analyze 端点拒绝新请求，防止失控烧钱。
    # 太石账号本身有 $50/天/人硬上限，本项目护栏建议设低于此值。
    LLM_DAILY_BUDGET_USD: float = 20.0

    # ── 素材视频分析 ──────────────────────────────────────────────
    # 单视频抽取多少关键帧送给模型。8~12 是 sonnet 视觉模型的甜区：太少
    # 漏关键场景，太多 token 成本陡升且 LLM 注意力分散。
    MATERIAL_ANALYZE_FRAMES: int = 10
    # 每帧降采样最长边（像素）。Claude vision 推荐 ≤ 1568px；超过会被自动
    # 缩放且不省钱。1280 兼顾清晰度和上传体积。
    MATERIAL_ANALYZE_FRAME_MAX_DIM: int = 1280

    # Sentry：留空时不上报。生产环境填入 DSN 即开启
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.05

    @property
    def cors_origin_list(self) -> list[str]:
        if not self.CORS_ORIGINS or self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @staticmethod
    def _parse_combos(raw_str: str) -> list[tuple[str, str]]:
        """解析逗号分隔的 "country:platform" 串成 [(country, platform), ...]。

        坏数据（漏冒号、空 country、非法平台）跳过并记日志，不要因为一个
        组合的拼写错误把整个 scheduler 拉垮。结果去重、保序。
        """
        import logging
        logger = logging.getLogger(__name__)
        out: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for raw in (raw_str or "").split(","):
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

    @property
    def sync_combos_list(self) -> list[tuple[str, str]]:
        """解析 SYNC_RANKING_COMBOS 成 [(country, platform), ...]（全部需同步的 combo）。"""
        return self._parse_combos(self.SYNC_RANKING_COMBOS)

    @property
    def sync_primary_combos_set(self) -> set[tuple[str, str]]:
        """主市场 combo 集合（每日全量同步）。SYNC_RANKING_COMBOS_PRIMARY 解析而来。
        只取与 SYNC_RANKING_COMBOS 的交集——主市场配错也不会凭空多同步一个 combo。"""
        primary = set(self._parse_combos(self.SYNC_RANKING_COMBOS_PRIMARY))
        return primary & set(self.sync_combos_list)

    class Config:
        env_file = ".env"

settings = Settings()
