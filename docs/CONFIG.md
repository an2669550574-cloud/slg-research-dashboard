# 配置速查表（活配置 vs 纯默认）

`backend/app/config.py` 有 **118** 个配置项，绝大多数跑代码默认、从没在 prod 动过。这份表回答三个
维护者最常问的问题：**①哪些 prod 真调过了（偏离默认）②为什么调这个值 ③改了怎么让它生效**。

> **本文件不写密钥/域名/网关地址/webhook**（`CLAUDE.md` 硬规则）——这些只在 `.env` / 运维私有渠道，
> 表里一律记「见 .env」。只文档化非敏感的调优旋钮（间隔 / 上限 / 开关 / 阈值）。
>
> 两个 `.env`：根 `.env`（compose `--env-file` 用，含 `API_KEY` / 域名 / TLS）；`backend/.env`（后端应用配置，
> 经 compose `env_file:` 注入）。同名以进程实际读到的为准。

---

## 一、改了配置怎么生效（**最容易忘、先看这段**）

pydantic Settings 在**进程启动时**读一次 env；compose 的 `env_file:` 在**容器创建时**注入。所以：

| 改了什么 | 让它生效的命令 | 为什么 |
|---|---|---|
| `backend/.env` 任何项（同步节奏 / 配额 / 开关 / 密钥…） | `docker compose -f docker-compose.prod.yml --env-file .env up -d backend` | `env_file` 在**容器创建**时注入 → 必须**重建**容器 |
| 根 `.env` 的 **`API_KEY`** | `… build frontend --no-cache && … --env-file .env up -d` | 前端把 `API_KEY` 当 build arg 编进 `VITE_API_KEY`（编译期）→ 不重构前端仍用旧 key，登录后 **401** |
| 根 `.env` 其它（`SLG_DOMAIN`/`CORS_ORIGINS`/`SLG_TLS_EMAIL`/`RATE_LIMIT_DEFAULT`） | `… --env-file .env up -d` | compose 变量替换 + backend `environment` |
| **代码**改动 | `… --env-file .env up -d --build` | 要重新构建镜像 |

- ⚠️ **`docker compose restart` 永远不重读 `.env`**——它只重启进程、复用容器现有环境。别用它来生效配置改动。
- `--force-recreate` 可强制重建（`env_file` 内容变了但 compose 没感知到差异时用）。
- 内存 TTL 缓存（如 api_usage 的 `SENSOR_TOWER_ACCOUNT_USAGE_TTL_HOURS`）随容器重建即清。

---

## 二、prod 已显式设置的项（活配置）

下表 = prod `.env` 里出现的变量。**「偏离默认」列**：✅=值和 `config.py` 默认不同（真调过）、⚪=显式设但等于默认
（写出来只为醒目/防漂移）、🔑=密钥凭据（值见 .env）。

### 数据与同步（ST 配额相关，最硬约束）

| 变量 | 偏离默认 | prod 值 / 说明 | 为什么 |
|---|---|---|---|
| `USE_MOCK_DATA` | ✅ | `false`（默认 `True`） | prod 拉真实数据；本地开发才 true |
| `SYNC_RANKING_COMBOS` | ✅ | 10 combo：US/JP/KR/DE/RU × ios/android（默认 6） | 收入榜采集面。**改后配额变**，先掂量额度 |
| `SYNC_SECONDARY_INTERVAL_DAYS` | ✅ | `7`（默认 30） | 次市场**周级**同步（2026-07-16 D2 裁定由 14 提频：实测月用量 105 ≪ 估算 156、护栏 200 内 headroom ~95，且次市场贡献 SLG 新品为 US 2.4 倍——检出延迟砍半值这个价。曾为省配额 7→14，此次数据驱动回摆；改回 = sed 回 14 + force-recreate backend） |
| `FREE_CHART_COMBOS` | ✅ | US/JP/KR × 双端（默认空） | 下载榜并行采集（ADR 0001），+1 配额/combo |
| `SENSOR_TOWER_CACHE_TTL` | ✅ | `1800`（默认 86400） | ST 响应缓存 30 分钟（更新鲜；仍省重复调用） |
| `SENSOR_TOWER_ANDROID_ENRICH_LIMIT` | ⚪ | `200`（=默认） | — |
| `SENSOR_TOWER_API_KEY` / `SENSOR_TOWER_BASE_URL` | 🔑 | 见 .env | ST 鉴权（`auth_token` query，非 Bearer） |
| `DATABASE_URL` | — | 见 .env（prod SQLite 路径） | — |

