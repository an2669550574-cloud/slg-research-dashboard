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
    # 标签库「删除」专用管理员口令。看板是单把 API_KEY 共享、无用户体系，故用一道
    # 独立口令挡住误删一级/二级标签（连带丢失已打标记）。留空 → 跳过校验（开发模式，
    # 与 API_KEY 同款语义）；生产环境想要"仅管理员可删"必须设置。不进前端构建，
    # 仅运行时由前端弹框收集、走 X-Admin-Password 头发来后端比对。
    ADMIN_DELETE_PASSWORD: Optional[str] = None
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
    # 配额分级后稳态自动同步 ≈ 19/月（US 周级 + JP/KR 月级 + 销量仅主市场双周），
    # 留余量给手动刷新/详情页按需取 → 设 50 封顶。
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

    # 拉 ST /v1/api_usage（公司账户级用量）的缓存窗口。2026-06-11 实锤：本接口
    # **不计公司池**（连打两次 org.usage 纹丝不动；同窗口 featured/impacts 每次
    # +1 形成对照）——此前按「大概率 +1」保守设 336h，副作用是月初重置后拉到的
    # 0/3000 快照"新鲜"半个月，仪表盘公司池水位整月冻结看不见真实消耗。刷新
    # 既然免费，小时级即可：水位及时、TTL 仍挡住前端轮询打爆 ST。
    SENSOR_TOWER_ACCOUNT_USAGE_TTL_HOURS: int = 1
    # 公司账户池软预留：池剩余 ≤ 此值时本项目 try_consume 主动返 False，让出最后
    # 几次给其他团队（避免我们一夜把池子拼光，导致他们的业务全断）。3000 池里留
    # 30 缓冲，本项目日均同步约 12 次，相当于 2~3 天的安全余量。设 0 = 仅硬限。
    SENSOR_TOWER_ORG_RESERVE: int = 30
    # 公司账户池"黄色警示"门槛：池剩余 ≤ 此值时前端弹全局黄条提示用户。比
    # ORG_RESERVE 高一档：给操作者预警窗口，"快没了"≠"已主动停"。
    SENSOR_TOWER_ORG_LOW_THRESHOLD: int = 100

    # 需要同步的 (country, platform) 组合全集。逗号分隔 "country:platform"。
    # 安卓也覆盖 US/JP/KR，与 iOS 对称（详情页两端都能切国家）。
    # 实际月配额由下方"配额分级"压低：US 主市场周级、JP/KR 次市场月级、销量仅
    # 主市场双周 → 6 组从约 360/月降到约 19/月（详见 SYNC_RANKING_COMBOS_PRIMARY 等）。
    SYNC_RANKING_COMBOS: str = "US:ios,US:android,JP:ios,KR:ios,JP:android,KR:android"

    # ── 配额分级（主/次市场 + 销量解耦）────────────────────────────
    # 目标：稳态 ≤50 次/月（公司池 3000 共享）。三个间隔旋钮按 UTC 日序号取模
    # 判定"今天是否到点"，纯函数、跨重启/多副本一致，无需持久化游标。
    #
    # SYNC_RANKING_COMBOS_PRIMARY: 主市场 combo（用 PRIMARY 间隔，可调得比次市场勤）。
    # 不在此列但在 SYNC_RANKING_COMBOS 内的 = 次市场（用 SECONDARY 间隔）。
    SYNC_RANKING_COMBOS_PRIMARY: str = "US:ios,US:android"
    # 主/次市场拉榜间隔（天）。1=每天，2=隔日，7=每周，30=每月。
    # 现策略：US 是唯一在盯的主市场 → 周级；JP/KR 仅偶尔回看 → 月级。
    # rank 长期趋势走本地库读，次市场月级足够。要 US 更勤就把 PRIMARY 调小。
    # 用量：US 2组×周 ≈ 8.7/月 + JP/KR 4组×月 ≈ 4/月 ≈ 12.7/月拉榜。
    SYNC_PRIMARY_INTERVAL_DAYS: int = 7
    SYNC_SECONDARY_INTERVAL_DAYS: int = 30
    # 日榜销量(下载/收入)抓取间隔（天）。ST 销量估算本身 T-1/T-2、日间波动小。
    # 非抓取日榜行 dl/rev 落 NULL（库内诚实），日榜读路径用该 app 上次已知值
    # 兜底显示，详情页趋势自然退化成稀疏数据点。
    # ⚠️ 销量只对**主市场**抓（见 scheduler._scheduled_sync 的 with_sales 门控）：
    # 次市场月级拉榜永远 with_sales=False，JP/KR 销量改走详情页按需取。
    # 取 7 与主市场拉榜(SYNC_PRIMARY_INTERVAL_DAYS)对齐：每个拉榜日都顺带拉销量，
    # 仪表盘金额与排名同周级刷新。用量：US 2组×每周 ≈ 8.7/月销量调用
    # （较双周 14 多 ≈ 4.3/月）。设 14 = 双周解耦省配额；1 = 每天抓。
    SALES_FETCH_INTERVAL_DAYS: int = 7

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

    # ── 新品监测（newcomers）：本地零配额「新面孔」检测 ─────────────────
    # 「新面孔」= 某 app_id 在过去 NEWCOMER_WINDOW 个同步快照里从没出现过、却在
    # 最近一次同步进入 Top NEWCOMER_TOPN 的产品。纯读 game_rankings，零 ST 配额。
    # 与 COMPETITOR_*(movement 今日 vs 昨日 TopN 进退、且只看 SLG 白名单) 互补：
    # 那个抓"老熟人进退"，这个抓"全新面孔"且**故意不走 is_slg**——全新产品的发行商
    # 往往还没进 SLG 白名单(白名单滞后维护)，过滤会把最该看的新厂商新品筛掉。
    # 回看多少个同步快照作"见过"基线。同步周级化后 ≈ 4 周历史。
    NEWCOMER_WINDOW: int = 4
    # 最近一次同步里名次 ≤ 该值才算"新进榜"，过滤榜尾噪声。
    NEWCOMER_TOPN: int = 50
    # 历史沉淀口径（market_newcomer_log）：比日报 Top50 宽，页面可筛 Top50/100。
    NEWCOMER_HISTORY_TOPN: int = 100

    # ── App Store 清单雷达（免费 iTunes lookup，零 ST 配额）──────────────
    # 每轮对每个开发者账号扫这些 storefront（逗号小写）。SLG 几乎都先软启动：
    # ph/ca/au/sg 是经典测试区，us 单区扫描会在软启动期完全失明。每加一区 =
    # 每轮每账号多 1 个免费请求，与 ST 配额无关。
    ITUNES_RELEASES_STOREFRONTS: str = "us,ph,ca,au,sg"
    # 基线之后首次见到、但 iTunes release_date 早于 N 天前的 app → 静默入基线
    # 不报"新上架"。两个作用：①新增扫描区首轮会捞出一堆历史区域限定 app（老的
    # 区域变体/死掉的测试包），不是"新品"，不该刷屏；②防止下架老 app 重新上架
    # 被误报。N 天内的才是真新品。
    ITUNES_RELEASES_OLD_RELEASE_DAYS: int = 180

    # ── 钉钉告警（自定义群机器人 webhook）────────────────────────────────
    # 不配 URL = 整体关闭（所有发送静默 no-op）。值放 backend/.env，不进 git。
    # DINGTALK_WEBHOOK_URL: 群机器人 webhook 完整地址（含 access_token）。
    # DINGTALK_SECRET: 机器人「加签」安全设置的 secret（选填；用「自定义关键词」
    #   模式时留空，关键词建议设 "SLG"——所有消息标题都带该前缀）。
    DINGTALK_WEBHOOK_URL: str = ""
    DINGTALK_SECRET: str = ""

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

    # ── 自有产品画像分析（我方产品 → AI 反推 brief）────────────────
    # 产品画像不需要逐秒看片，每个视频抽几帧看清题材/画风/玩法即可。
    PRODUCT_ANALYZE_FRAMES_PER_VIDEO: int = 6
    # 单次产品解析送给模型的图片总数上限（视频帧 + 图片素材合计）。
    # Claude vision 单次 ≤20 图，留点余量。
    PRODUCT_ANALYZE_MAX_IMAGES: int = 18

    # Sentry：留空时不上报。生产环境填入 DSN 即开启
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "production"
    SENTRY_TRACES_SAMPLE_RATE: float = 0.05

    # ── 微信公众号文章搜索（日报附行业分析）────────────────────────
    # 默认关闭：未部署 wechat-download-api / mock 模式下不去连，避免拖慢日报。
    WECHAT_ENABLED: bool = False
    WECHAT_API_BASE: str = "http://127.0.0.1:5001"
    # 单轮日报最多用多少个新品名去搜（限并发与外部服务压力）。
    WECHAT_MAX_KEYWORDS: int = 20
    # 登录 session 剩余 ≤ 此天数 → 提前预警；已过期/未登录则直接提醒。微信 MP
    # session 本就短（~4 天），预警设 1 天（仅最后一天提醒）避免天天刷屏。
    WECHAT_EXPIRY_WARN_DAYS: int = 1

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
