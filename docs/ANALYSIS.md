# SLG Research Dashboard · 只读分析报告

> 分析范围:纯源码静态扫描,不修改任何文件,不安装依赖,不执行重构
> 日期:2026-05-12

---

## 1️⃣ 技术栈

| 维度 | 选型 | 说明 |
|---|---|---|
| **前端框架** | React 18.3 + TypeScript 5.4 + Vite 5.3 | SPA，`react-router-dom v6` 路由 |
| **状态管理** | TanStack Query v5 | 服务端状态；无 Redux/Zustand，跨页面状态靠 React Query 缓存共享 |
| **UI** | Tailwind 3 + 自定义 CSS 变量主题 + `lucide-react` 图标 + `react-hot-toast` | 无组件库；明暗主题靠 CSS variables (`--bg-elevated` / `--text-primary` 等) |
| **图表库** | Recharts 2.12 | 折线 / 面积 / 柱状 |
| **后端框架** | FastAPI 0.111 + Uvicorn | 全异步 |
| **数据库** | SQLite + aiosqlite + SQLAlchemy 2 async | 文件型，生产部署在容器卷里 |
| **迁移** | Alembic 1.13 | 启动时 `command.upgrade(cfg, "head")` 自动升级，见 `backend/app/database.py:21-26` |
| **缓存** | 进程内 `InMemoryTTLCache`(L1) + SQLite `sensor_tower_snapshots` 表(L2) | 单进程内存 + 跨进程持久化双层；无 Redis |
| **定时任务** | APScheduler 3.10 `AsyncIOScheduler` | Cron 02:30 / 02:35 抓 US/iOS + US/Android |
| **鉴权** | `X-API-Key` Header 单密钥 | 见 `backend/app/security.py`，无用户系统 |
| **限流** | slowapi 0.1.9 | 默认关闭；只对 `/history/sync/{id}` 强制 10/hour |
| **可观测性** | sentry-sdk + 自定义 `RequestLoggingMiddleware` + `X-Request-ID` | DSN 可选 |
| **测试** | pytest + pytest-asyncio(后端) · vitest + testing-library(前端) | 11 个后端测试文件，3 个前端测试 |

---

## 2️⃣ 功能模块梳理

### 仪表盘 `frontend/src/pages/Dashboard.tsx`
- 4 张 StatCard:监控游戏数 / 总下载 / 总收入 / Top1 游戏
- 2 张柱图(收入 / 下载)
- Top 8 列表(点击进详情)
- **"刷新"按钮调用 `force_refresh_today_rankings`，绕过 L1+L2 缓存，消耗 1 次配额** → `backend/app/routers/games.py:62-66`
- ⚠️ **国家/平台被硬编码为 US/iOS**，`Dashboard.tsx:41`

### 排行榜 `frontend/src/pages/Rankings.tsx`
- 8 国(`US/GB/DE/JP/KR/AU/CA/FR`)× 2 平台
- 客户端搜索(name/publisher)
- CSV 导出
- ⚠️ **过滤纯前端**，数据全量返回再 `.filter()`

### 游戏对比 `frontend/src/pages/Compare.tsx`
- 最多 3 款，3 个指标(revenue/downloads/rank)，3 个时间窗(7/30/90 天)
- 用 `useQueries` 并行拉每款游戏的 `/metrics`
- 共用 X 轴日期，缺失点 `connectNulls`

### 素材库 `frontend/src/pages/Materials.tsx` + GameDetail 内嵌 `MaterialsPanel`
- 4 种平台标签(youtube/tiktok/meta/other)，3 种类型(video/image/playable)
- 标签 / 备注 / 关联游戏
- ⚠️ **关联游戏名通过 `gamesApi.rankings()` 查表**，`Materials.tsx:24-27` —— 不在今日榜单里的 tracked game 显示不出名字

### 游戏管理 `frontend/src/pages/GamesManage.tsx`
- iTunes Search API "查询"按钮 → 自动填表
- 创建 / 编辑 / 删除
- ⚠️ **删除不级联**，history/materials 保留

### Sensor Tower API 使用量 `frontend/src/components/QuotaBanner.tsx` + `backend/app/services/quota.py`
- 月度 limit 默认 500，used 进度条 + 三档配色(`<80%` 绿 / `80–99%` 黄 / `100%` 红)
- 60s 自动 refetch
- 超额降级:返回最后一次成功 snapshot(可能"过期")