### 推送与告警

| 变量 | 偏离默认 | prod 值 / 说明 | 为什么 |
|---|---|---|---|
| `DINGTALK_WEBHOOK_URL` / `_SECRET` | 🔑 | 见 .env | 维护者群 webhook（加签） |
| `DINGTALK_WEBHOOK_URL_LEADER` / `_SECRET_LEADER` | 🔑 | 见 .env | 领导群 webhook（2026-06-30 配，双卡分发） |
| `WECHAT_ENABLED` | ✅ | `true`（默认 False） | 开公众号文章联动 |
| `WECHAT_INDUSTRY_KEYWORDS` | ✅ | 短词表：出海,海外,新游,首发,公测,SLG,策略,买量,畅销榜,新品,4X,三国 | **必须短词**（wechat-api 是标题精确匹配，组合词搜不出）；改词也应同步 `_industry_score` 权重表 |
| `WECHAT_API_BASE` | — | 见 .env（本机公众号服务） | — |
| `DASHBOARD_BASE_URL` | ✅ | 见 .env（= prod 域名） | 卡片深链基址；空则无 🎯 看板链接 |

### 平台 / 运维（多在根 .env）

| 变量 | 偏离默认 | prod 值 / 说明 |
|---|---|---|
| `API_KEY` | 🔑 | 见根 .env（同值编进前端 `VITE_API_KEY`，改动见 §一） |
| `CORS_ORIGINS` | ✅ | 锁定 prod 域名（默认 `*`） |
| `RATE_LIMIT_DEFAULT` | ✅ | `120/minute`（默认 None=不限） |
| `RATE_LIMIT_AI_SYNC` | ⚪ | `10/hour`（=默认） |
| `SLG_DOMAIN` / `SLG_TLS_EMAIL` / `SLG_ORIGIN_TLS` | — | 见根 .env（域名 + CF 拓扑，详见 SECURITY-CADDY-DOMAIN.md） |
| `LOG_LEVEL` | ⚪ | `INFO`（=默认） |
| `SENTRY_DSN` | 🔑 | 见 .env（迁移须手动带，不进 git） |
| `SENTRY_ENVIRONMENT` / `SENTRY_TRACES_SAMPLE_RATE` | ⚪ | `production` / `0.05`（=默认） |
| `ANTHROPIC_API_KEY` / `TAISHI_API_KEY` / `YOUTUBE_API_KEY` | 🔑 | 见 .env（LLM 走太石网关 `TAISHI_*`，不直连官方） |

---

## 三、值得知道的纯默认项（没进 prod .env，跑代码默认）

维护者可能想调、但目前用默认的旋钮。全部改法同 §一（多在 `backend/.env`）。

### ST 配额护栏
| 变量 | 默认 | 控制 |
|---|---|---|
| `SENSOR_TOWER_MONTHLY_LIMIT` | `200` | 本地软护栏月上限（实测双周态 ~105/月，2026-07-16 周级后预计 ~125-135）。核心约束，改前确认水位 |
| `SENSOR_TOWER_QUOTA_WARN_PCT` | `80` | 用量触顶告警阈值 |
| `SENSOR_TOWER_RANKING_LIMIT` | `100` | 每次拉榜深度（同 1 次配额多捞深位） |
| `SENSOR_TOWER_ORG_RESERVE` / `_ORG_LOW_THRESHOLD` | `30` / `100` | 公司池软预留 / 低水位线 |
| `SALES_FETCH_INTERVAL_DAYS` | `7` | 主市场销量抓取间隔（省 top-N 批量销量配额） |
| `RANK_BACKFILL_ENABLED` | `False` | 历史回填**默认关**（开了烧配额） |

