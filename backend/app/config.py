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
    # 每月最多调用 Sensor Tower 真实 API 的次数（本项目本地软护栏，防 bug 烧穿公司池）。
    # 公司账号 3000/月多团队共享；软预留护栏（SENSOR_TOWER_ORG_RESERVE）另保护池底。
    # 当前节奏（prod 10 combo 收入榜 + US/JP/KR 下载榜，US 每日 / 次市场双周）自动同步
    # ≈156/月（次市场周级则 ≈182），叠加手动刷新 / 详情页按需取 → 设 200 留 ~44 余量，
    # 仅占公司池 ~6%。旧注释「≈74/月、设 100」是 6-combo 时代的，节奏扩到 10 组 + 下载榜
    # 后真实需求早破 100，导致每月中旬就烧穿、后半月全站回退快照——本次抬到 200 修复。
    # 超额后自动降级到 sensor_tower_snapshots 表里的最后一次成功响应（不报错、不断站）。
    # 历史回填已默认关闭（RANK_BACKFILL_ENABLED=False），故本值不再依赖
    # RANK_BACKFILL_QUOTA_FLOOR 挡停回填；要一次性补历史另见该开关。
    SENSOR_TOWER_MONTHLY_LIMIT: int = 200
    # 用量越过该百分比时打一条 ERROR（经 Sentry 推送），让维护者在配额耗尽
    # 前就收到主动告警，而不是等线上静默降级到过期快照才发现。
    SENSOR_TOWER_QUOTA_WARN_PCT: int = 80

    # 排行榜接口 /v1/{os}/ranking 参数。本项目是 SLG 竞品监控：看「策略子类 ×
    # 畅销榜」才对得上目的（SLG 重收入重买量；全游戏×免费榜会全是休闲消除）。
    # iOS 7017 = App Store 游戏/策略子类；chart_type 畅销榜。Android 类目串。
    SENSOR_TOWER_RANKING_CHART_TYPE_IOS: str = "topgrossingapplications"
    SENSOR_TOWER_RANKING_CHART_TYPE_ANDROID: str = "topgrossing"
    # 下载/免费榜 chart_type（ADR 0001）：两端都用「总免费榜」，新品由自家
    # first-appearance 逻辑筛，不依赖平台的「新品榜」定义，保持两端口径一致。
    SENSOR_TOWER_RANKING_CHART_TYPE_IOS_FREE: str = "topfreeapplications"
    SENSOR_TOWER_RANKING_CHART_TYPE_ANDROID_FREE: str = "topselling_free"
    # 哪些 combo 额外采下载榜（逗号分隔 "country:platform"）。空 = 全关（一键回退）。
    # prod 经 .env 设 US/JP/KR × 双端；US 日级、JP/KR 周级沿用现有 cadence 门控。
    FREE_CHART_COMBOS: str = ""
    SENSOR_TOWER_RANKING_CATEGORY_IOS: str = "7017"
    SENSOR_TOWER_RANKING_CATEGORY_ANDROID: str = "game_strategy"
    SENSOR_TOWER_RANKING_LIMIT: int = 100
    # Android 名字/图标/发行商靠抓 Google Play 商品页（无批量接口，一个包名一次
    # 请求，零 ST 配额）。只补前 N 个：榜尾对竞品监控无意义，限量压低耗时与封 IP
    # 风险。60→200：US 安卓畅销策略榜 #61-200 段密集分布已建档主体的产品
    # （com.elex / com.camelgames / com.kingsgroup / com.plarium / com.innogames /
    # com.tap4fun 等），富化只到 60 时这些行 publisher=None、无法按马甲归一；提到
    # 200 让其补到发行商字段后自动接回资本树（并发 8、纯 GP 抓取，约 3.3× 耗时）。
    SENSOR_TOWER_ANDROID_ENRICH_LIMIT: int = 200
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
    # 现策略：US 是唯一在盯的主市场 → 每日（全市场新面孔检出要日级，不漏新品）；
    # JP/KR 仅偶尔回看 → 月级。rank 长期趋势走本地库读，次市场月级足够。
    # 要省配额就把 PRIMARY 调大（7=周级，回到双周新面孔）。
    # 用量：US 2组×每日 ≈ 61/月 + JP/KR 4组×月 ≈ 4/月 ≈ 65/月拉榜。
    SYNC_PRIMARY_INTERVAL_DAYS: int = 1
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
    # 10 组合 × 52 周 ≈ 520 次一次性。每晚日常同步后涓流补，受配额护栏约束，
    # 永不挤占核心日同步；补完自动停（按 game_rankings 已有 rank 行判进度）。
    # **默认关闭**：回填是一次性活、主历史早已补齐；此前靠 MONTHLY_LIMIT(100) <
    # QUOTA_FLOOR(150) 被动挡停。本次把月上限抬到 200(>150) 后它会自动复活、每晚
    # +5 贪婪补到「剩余=FLOOR」吃掉同步余量，故显式关闭。要补未完历史（如新加 combo）：
    # 临时置 True + 把 MONTHLY_LIMIT 调到 >FLOOR 再跑，补完调回。
    RANK_BACKFILL_ENABLED: bool = False
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
    # movement「空降」回归门控回看窗（天）：new_entrant 默认靠 today vs 上一可用日两快照判，
    # 老 SLG 短暂跌出 TopN 又回来会被错标「🆕 空降」（prod 实测 US/iOS top 榜 ~32% app 有
    # 出榜又回缺口）。回看上一可用日**之前** N 天内是否曾在 TopN，曾在 → is_reentry=True，
    # 渲染「🔄 重回」+ 重要度降权、不污染今日要闻。纯本地多一条窗口查询，零 ST。0=关回归判定。
    COMPETITOR_REENTRY_WINDOW_DAYS: int = 30

    # ── 连涨趋势（sustained climb）：补 surge 单日阈值的盲区 ─────────────
    # surge 靠「今日 vs 上一可用日」单日名次跳 ≥ COMPETITOR_RANK_JUMP，抓不到「每天涨一点、
    # 单日够不到阈值、但多日累计很可观」的稳步爬升（真实样本 War and Order #40→#38→#35→#28，
    # 5 天累计升 12、单日最多才 7，被日间 diff 漏掉）。连涨 = 窗口内 SLG 竞品累计升幅达标、
    # 今日处窗口新高、且**无任何单日 surge**（每步 < RANK_JUMP，故与 surge 段零重叠、不重报）。
    # 纯读 game_rankings 窗口历史，零 ST。<=0（WINDOW 或 MIN_DROP）关此检测。
    COMPETITOR_CLIMB_ENABLED: bool = True
    # 回看窗口（天）。取 5 天：既够沉淀多日趋势，又短到 dense 日更市场才攒够快照——次市场
    # （双周同步）窗口内快照 < MIN_SNAPSHOTS 会被自动跳过（连涨只对 US/iOS 这类日更市场有意义）。
    COMPETITOR_CLIMB_WINDOW_DAYS: int = 5
    # 窗口内累计升幅（start_rank - cur_rank）≥ 该值才算连涨。与 RANK_JUMP 对称取 10：单日跳
    # ≥10=surge；多日累计 ≥10 且无单日 surge=连涨。真实校准：10 抓到 War and Order + Z Route
    # 两例稳步爬（20 天 2 例，高信号低噪），12 只剩 1 例。
    COMPETITOR_CLIMB_MIN_DROP: int = 10
    # 今日名次 ≤ 该值才纳入连涨候选。刻意比 surge 的 TopN(20) 宽——US/iOS top20 被头部 SLG
    # 霸榜极稳，真正的稳步爬升发生在中段向上（#40→#28），top20 内几乎无连涨信号。
    COMPETITOR_CLIMB_TOPN: int = 40
    # 窗口内至少 N 个快照才判连涨（baseline 充分性）。< N = 数据太稀疏（次市场/冷启动），
    # 跳过不误报。日更市场 5 天窗口天然满足，双周市场天然不足 → 结构性只对日更市场生效。
    COMPETITOR_CLIMB_MIN_SNAPSHOTS: int = 3

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
    # 检出日志保留天数：market_newcomer_log 只增不减，每日 prune 掉
    # first_detected_at 早于 N 天前的行（页面默认只看 90 天、最多筛 365 天，
    # 留 365 天足够覆盖且不让表无限膨胀）。<=0 = 关闭 prune（永久保留）。
    NEWCOMER_LOG_RETENTION_DAYS: int = 365
    # 厂商主体新品（publisher newcomers）的名次阈值。原本无阈值导致 weekly combo
    # 长尾抖动（老 SLG 产品因 4 周内有一周漏榜被误报"新品"）刷屏 digest——
    # JP/android 实测单 combo 23 项里 22 项是 #137–#535 的长尾噪声。设 200 让
    # 真正值得关注的中段新品仍能命中，砍掉榜尾。
    PUBLISHER_NEWCOMER_TOPN: int = 200
    # 厂商新品的 baseline 充分性门控：本地 game_rankings 快照过少时，"首次出现在
    # 本地榜单" ≈ "首次被我们采到"，与产品真实上架日完全脱钩——次市场（DE/RU 双周
    # 同步）刚开始采集只有 1~2 个快照，as_of 当期榜里凡上一快照不在榜的老产品全被
    # 误报"新品"（实测 DE/ios 18 项、RU/ios 12 项全是 2013–2017 老 SLG）。要求至少
    # N 个历史快照才报厂商新品，不足则视为"数据积累中"（no_baseline）不报。攒够后
    # 真实上架日门控（见 ITUNES_RELEASES_OLD_RELEASE_DAYS）继续兜底滤老产品。
    PUBLISHER_NEWCOMER_MIN_BASELINE: int = 3

    # ── 每日 digest 群推送封顶（避免波动大的日子刷出一张超长卡）────────────
    # 单 combo 的 movement 显示行上限（新品两层已各自 [:10]）。movement 原本无任何
    # 上限——波动大的市场一天能甩出几十条异动，封死单 combo 展示量。
    DIGEST_MOVEMENT_TOPN: int = 8
    # 全卡全局 item 上限（按 combo 粒度累加，超出的 combo 折叠成
    # 「…另有 N 项，看板查看全部」一行，保证卡片长度可控）。
    DIGEST_MAX_ITEMS: int = 30
    # 卡顶「今日要闻」跨 combo 置顶条数：把全卡最高重要度的 N 个事件（市场权重 ×
    # 事件强度，见 release_alerts._event_score）抽出来置顶，保证核心市场大事件不被
    # 次市场长尾折叠挤掉。仅当（排除正文首位 combo 后）事件数 > 该值才渲染（小卡本身
    # 已短、置顶会和正文重复，没必要）。0=关。
    DIGEST_HIGHLIGHTS_TOPN: int = 5
    # 注：实机视频不再单列整段（曾有 DIGEST_VIDEO_TOPN 控制其展示上限），改为内联进各
    # 新品行（🎬，见 release_alerts.build_newcomer_lines），免同批新品名列两遍。
    # 单 combo「市场新面孔 · 待识别新厂」(is_slg=false) 展示上限：次市场（RU/DE）批量同步日
    # 会一次涌进几十个未识别新面孔（混足球/塔防/恐怖等非 SLG 噪声，且 genre 仅本地化大类
    # 「Игры/Spiele」无法精准门控），逐条列会刷屏。只详列前 N 个（按榜排名），其余折叠成
    # 「另有 M 个未识别新面孔上榜，看板核查」一行——建档线索仍可经折叠行→看板追溯，不静默丢。
    DIGEST_MARKET_LEAD_TOPN: int = 3
    # 平淡日心跳卡：maintainer 卡全空（无异动/新品/版本…）且核心 US/iOS 已同步=「真平淡日」时，
    # 是否发一张「今日平静」心跳卡（让领导/维护者知道系统活着、非漏发）。默认 False——测试群
    # 只有本人、天天收无聊心跳没意义；**推领导群后再开**（领导看不到卡会误读「是不是坏了」）。
    # 注意：与此分支无关的「核心 US/iOS 今日无快照」(同步未就位) 始终升 logger.error→Sentry
    # + 发克制维护者兜底卡，不受本开关控制——那是管道故障告警，不是平淡日心跳。
    DIGEST_HEARTBEAT_ENABLED: bool = False
    # 平淡日阈值：当日竞品实质信号（异动 + 四层新品 + 版本 + 新区）少于此数 → 判「平淡日」，
    # 触发**维护者卡**兜底填充（SLG 行业动态段等），补次市场非同步日 + 美区平淡时的稀薄卡。
    # 仅核心 US/iOS 已同步（真平淡、非管道故障）时才填。<=0 = 关闭兜底。
    DIGEST_QUIET_THRESHOLD: int = 6
    # 平淡日兜底之一：把商店雷达（itunes/gp 清单 diff，6h 级独立推送）近 N 天的非基线新上架
    # 折进 digest 一段，给平淡日一个「近期雷达catch」的日级汇总视图。仅维护者卡、仅平淡日。
    # 零 ST（纯本地 publisher_itunes_apps 读）。<=0 = 关闭该段。
    DIGEST_RADAR_RECENT_DAYS: int = 2
    # 新品周察周报卡（P0-1③ 新品生命周期追踪）：周级独立卡，回顾近 N 天检出的 SLG 新品
    # 「存活/爬升/掉榜」分层（读时算 game_rankings 走势，零 ST）。补「检出即阅后即焚」断层——
    # 领导看到「新品 X 上架」后没人答「它后来怎么样了」。两卡都发（SLG-only，领导可读）。
    DIGEST_WEEKLY_REVIEW_ENABLED: bool = True
    DIGEST_WEEKLY_REVIEW_DAYS: int = 30      # 回看窗口
    DIGEST_WEEKLY_REVIEW_CAP: int = 8        # 起飞 / 掉榜每段明细上限
    # 存量竞品玩法子品类回补（P1-2）：给「有描述的 is_slg 存量 app」（tracked games / movement
    # 老熟人 / subgenre 特性前老检出行）补分类进 app_subgenre 表，digest 建 own_matches 时作
    # fallback → ⚔️ 同赛道对老竞品也生效。digest 内每轮 drain 上限（LLM 便宜文本模型、前进式累积，
    # 几天内把存量分类完，稳态近 0）。<=0 = 关闭回补。
    APP_SUBGENRE_BACKFILL_CAP: int = 15
    # 商店雷达软启动新品接入富化管道（P1-1，修「信号越早富化越少」倒挂）：雷达清单 diff 检出
    # 真新上架（is_baseline=false）且属 SLG 主体时，补写一行「影子行」进 market_newcomer_log
    # （chart_type='radar'，rank=NULL）→ 天然汇入中文化 / subgenre / 视频富化流。影子行**不进
    # 新品页市场卡片网格**（/history 排除 chart_type='radar'），只把富化出的 📝 摘要回显到「商店
    # 雷达」区块 + digest 雷达段。零 ST（富化字段本就随 iTunes lookup 拿到）。False = 关此接入。
    RADAR_NEWCOMER_ENRICH_ENABLED: bool = True

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
    # GP 侧「老品」兜底：Google Play 开发者页拿不到可靠 release_date（页面结构里没有），
    # 上面的上架日门控对 GP 全失效——GP 开发者页分页，首同步没抓全的老包（如 EasyTech
    # 的 World Conqueror 2，6.5 万评价、十年老游戏）下轮才现身会被误报"新上架"。评价数
    # 是随详情页 JSON-LD 免费抓到的「存量用户」代理：超过该阈值 = 明显是有大量用户的老
    # app，非新上架 → 静默入基线。仅在 release_date 缺失时启用（iOS 有真实上架日就信它，
    # 避免误杀首月冲高评价的真爆款新游）。评价数缺失/低 = 真软启动，不抑制。<=0 = 关闭。
    ITUNES_RELEASES_ESTABLISHED_RATING_COUNT: int = 10000

    # ── 钉钉告警（自定义群机器人 webhook）────────────────────────────────
    # 不配 URL = 整体关闭（所有发送静默 no-op）。值放 backend/.env，不进 git。
    # DINGTALK_WEBHOOK_URL: 群机器人 webhook 完整地址（含 access_token）。
    # DINGTALK_SECRET: 机器人「加签」安全设置的 secret（选填；用「自定义关键词」
    #   模式时留空，关键词建议设 "SLG"——所有消息标题都带该前缀）。
    DINGTALK_WEBHOOK_URL: str = ""
    DINGTALK_SECRET: str = ""
    # DINGTALK_WEBHOOK_LABEL: 当前 webhook 对应群的人读别名，仅用于发送日志区分（不进
    # 卡片内容）。当前 = 仅本人的「测试群」（= maintainer 群，收全量含待建档/雷达/重登提醒）。
    DINGTALK_WEBHOOK_LABEL: str = "测试群"
    # ── 领导群（leader target）──────────────────────────────────────────
    # 每日 digest 双发: maintainer 群收全量(含待建档/视频/运维杂讯)，领导群收**剥离维护者
    # 杂讯的精简情报卡**。**只有这三项独立配了 leader webhook，才真往领导群发第二张卡**
    # （未配 = 维持今天的单卡单群，向后兼容；不会把领导版卡误发回 maintainer 群）。
    # 值放 backend/.env，不进 git。维护者类提醒(微信重登/商店雷达/自检)永远只发 maintainer。
    DINGTALK_WEBHOOK_URL_LEADER: str = ""
    DINGTALK_SECRET_LEADER: str = ""
    DINGTALK_WEBHOOK_LABEL_LEADER: str = "领导群"

    # 看板对外可访问基址（如 https://<域名>，**不含**末尾斜杠）。仅用于在每日 digest
    # 里给新品行拼「看板定位」深链（?focus=<app_id> 进新品页高亮该卡）。空 = 不拼深链
    # （digest 行为完全向后兼容）。值放 backend/.env / 运维私有渠道，不进 git。
    DASHBOARD_BASE_URL: str = ""

    # 素材库上传文件落盘根目录。容器内走已挂载的 ./data:/app/data 卷，
    # 宿主机即 /opt/slg-research-dashboard/data/materials，与 DB 同一备份域。
    MEDIA_ROOT: str = "./data/materials"
    # 单文件大小上限（字节）。默认 200MB，覆盖绝大多数广告视频素材。
    MEDIA_MAX_BYTES: int = 200 * 1024 * 1024
    # 站内播放/预览签名 URL 有效期（秒）。<video src> 带不了请求头，故用
    # HMAC 短时令牌走 query string；列表每次刷新会重签，过期自然失效。
    MEDIA_URL_TTL_SECONDS: int = 6 * 3600
    # 媒体签名 HMAC 密钥。独立于 API_KEY——API_KEY 会编进前端 bundle，若复用为媒体
    # 签名密钥，任何拿到 bundle 的人即可伪造任意 material 的合法 token。设独立随机值
    # （不进前端）闭合此漏洞。未配则回退 API_KEY（平滑迁移，旧链接不立即失效）。
    MEDIA_SIGNING_SECRET: Optional[str] = None

    # ── 太石 LLM 网关 ────────────────────────────────────────────
    # 公司统一大模型网关，OpenAI 兼容协议。审批通过后钉钉发 key。
    # 不直连 Anthropic/OpenAI（合规）。文档：reference_taishi_gateway memory。
    TAISHI_API_KEY: Optional[str] = None
    TAISHI_BASE_URL: str = "https://relay.tuyoo.com/v1"
    # 视频/图片分析用：视觉模型。Claude sonnet/opus 与 Gemini 系列支持图，
    # GLM 系列只支持 text，不能用于素材帧分析。
    TAISHI_VISION_MODEL: str = "claude-sonnet-4.5"
    # 纯文本轻任务用（新品描述中文化 + 一句话摘要）：选便宜模型即可，量小、非关键。
    TAISHI_TEXT_MODEL: str = "gemini-3-flash-preview"
    # 新品中文化每日上限（防极端日批量翻译烧成本）；只对 is_slg 新品翻一次（按 app 去重）。
    NEWCOMER_TRANSLATE_DAILY_CAP: int = 30
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
    # 文章 ↔ 新品名匹配的最小名长（非拉丁名按非空白字符数）。1 字通用名（"城""塔"）
    # 裸 substring 必泛滥误挂，<此值的非拉丁名跳过匹配（宁漏不误）。拉丁名走词边界
    # 不受此限。默认 2：保留"原神"(2字) 等真名，砍掉单字噪声。
    WECHAT_MATCH_MIN_NAME_LEN: int = 2
    # 登录 session 剩余 ≤ 此天数 → 提前预警；已过期/未登录则直接提醒。微信 MP
    # session 本就短（~4 天），预警设 1 天（仅最后一天提醒）避免天天刷屏。
    WECHAT_EXPIRY_WARN_DAYS: int = 1
    # ── 平淡日「SLG 行业动态」兜底段（公众号广搜，仅维护者卡）──────────────
    # 与「按新品名精确回挂文章」互补：那个把文章挂到当日检出新品，这个是**无检出/信号
    # 稀薄时**用行业关键词广搜订阅号，补一段近期 SLG 行业/新品动态。仅 DIGEST_QUIET_THRESHOLD
    # 判定的平淡日 + 核心已同步时触发。零 ST（走 wechat-api）。#182 起**两卡都发**（段头标注
    # 「非我方追踪竞品·行业面背景」划清口径边界，靠标注而非删段）；跨天去重见下方台账。
    WECHAT_INDUSTRY_ENABLED: bool = True
    # 受控关键词表（偏新品动态；覆盖 新品/首发/上线 + 出海/海外 + 版号/厂商/投融资 + 买量/素材
    # 四类）。逗号分隔，可在 backend/.env 覆盖。空 = 关闭该段。宁少而准，别用光秃秃「SLG」招噪。
    WECHAT_INDUSTRY_KEYWORDS: str = (
        "SLG 新游,策略新游 上线,SLG 首发,SLG 出海,策略手游 海外,SLG 版号,SLG 投融资,SLG 买量 素材")
    # 只取最近 N 天的文章（"动态"要新）。跨天重复由下方「已推 link 台账」持久去重兜底，时窗
    # 只控搜索范围（不再是唯一防重手段）。
    WECHAT_INDUSTRY_DAYS: int = 3
    # 行业段最多展示几条。
    WECHAT_INDUSTRY_MAX: int = 4
    # 行业动态段跨天去重：发过的文章 link 落 wechat_article_sent 台账，后续广搜结果里已推的
    # link 全过滤掉，让领导群每天见到没推过的文章（此前只靠 WECHAT_INDUSTRY_DAYS 时窗，连续
    # 平淡日会重复推同一篇）。零 ST（纯本地表读写）。False = 退回旧的「仅时窗控重复」行为。
    WECHAT_ARTICLE_DEDUP_ENABLED: bool = True
    # 已推 link 台账保留天数：只增不减会膨胀，落库时 prune 掉 first_sent_date 早于 N 天前的行。
    # 30 天足够（一篇文章不会 30 天后还被当「近期动态」搜出来）。<=0 = 关 prune（永久保留）。
    WECHAT_ARTICLE_SENT_RETENTION_DAYS: int = 30

    # ── 新品实机玩法视频自动搜集（YouTube Data API · ADR 0002）──────────────
    # 竞品新品检出后按「游戏名 + gameplay」搜 YouTube 实机玩法视频候选。YT 独立
    # 配额，完全不碰 Sensor Tower 池。留空 = 整体关闭（搜索静默 no-op、返回空），
    # 与 newcomer enrich 同哲学。值放 backend/.env，不进 git。
    YOUTUBE_API_KEY: Optional[str] = None
    # 每日 search.list 调用硬上限（软护栏）。YT 免费池 10000 units/天、search=100
    # units/次 → 100 次/天；设 80 留余量。当日触达上限后新检出 app 排次日再搜
    # （不静默丢，见 ADR 0002 配额护栏）。
    YOUTUBE_SEARCH_DAILY_CAP: int = 80
    # 每个新品存几条候选（= search.list maxResults，一次调用）。前 5 条够人工挑、
    # 不刷屏；YT 单次 maxResults 上限 50。
    YOUTUBE_SEARCH_MAX_RESULTS: int = 5
    # 搜索词后缀：拼成 "<游戏名> <后缀>"。默认 gameplay 召回实机；可调优（试
    # "gameplay walkthrough" 提纯，或后续加排除词压直播/解说噪声，见 ADR 0002 观察）。
    YOUTUBE_SEARCH_QUERY_SUFFIX: str = "gameplay"
    # 只给「近 N 天检出」的新品搜视频：聚焦新品语义、防首次上线把 365 天历史检出
    # 全量搜爆配额。0 = 不限（给所有未搜过的检出补搜）。台账去重保证每 app 只搜一次。
    YOUTUBE_SEARCH_LOOKBACK_DAYS: int = 30

    # 分地区上线对照（需求② 子项③ / ADR 0004）：对每个 tracked iOS 竞品在这些
    # storefront 查 iTunes releaseDate（零 ST，每 country 一次批量 lookup）。逗号分隔
    # 国家码；按 country 循环、每轮含全部 trackId，故请求数 = storefront 数（与游戏数
    # 无关）。默认覆盖主要 SLG 市场，可按需增删。
    REGION_LAUNCH_STOREFRONTS: str = "us,jp,kr,tw,cn,de,gb,fr,ca,br"
    # 「竞品新进某区」事件检测窗口：releaseDate 落在近 N 天的分地区上架才算「新上线」
    # 事件、推进每日 digest（防首填把多年前的历史上架日全量当新闻刷屏）。
    REGION_LAUNCH_RECENT_DAYS: int = 30

    @property
    def region_launch_storefront_list(self) -> list[str]:
        """REGION_LAUNCH_STOREFRONTS → 去空去重的小写国家码列表（保序）。"""
        seen: list[str] = []
        for c in (self.REGION_LAUNCH_STOREFRONTS or "").split(","):
            cc = c.strip().lower()
            if cc and cc not in seen:
                seen.append(cc)
        return seen

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

    @property
    def free_chart_combos_set(self) -> set[tuple[str, str]]:
        """额外采下载榜的 combo 集合（ADR 0001）。FREE_CHART_COMBOS 解析而来，
        只取与 SYNC_RANKING_COMBOS 的交集——不在主同步集里的不会凭空多采。"""
        return set(self._parse_combos(self.FREE_CHART_COMBOS)) & set(self.sync_combos_list)

    class Config:
        env_file = ".env"

settings = Settings()