### CSV 导出 `frontend/src/lib/csv.ts`
- 出现在 Dashboard / Rankings / Materials / GameDetail Timeline 四处
- 列定义通过 `{header, get}` 数组传入，统一封装

### 数据刷新
三个触发口:
1. dashboard "刷新"按钮 → `force_refresh_today_rankings`(消耗 1 quota)
2. `POST /api/games/sync-rankings` → 与定时任务同逻辑(写 `game_rankings` 表)
3. APScheduler 每天自动跑

---

## 3️⃣ 数据流

### 真实 API vs Mock

| 数据 | 来源 | 切换条件 |
|---|---|---|
| 今日榜单 / 收入 / 下载 / 单 app rank | **Sensor Tower**(真) / 内置 mock | `USE_MOCK_DATA=false` **且** `SENSOR_TOWER_API_KEY` 非空 → 真，否则 mock |
| 游戏元信息(name/publisher/icon/release_date) | **iTunes Search API**(免费，真) | 始终真，失败回 `None` |
| 游戏发展历程 | **MOCK_HISTORIES** 优先 → **Claude API**(`claude-opus-4-7`) → `DEFAULT_HISTORY` 兜底 | `backend/app/services/ai_history.py:44-78` |
| 已追踪游戏列表 / 素材 / 时间轴 | **SQLite 本地**(真) | 始终真 |
| 配额计数 / Sensor Tower snapshot | **SQLite 本地**(真) | 始终真 |

### 前端 → 后端路由对照表

| Frontend | Backend |
|---|---|
| `gamesApi.list` | `GET /api/games/` (+ q/platform/country/publisher/sort_by/order/limit/offset) |
| `gamesApi.get` | `GET /api/games/{app_id}` |
| `gamesApi.create / update / delete` | `POST/PUT/DELETE /api/games/{app_id}` |
| `gamesApi.lookup` | `POST /api/games/lookup?app_id=...` (iTunes) |
| `gamesApi.rankings` | `GET /api/games/rankings` |
| `gamesApi.metrics` | `GET /api/games/{app_id}/metrics` |
| `gamesApi.syncRankings` | `POST /api/games/sync-rankings` |
| `gamesApi.refreshRankings` | `POST /api/games/rankings/refresh` |
| `historyApi.get / create / sync / delete` | `/api/history/*` |
| `materialsApi.list / create / update / delete` | `/api/materials/*` |
| `quotaApi.get` | `GET /api/quota/` |
| _(未在前端使用)_ | `GET /api/games/seed`、`GET /api/health/deep`、`GET /api/cache/stats` |

### 统一数据类型定义

⚠️ **没有跨前后端的 schema 共享**。后端用 Pydantic(`backend/app/schemas/`)，前端**全用 `any`**(`gamesApi.list: (params?: Record<string, any>) => Promise<any>`)。前端只有 `QuotaInfo` 一个手写接口在 `frontend/src/components/QuotaBanner.tsx:3-10`。

---

## 4️⃣ 主要问题清单

### 🔴 P0 — 数据正确性 / 隐性 Bug

| # | 问题 | 影响文件 | 风险 |
|---|---|---|---|
| B1 | **定时任务写入的 `game_rankings` 表从来没被任何 API 读过** —— scheduler 每天写，但 `/games/rankings` 永远走 cache/snapshot/mock 路径。等于每天写"死数据"。 | `backend/app/scheduler.py:15-49` · `backend/app/routers/games.py:57-59` | 高(浪费配额 + 历史趋势丢失) |
| B2 | **`/games/{id}/metrics` rankings 强制 country=US，downloads/revenue 用调用方传的 country(默认 WW)** —— 同一接口三条线用两个地理口径。 | `backend/app/routers/games.py:171-173` | 中(对比图含义错乱) |
| B3 | **`['rankings']` queryKey 在三页面用法不一致**:Dashboard `['rankings','US','ios']` · GameDetail 同 · Rankings 用 `['rankings', country, platform]` · **Materials 用裸 `['rankings']`** —— Materials 永远独立 fetch 一次同样的 US/iOS 数据。 | `frontend/src/pages/Materials.tsx:24-27` | 中(浪费请求 + 缓存失效不一致) |
| B4 | **GameDetail 通过今日榜单查游戏头部信息**，游戏掉出今日 Top 时，详情页头部会空。 | `frontend/src/pages/GameDetail.tsx:364-369` | 中 |
| B5 | **APScheduler 没显式设 timezone**，代码注释说 "02:30 UTC" 但 `CronTrigger(hour=2, minute=30)` 默认走 OS timezone。 | `backend/app/scheduler.py:69-84` | 中(部署时机可能漂) |
| B6 | **`datetime.utcnow()` 全文使用**(Python 3.12 deprecated;Sensor Tower 是 T+1 日级，时间不一致会影响 cache key 跨午夜) | scheduler.py · models · quota.py 等 | 低 |