### movement 竞品异动
| 变量 | 默认 | 控制 |
|---|---|---|
| `COMPETITOR_ALERT_TOPN` | `15` | 空降/窜升/跌出/回归的名次闸门（#200 由 20 收到 15） |
| `COMPETITOR_REVENUE_TOPN` | `20` | 收入异动独立更宽闸门（#201 解耦，收入是高信号不随名次收窄） |
| `COMPETITOR_REVENUE_PCT` | `50` | 收入大涨阈值（%） |
| `COMPETITOR_CLIMB_TOPN` / `_WINDOW_DAYS` / `_MIN_DROP` | `30` / `5` / `10` | 连涨趋势（#184，补 surge 单日盲区） |
| `COMPETITOR_REENTRY_WINDOW_DAYS` | `30` | 回归门控窗口 |

### 新品监测
| 变量 | 默认 | 控制 |
|---|---|---|
| `NEWCOMER_WINDOW` | `4` | 新面孔判定回看快照数 |
| `NEWCOMER_BASELINE_DAYS` | `30` | baseline 日历下限（#222，治 US 日更 reentry 噪声） |
| `RSS_EARLYBIRD_COUNTRIES` | `jp,kr` | RSS 早鸟国家（ADR 0005）；**置空关闭** |
| `PUBLISHER_NEWCOMER_MIN_BASELINE` | `3` | 厂商新品 baseline 充分性门控（#161） |
| `NEWCOMER_LOG_RETENTION_DAYS` | `365` | 检出日志保留天数（prune job 用） |

### digest 推送
| 变量 | 默认 | 控制 |
|---|---|---|
| `DIGEST_MAX_ITEMS` | `30` | 全局封顶 |
| `DIGEST_MOVEMENT_TOPN` | `8` | 异动展示 cap |
| `DIGEST_QUIET_THRESHOLD` | `6` | 平淡日判定阈值（低于则触发兜底填充） |
| `DIGEST_HEARTBEAT_ENABLED` | `False` | 平淡日 keep-alive 空卡（**与 job 心跳自检无关**） |
| `DIGEST_WEEKLY_REVIEW_*` | `True`/`30`/`8` | 新品周察卡（周一 04:40 UTC） |
| `DIGEST_MONTHLY_ROLLUP_*` | `True`/`30`/`5` | 月度市场月报（每月 1 号 05:00 UTC，#233） |

### LLM（太石网关）/ 微信 / YouTube / 雷达
| 变量 | 默认 | 控制 |
|---|---|---|
| `LLM_DAILY_BUDGET_USD` / `_MONTHLY_BUDGET_USD` | `5` / `30` | LLM 成本护栏（#194，触顶告警） |
| `MEDIA_SIGNING_SECRET` | `None` | 媒体签名密钥（#195）；**未设=回退 API_KEY**（待设） |
| `TAISHI_VISION_MODEL` / `_TEXT_MODEL` | `claude-sonnet-4.6` / `gemini-3-flash-preview` | 素材分析 / 文本模型 |
| `WECHAT_INDUSTRY_DAYS` / `_MAX` | `5` / `4` | 行业动态窗口 / 推送条数上限 |
| `WECHAT_ARTICLE_SENT_RETENTION_DAYS` | `30` | 已推文章台账保留（跨天去重 prune） |
| `YOUTUBE_SEARCH_DAILY_CAP` | `80` | 视频搜集日上限（YT 独立配额，不碰 ST） |
| `ITUNES_RELEASES_ESTABLISHED_RATING_COUNT` | `10000` | 商店雷达老品门控阈值（#176，GP 无 release_date 时的老品代理） |
| `REGION_LAUNCH_STOREFRONTS` | `us,jp,kr,tw,cn,de,gb,fr,ca,br` | 分地区上线对照的 storefront 名单 |

---

## 四、坑（勿踩）

- **`restart` 不重读 .env**（见 §一）——最常见的「改了没生效」。
- **`WECHAT_INDUSTRY_KEYWORDS` 必须短词**、不带空格/组合词（wechat-api 标题精确匹配）；改词表也应同步 `wechat_articles.py` 的 `_industry_score` 权重表。
- **同步节奏刻意省配额**（`SYNC_*` / `FREE_CHART_COMBOS`）——别擅自加密，改前确认 ST 水位（`docs/ARCHITECTURE.md § ST 配额体系`）。
- 密钥类改动别忘 Sentry DSN / webhook 迁移时手动带（不进 git）。