### 🟡 P1 — UI / UX

| # | 问题 | 影响文件 | 风险 |
|---|---|---|---|
| U1 | **Dashboard 国家/平台硬编码 US/iOS**，无切换器。 | `frontend/src/pages/Dashboard.tsx:41` | 中 |
| U2 | **没有分页 UI**(后端已支持 `limit/offset/X-Total-Count`)，所有页面拉 `limit=200` 上限。 | Dashboard / Compare / GamesManage | 中(数据量大就崩) |
| U3 | **Rankings/Materials 的筛选是纯客户端 `.filter()`**，没用上后端的 `q` 参数。 | `frontend/src/pages/Rankings.tsx:26-29` | 低 |
| U4 | **"刷新数据"按钮不刷新 metrics 图**，只刷今日榜单。用户在详情页点也不会动。 | `frontend/src/lib/api.ts:38-39` | 中(预期不符) |
| U5 | **Dashboard 收入/下载柱图共用同一份 `revenueChartData`**，且 x 轴 label 在 10 字截断后可能重复。 | `frontend/src/pages/Dashboard.tsx:66-70` | 低 |
| U6 | **删除游戏 confirm 用 `window.confirm`**，样式不统一；无 undo。 | `frontend/src/pages/GamesManage.tsx:148-151` | 低 |
| U7 | **空状态提示文案弱**，Compare 未选游戏只有 "pickGames"，没说明 metric 含义、最多几个。 | `frontend/src/pages/Compare.tsx:156-160` | 低 |

### 🟠 P1 — 数据结构

| # | 问题 | 影响文件 | 风险 |
|---|---|---|---|
| D1 | **`event_date / release_date / GameRanking.date` 都是 `String(20)`**，排序/聚合靠 ISO 字符串字典序。 | models/ 所有 | 中(查询性能、跨月聚合) |
| D2 | **`Game.country` 只是单个值**，但实际业务一款游戏会在多个区域监控。 | `backend/app/models/game.py:16` | 中 |
| D3 | **`game_rankings` 没有联合唯一约束**，只靠 scheduler 内 "先删再插" 保证幂等 —— 并发触发同步会写重复。 | `backend/app/scheduler.py:23-31` · `backend/app/models/game.py:23-34` | 中 |
| D4 | **前端无 TS 类型** —— 所有 `gamesApi.list: ... => any`，字段拼错只能运行时炸。 | `frontend/src/lib/api.ts` | 中 |

### 🔴 P0 — 权限和安全

| # | 问题 | 影响文件 | 风险 |
|---|---|---|---|
| S1 | **单一 `API_KEY` 共享给所有用户**；不区分人，撤销/审计无能为力。 | `backend/app/security.py` · `frontend/src/lib/api.ts:4` | 高(团队协作场景) |
| S2 | **`VITE_API_KEY` 在前端构建期注入 → 打包进 JS bundle**，前端制品在浏览器里可见。 | `frontend/src/lib/api.ts:4-9` | 高(如对外暴露) |
| S3 | **`API_KEY` 未配置时所有端点免鉴权**，本地很爽，但生产忘了配就裸奔。 | `backend/app/security.py:13-14` | 中 |
| S4 | **无审计日志** —— 谁删了游戏 / 改了素材，没记录(尽管有 access log)。 | 全后端 | 中 |
| S5 | **CORS `allow_origins="*"` 默认值**，生产忘了改就开；尽管 `credentials=False` 时无法带 cookie，但 API Key 在前端可见时风险仍在。 | `backend/app/config.py:14` · `backend/app/main.py:40-49` | 中 |

### 🔴 P0 — API quota 风险

| # | 问题 | 影响文件 | 风险 |
|---|---|---|---|
| Q1 | **当月配额耗尽 → 返回 stale snapshot，但前端无视觉降级** —— banner 显示 100% 但用户以为数据是新鲜的。 | `backend/app/services/sensor_tower.py:111-116` · `frontend/src/components/QuotaBanner.tsx` | 高 |
| Q2 | **`get_rankings`(单 app 历史榜单)默认 `country="US"` hardcode**，跟 downloads/revenue 不一致(见 B2)；上层 `/metrics` 一次调用就消耗 3 次配额。 | `backend/app/services/sensor_tower.py:159-169` | 高 |
| Q3 | **Compare 页选 3 款游戏 × 1 个时间窗 = 9 次潜在配额消耗**(rankings + downloads + revenue × 3 games)；切换时间窗 cache key 变，**会再炸 9 次**；首次操作能干掉 27 配额。 | `frontend/src/pages/Compare.tsx:24-30` · `backend/app/services/sensor_tower.py:154-157` | 高 |
| Q4 | **力刷按钮无前端节流**(后端只 single-flight)，手抖连点就连续消耗。 | `frontend/src/pages/Dashboard.tsx:102-110` | 中 |
| Q5 | **`force_refresh_today_rankings` 只清两层缓存，但 metrics 的 cache key 不清** —— 力刷只刷今日榜不刷历史趋势，用户期望可能落空。 | `backend/app/services/sensor_tower.py:207-218` | 低 |
| Q6 | **配额超限的"消耗-回滚"非原子操作**(`INSERT...RETURNING` 然后超额时 `UPDATE count-1`):SQLite 单写器 OK；一旦换 Postgres + 多 worker，并发可能短暂超过 limit。 | `backend/app/services/quota.py:27-49` | 低 |

### 🟡 P1 — 代码结构

| # | 问题 | 影响文件 | 风险 |
|---|---|---|---|
| C1 | **缺少跨前后端的 schema 共享** —— Pydantic 输出转 TS 类型可用 `datamodel-code-generator` 或 `openapi-typescript`，目前是纯手工同步。 | 整个项目 | 中 |
| C2 | **`init_db` 在 lifespan 里用 sync alembic.command.upgrade** —— 短时阻塞 event loop，启动期间健康检查可能 timeout。 | `backend/app/database.py:21-26` | 低 |
| C3 | **未读路由 `/api/games/seed` / `/api/cache/stats` / `/api/health/deep` 没人调** —— 死代码或调试残留。 | `backend/app/main.py` · `backend/app/routers/games.py` | 低 |
| C4 | **i18n 翻译键用 `as keyof typeof` 强制转换**，新增 key 时容易遗漏其一语言。 | `frontend/src/pages/Materials.tsx:54` 等 | 低 |
| C5 | **AI 历程 `_parse_json_array` 解析很脆**(只识别 \`\`\`json)，模型返回非标 markdown 就 fallback 到 default —— 用户体验差。 | `backend/app/services/ai_history.py:34-41` | 低 |

### 🟠 P1 — 接入钉钉机器人的预期难点

| # | 难点 | 风险 |
|---|---|---|
| DD1 | **没有"昨天的快照"概念可对比** —— `game_rankings` 表写了但没人用，要做日报必须先决定:用 `game_rankings`(自己抓的)还是 `sensor_tower_snapshots`(顺手存的)做基线。 | 高 |
| DD2 | **没有异常检测规则引擎** —— 排名跳变/收入飙升 阈值要在哪配？目前 config 里没有任何业务阈值。 | 中 |
| DD3 | **没有"订阅 / 关注列表"概念** —— 全部 tracked games 一起播报会刷屏；需要 user 概念或 watchlist 表。 | 中 |
| DD4 | **钉钉 webhook 签名 + 加签字符串构造** —— 需要加 secret 配置项 + 签名工具函数；FastAPI 侧需要单独的 `notification` 服务模块。 | 低 |
| DD5 | **APScheduler 单进程内运行** —— 推送任务和数据同步抢同一个 scheduler；失败重试、幂等都没做(只有 `misfire_grace_time=3600`)。 | 中 |
| DD6 | **没有 outbound HTTP 重试 / 死信队列** —— 钉钉接口偶尔 5xx 就丢消息。 | 中 |
| DD7 | **没有用户身份 → 推送目标映射** —— "把 X 的关注推到 X 的钉钉群" 需要先有用户/群组模型。 | 高 |

---

## 5️⃣ 三阶段优化计划

> 每条:**影响文件 · 风险等级 · 工作量(人时)**
> 风险等级:🟢 低 / 🟡 中 / 🔴 高(指改动可能引入回归的风险)

### 🚀 阶段一 · 不改架构，只优化可用性(预估 12–18h)

| # | 项 | 影响文件 | 风险 | 工时 |
|---|---|---|---|---|
| 1.1 | 修 B3:统一 `['rankings', country, platform]` queryKey;Materials 用 `gamesApi.list()` 而非 rankings 做名字映射(B4 同时解决) | `frontend/src/pages/Materials.tsx` · `frontend/src/pages/GameDetail.tsx` | 🟢 | 1h |
| 1.2 | 修 B2 + Q2:`/metrics` 三条线统一 country 参数;前端 `metrics()` 默认值变成显式可选 | `backend/app/routers/games.py` · `frontend/src/lib/api.ts` · `frontend/src/pages/Compare.tsx` | 🟡 | 2h |
| 1.3 | 修 B5:scheduler 显式 `timezone="UTC"` | `backend/app/scheduler.py` | 🟢 | 0.3h |
| 1.4 | 修 U1:Dashboard 加 country/platform toggle，默认还是 US/iOS，但可切 | `frontend/src/pages/Dashboard.tsx` | 🟡 | 2h |
| 1.5 | 修 Q4:Refresh 按钮加 5s debounce + 当月超额时禁用(`quota.exhausted`) | `frontend/src/pages/Dashboard.tsx` | 🟢 | 0.5h |
| 1.6 | 修 Q1:QuotaBanner 100% 时叠加 "数据可能为快照，最后更新于 YYYY-MM-DD" → 后端 snapshot 路径加 `X-Snapshot-Updated-At` header | `backend/app/services/sensor_tower.py` · `frontend/src/components/QuotaBanner.tsx` | 🟡 | 2h |
| 1.7 | 修 U3:Rankings 把搜索词改成 `gamesApi.list({q})` 调用 + Rankings 改用本地 `game_rankings` 表的当日数据(为阶段二铺路) | `frontend/src/pages/Rankings.tsx` | 🟡 | 2h |
| 1.8 | 修 D4 一部分:为 `gamesApi` / `historyApi` / `materialsApi` 写显式返回类型(从 Pydantic schema 手抄一份 `types.ts`) | 新建 `frontend/src/lib/types.ts` · `frontend/src/lib/api.ts` | 🟢 | 2h |
| 1.9 | 修 S3:`API_KEY` 未配置时，生产环境(`SENTRY_ENVIRONMENT=production`)拒绝启动 | `backend/app/config.py` · `backend/app/main.py` | 🟡 | 0.5h |
| 1.10 | 修 U2:为 Rankings/Materials/GamesManage 列表加分页(后端已支持) | 三个 page tsx | 🟡 | 3h |
| 1.11 | 删 C3 死代码，或在 `/api/cache/stats` `/api/health/deep` 加 README 说明用途 | `backend/app/main.py` | 🟢 | 0.3h |
| 1.12 | 修 D3:`game_rankings` 表加联合 unique `(app_id, date, country, platform)` Alembic 迁移 | `backend/alembic/versions/` | 🟡 | 1h |

**预期收益**:配额浪费降一半;Dashboard 多国家可用;前端类型安全;数据降级时用户有感知。

---

### 📈 阶段二 · 业务洞察 / 异常检测 / 筛选(预估 30–45h)

| # | 项 | 影响文件 | 风险 | 工时 |
|---|---|---|---|---|
| 2.1 | **激活 `game_rankings` 表**:新增 `GET /api/rankings/history?date=YYYY-MM-DD&country&platform`，Dashboard / Rankings 优先读它，Sensor Tower 只在缺当日数据时调用 → 配额需求降 80%+ | `backend/app/routers/games.py` · `backend/app/scheduler.py` · `frontend/src/pages/Rankings.tsx` · `frontend/src/pages/Dashboard.tsx` | 🔴 | 8h |
| 2.2 | **日环比/周环比 deltas**:`GET /api/rankings/delta?days=1` 返回每个 app 的 rank/revenue/downloads 差值，前端列表加 ↑↓ 图标 | 新 router + Dashboard/Rankings | 🟡 | 5h |
| 2.3 | **异常检测引擎**(规则版):新 `app/services/anomaly.py`，规则配置存 JSON 文件，如 `rank_jump > 5` / `revenue_pct > 30%` / `new_in_top_20`；每日同步任务结束后扫一遍写 `anomaly_events` 表 | 新建 service + model + alembic + scheduler hook | 🟡 | 8h |
| 2.4 | **异常时间轴页面 `/anomalies`**:列出近 7/30 天所有触发的规则，可点进游戏详情 | 新 page tsx + API | 🟡 | 4h |
| 2.5 | **Watchlist** :`Game.is_watched` 布尔字段；UI 加星标按钮；Dashboard 顶部多一个 "我的关注" 切换 | `backend/app/models/game.py` · alembic · UI | 🟢 | 3h |
| 2.6 | **多维筛选 chips**(发行商 / 上线年份 / 标签):后端 `/games/` 已有 publisher 参数，补 release_year 范围 + tags any-of | `backend/app/routers/games.py` · `frontend/src/pages/Rankings.tsx` | 🟡 | 4h |
| 2.7 | **多国家并排对比**:Rankings 顶部支持多选国家，展示同一款游戏在不同国的排名条 | `frontend/src/pages/Rankings.tsx` | 🟡 | 4h |
| 2.8 | **修 B1 永久性**:把今日榜单的 `/games/rankings` 切到读 `game_rankings` 表(只在表里没有今日数据时回落 Sensor Tower)；跟 2.1 一起 | 同 2.1 | (含在 2.1) | — |
| 2.9 | **OpenAPI → TS 自动生成**:CI 步骤跑 `openapi-typescript`，产出 `types.gen.ts`；替换 1.8 的手抄 | `.github/workflows/ci.yml` · `frontend/src/lib/api.ts` | 🟢 | 3h |

**预期收益**:日常运营从"看数"升级到"找异常"；配额消耗结构性下降；Watchlist 为阶段三推送做用户基础。

---

### 🔔 阶段三 · 钉钉机器人 / 日报 / 权限体系(预估 40–60h)

| # | 项 | 影响文件 | 风险 | 工时 |
|---|---|---|---|---|
| 3.1 | **用户系统**:新 `User` 表(email / role: viewer/analyst/admin / dingtalk_id)，替换单 `API_KEY` 为 JWT/Session；`/api/auth/login` `/me`；前端 `<AuthGate>` | 新建 models/user.py · routers/auth.py · 整个前端 axios 拦截器改写 | 🔴 | 12h |
| 3.2 | **API Token 表**(给脚本/CI 用):每个 user 可创建多个 token，可单独撤销 | 同上 | 🟡 | 3h |
| 3.3 | **审计日志**:`AuditLog` 表 + 中间件，记录所有非 GET 请求的 user/action/target | 新 middleware · model · alembic | 🟡 | 4h |
| 3.4 | **钉钉服务模块** `app/services/dingtalk.py`:加签 webhook、Markdown 模板、重试 + 死信表 | 新建 service + model `outbound_messages` | 🟡 | 5h |
| 3.5 | **订阅 / 推送目标**:`Subscription` 表(user_id, watchlist_id, channels: [dingtalk_webhook, email], rule_ids) | 新 model · routers/subscriptions.py | 🟡 | 5h |
| 3.6 | **每日日报生成**:APScheduler 早 9:00 跑一次，基于 Watchlist + 异常事件输出 markdown，推送给订阅者 | scheduler.py · 新 templates 目录 | 🟡 | 6h |
| 3.7 | **实时异常推送**:阶段二 2.3 触发的异常，按 Subscription 实时推钉钉(批量去重，15min 窗口聚合) | 同上 | 🟡 | 5h |
| 3.8 | **报告导出**(PDF/HTML):任意时间范围 → 渲染 chart 截图 + 表格，适合发周会 | 新 router · 引入 weasyprint 或 puppeteer | 🟡 | 8h |
| 3.9 | **测试用钉钉发送沙箱**:管理后台一键 "发测试消息到我"，验证 webhook | UI + service | 🟢 | 1h |
| 3.10 | **scheduler 集群安全**:若未来要起多副本，把 APScheduler 换成 SQLAlchemyJobStore + 显式 lock，或拆出 worker 服务 | scheduler.py · docker-compose | 🔴 | 6h |

**预期收益**:从"工具"升级到"日常工作流"——团队成员主动接收洞察，审计/权限合规。

---

## 总结

**项目已经超过"原型期"**:基础功能完整、缓存配额护栏到位、CI/部署/备份齐全。

**最需要优先做的三件事**(回报最高 / 风险最低):
1. **阶段一 1.1 + 1.2 + 1.5 + 1.6**(共 ~6h)—— 修 queryKey、metrics country、refresh 节流、stale 视觉降级
2. **阶段二 2.1**(8h)—— 把 `game_rankings` 表激活，这是阶段三日报/异常检测的数据基础，也直接削掉配额
3. **阶段三 3.1**(12h)—— 用户系统:钉钉、订阅、watchlist 全都要它做地基

如果只能干一件:**阶段二 2.1**。它一次性解决配额风险 + 历史数据空洞 + 阶段三所需的数据底座。