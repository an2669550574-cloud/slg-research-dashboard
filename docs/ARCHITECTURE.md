# 架构说明（slg-research-dashboard）

> 「为什么是这样设计的」的权威说明。改动相关代码前必读，避免「修回去」踩坑。
> Runbook（怎么操作）在同目录其它 .md；产品业务知识在 [`PUBLISHERS.md`](PUBLISHERS.md)。

---

## Sensor Tower 配额体系

ST API 调用受**两层约束**：公司池 3000 次/月（多团队共享，真硬上限）+ 本项目 500 次/月（本地软护栏 `SENSOR_TOWER_MONTHLY_LIMIT`，防本项目 bug 烧穿公司池）。两者都要看，**做账以 3000 池为基准**。

### 同步节奏（省配额核心）

历史用量 ~360/月，经 PR #7 / #8 / #9 三轮压缩。**同步节奏是刻意调过的，别擅自加密**——每加密一档次市场就多吃公司池。

| 项 | 配置 | 值（code 默认 → **prod 实际**） |
|---|---|---|
| 全集 combo | `SYNC_RANKING_COMBOS` | 默认 6 组（US/JP/KR×ios/android）→ **prod `backend/.env` 扩到 10 组**（加 DE/RU×ios/android） |
| 主市场 | `SYNC_RANKING_COMBOS_PRIMARY` | `US:ios,US:android`（每日全量同步） |
| 主市场周期 | `SYNC_PRIMARY_INTERVAL_DAYS` | 1（US **每日**） |
| 次市场周期 | `SYNC_SECONDARY_INTERVAL_DAYS` | 默认 30 → **prod 覆盖为 7（周级）**；JP/KR/DE/RU 走这档 |
| 销量 | `SALES_FETCH_INTERVAL_DAYS` | 7（每个拉榜日顺带，且仅主市场） |
| 公司池水位 poll | `SENSOR_TOWER_ACCOUNT_USAGE_TTL_HOURS` | 1（PR #41 后；不计配额所以可频繁） |
| 本地硬上限 | `SENSOR_TOWER_MONTHLY_LIMIT` | 50（低于 backfill floor 150 → 回填自动停） |

**prod 有 env 覆盖**（`backend/.env`：`SYNC_RANKING_COMBOS`=10 组 + `SYNC_SECONDARY_INTERVAL_DAYS=7`）——改后须 `docker compose --env-file .env up -d backend` 才会重读（`restart` 不行）。当前池水位看 QuotaBanner / 状态记忆，别在此写死数字。回滚锚点：#8=`rollback-20260601-0255`、#9=`rollback-20260601-1143`。

**次市场「榜单同步滞后」横幅**（新品监测页）：数据按 `game_rankings` 的 `MAX(date) per combo` 算龄，≥3 天提示（琥珀）、≥14 天转红。次市场周级同步下，周期中段必然 ≥3 天 → 横幅常亮，**属设计内正常**（横幅已带安抚文案说明）。US 主市场每日同步，正常不进该横幅；若 US 也滞后才是真异常。

### 关键架构决策

**每次 sync 打几次 ST**：拉榜 1 次（`get_all_rankings_today` → `_cached_get`）+ 销量 1 次（`get_sales_batch`，Top20 批量一次拿全，仅销量到点日且主市场才打）。Android 补名字/图标走 Google Play 商品页抓取，**不吃 ST 配额**（只有 IP 封禁风险）。

**cadence 门控用纯函数 `date.toordinal() % interval_days == 0`**，无持久化游标，跨重启/多副本一致。代码在 `scheduler._due_by_interval` / `_combo_due_today` / `_sales_due_today`。

**销量仅主市场**：`_scheduled_sync` 里 `with_sales = is_primary and _sales_due_today(today)`，次市场恒 `False`。次市场（JP/KR/DE/RU）销量改走详情页**按需** ST（库未覆盖才打 1 次）。

**rank 长期趋势读本地 `game_rankings` 表（零配额）**——所以次市场周级同步也不影响趋势图，只是榜单"截至日"会旧。详情/对比页排名趋势**故意**走本地表，别"修"回 live ST。

**日榜兜底**：非抓取日榜行 dl/rev 落 NULL（库内诚实），读路径用该 app **上次已知值**兜底显示，**绝不回写库**。详情页趋势仍读真实 NULL 稀疏点。

### #8 堵掉的两处隐形泄漏（曾让低频同步形同虚设）

1. **dashboard 非同步日回退实时 ST**：`get_rankings` 原本严格 `date == today`，非同步日命中空 → 回退 `get_all_rankings_today` 实时打 ST。已改为**服务最近一次已同步的榜**（`func.max(date) where date <= today`），冷启动（该 combo 全无数据）才回退 ST。
2. **`/v1/api_usage` 绕过本地计数**：`quota._fetch_account_usage_live` 用裸 httpx、不走 `try_consume`。**该端点经实测不计公司池**（连打两次 org.usage 不动 vs featured/impacts 同窗口每次 +1），但仍可能消耗其它额度，靠 `ACCOUNT_USAGE_TTL_HOURS` 限频。当前 TTL=1h（仅挡前端轮询）。

### 公司池可见性 + 软预留（PR #41 / 2026-05-21）

`QuotaBanner` 双行展示：**公司账户本月 X/3000**（来自 `/v1/api_usage`，落 `sensor_tower_snapshots` 表 `cache_key=__sys:account_usage__`）+ **本项目 Y/500**（本地 `api_quota_monthly`）。容器色调由「更紧约束」决定，通常是公司线。

**TTL 演化的坑**：初始 6h → 配额收缩期按「该端点大概率自计公司池」的假设加码到 336h（14 天）→ **结果月初重置后拉到的 0/3000 快照"新鲜"半个月，横幅整月冻结 0%**，2026-06-11 当 bug 报上来。同日实锤端点不计公司池 → PR #41 把 TTL 降回 **1h**。**教训：用「保守假设」设的旋钮要在假设被实测推翻后回头改**。

**双闸门 try_consume**：先查 `_org_remaining_cached()`，公司池剩余 ≤ `SENSOR_TOWER_ORG_RESERVE`（默认 30）时直接返 False，不走本地计数。**目的是让出最后几次给其他团队**（不是怕 ST 拒，refund 已能兜）。无 account_usage 快照时保守放行。

**状态分类** `_classify_state(remaining)`：`normal`（>100）/ `low`（31~100）/ `reserved`（≤30）。常量 `SENSOR_TOWER_ORG_LOW_THRESHOLD=100`、`SENSOR_TOWER_ORG_RESERVE=30`。

**前端常驻 `<GlobalQuotaAlert />`**：normal 不渲染、low 黄条、reserved 红条；复用 `['quota']` queryKey 自动去重。文案：low="公司 ST 配额仅剩 N 次，本页面数据可能无法拉取到最新值"；reserved="公司 ST 配额已耗尽，本项目暂停调用 API；所有页面数据均来自历史快照"。

### 改 ST 逻辑前的检查清单

- [ ] 这次改动会新增多少 ST 调用？折算成月用量是多少？
- [ ] 能不能从本地 `game_rankings` / `games` 表出？（默认答案：能）
- [ ] 改 `SYNC_*_INTERVAL_DAYS` 之前——你确认要加密同步？理由是什么？
- [ ] 要补未完历史回填：临时把 `SENSOR_TOWER_MONTHLY_LIMIT` 调到 >150 再跑，补完调回。

调旋钮指引：
- 要 US 更勤：调小 `SYNC_PRIMARY_INTERVAL_DAYS`。
- 要恢复 JP/KR 自动销量：把它们加进 `SYNC_RANKING_COMBOS_PRIMARY`（会同时变每周）。

---

## 设计系统「情报终端」（Intelligence Console）

2026-05-19 起前端统一设计系统，由 vendored `anthropics/frontend-design` skill 驱动（`.claude/skills/frontend-design/`）。

### 五条硬约束（改 UI 前必读）

1. **字体只能自托管，永不引谷歌字体**。`frontend/public/fonts/` 放着 Bricolage Grotesque（展示）+ JetBrains Mono（数据）的 woff2；`index.css` 用 `@font-face`。曾用 `@import fonts.googleapis.com` → 境内用户访问 HK 服务器常被墙/超时，展示字根本不加载。**别再加任何外部字体 @import / link**。
2. **暗色是硬默认**。`lib/theme.ts` 未显式选择即 `dark`，不跟随系统亮色偏好（亮色下设计偏素，首因印象弱）。URL 参数 `?theme=light|dark` 可覆盖 localStorage（用于无头截图等场景，默认行为不变）。**别改回 prefers-color-scheme**。
3. **设计系统入口 = `components/PageHeader.tsx` + `index.css` 的 token/工具类**：`.font-display` / `.font-data` / `.eyebrow` / `.hud` / `.scan-rule` / `.glow-accent` / `.reveal*` / `.pulse-dot`，token `--accent` / `--signal` / `--border-strong`，tailwind `accent` / `signal` / `border-strong`、brand=电光 azure。新页面/组件**复用这些，别每页另造一套**。命名别和 Tailwind 生成类撞——踩过 `.ring-accent` vs `ring-accent`、`text-border-strong` 不是文字色。
4. **`.eyebrow` / `.font-data` / uppercase / 大字距 只能用于短英文母题文字**（PageHeader 的 `OVERVIEW` 等、终端序号 `01`）。**绝不套到中文 UI 标签 / 正文**（卡片标题、StatCard label/sub、类型名）——10px + 0.28em + 大写让中文又小又糊。已两次踩：Dashboard StatCard、素材卡 kicker。**中文一律普通 sans 可读字号**（`text-xs` / `text-sm` + `text-secondary` / `muted`）。
5. **页面骨架统一**：`px-4 sm:px-7 py-5 sm:py-7 max-w-[1500px] mx-auto` + 顶部 `<PageHeader eyebrow title subtitle stats?>{actions}</PageHeader>`。数字用 `tabular-nums` / `.font-data`。

### 部署 / 缓存

nginx：`/assets` 永久缓存、`index.html` `no-cache`（已在 `frontend/nginx.conf`）——部署即时可见，**别加回缓存**。

### 回滚锚点

| commit | 含义 |
|---|---|
| `5ac9d55` | 全站设计系统（当前形态） |
| `869f93f` | v2 仅素材页 |
| `0ac96d0` | v1 保守 |
| `7a7c156` | 改造前原始通用样子 |

### 预览/截图自查流程

临时把 `vite.config` 代理指 HK + `VITE_API_KEY` 跑 dev，Playwright 截图，**用完 `git checkout` 还原 vite.config 不提交**。

---

## 新品监测 + 每日情报 digest

新品监测（`services/newcomers.py` + `routers/newcomers.py` + 前端 `NewReleases.tsx`）和每日钉钉 digest（`services/release_alerts.py`，03:00 UTC 一张卡）共享一套「首次出现」检测核心，全程零 ST 配额、纯读本地 `game_rankings`。

### 检测核心 `_first_appearances`

锚定每个 combo 最近一次已同步快照（as_of，不强求等于今天——周级同步多数天无「今日」行），比对它与之前 W 个快照：当期出现、baseline W 个快照里没出现过 = 「首次出现」。

| 配置 | 值 | 含义 |
|---|---|---|
| `NEWCOMER_WINDOW` | 4 | 回看几个快照作 baseline。US daily ≈ 4 天，JP/KR/DE/RU weekly ≈ 4 周 |
| `NEWCOMER_TOPN` | 50 | 全市场新面孔：名次 ≤ 此值才算「新进榜」 |
| `PUBLISHER_NEWCOMER_TOPN` | 200 | 厂商主体新品：名次 ≤ 此值（比 50 宽——主体可信，名次较深也值得看，但砍 #201+ 长尾） |
| `NEWCOMER_HISTORY_TOPN` | 100 | 检出沉淀的**市场口径**（`market_newcomer_log`），比日报宽，页面可筛 Top50/100 |

> **检出沉淀取两路并集**（`record_market_newcomers`，按 app_id 去重）：市场口径 `detect_newcomers`（Top100）+ 已建档主体 `detect_publisher_newcomers`（Top200）。后者专门接住「冷启动名次深于 100、慢爬进榜时已被基线吞掉」的漏报（如 Century Games《Top General》首见 rank 144 > 100，旧逻辑永不入库）。日报推送口径（Top50）不受影响。

`no_baseline`（冷库/首次同步、无历史快照）一律返回空——绝不把首图全员当新品。

### is_reentry：真首发 vs 回归（PR #93/#94）

**weekly combo 的坑**：baseline 只 4 周，任何老 SLG 产品有一周漏榜，回来时就被判「首次出现」。实测 JP/android 单 combo 23 条 publisher 新品里 22 条是这种回归噪声。

`_first_appearances` 因此额外返回 `historical_ids`（baseline 窗口**之外**更早出现过的 app_id）；`_row_dict` 给每行打 `is_reentry`：

- **digest**（`build_newcomer_lines`）：`is_reentry=True` 的项**直接过滤掉**（先过滤再截断 10 条，避免回归占满名额）。digest 实测 45→24 项（-47%）。
- **检出沉淀**（`market_newcomer_log.is_reentry`，alembic 0022 起）：**保留**，让前端可区分展示。
- **前端**（`NewReleases.tsx`）：信号 chip「真首发(默认)/回归/全部」，`/history?signal=` 服务端筛；回归卡片打 cyan badge。
- **向后兼容**：0022 迁移前的历史行 `is_reentry=NULL`，`signal=true_new` 把 NULL 当真首发（老卡片照旧显示）。

### 缺口忽略名单过滤（2026-06-22）

全市场新品（`detect_newcomers`）+ 检出沉淀 + digest 复用 `publisher_ignores`（与 [`/gaps`](PUBLISHERS.md) **同一名单同一口径** `_tokens`+`corp_squash`），剔除**人工逐条确认的非 SLG 噪声**——误挂 App Store「strategy」标签的麻将 / 扑克 / 塔防 / 宝可梦对战等。

与上面「**故意不按 is_slg 过滤**」不冲突，是**两类信号的精准切分**：is_slg 白名单滞后维护、会漏掉真新厂（如新出海 SLG），按它过滤是误杀；忽略名单是逐条人工确认的非 SLG，过滤安全，且**不影响未建档的真 SLG**——不在名单里的新厂仍照常浮现（DEQU《Order of Kings》就是这样被新品监测捞出、2026-06-22 建档的）。`detect_publisher_newcomers`（已建档主体新品）天然 entity-scoped，不受影响。

**两处过滤口径（PR #99/#101）**：`detect_newcomers`（live `/` + 检出沉淀 sink + digest）在**检测时**过滤；`/history`（前端「全市场新面孔」视图读的就是这个）在**读时**过滤——后者让前端点「忽略」后该发行商行**立即消失**（日志行原样保留、不删表）。前端新品卡 is_slg=false 时同时给「建档」+「忽略」双动作：建档转主体、忽略写 `publisher_ignores`，与缺口卡同一闭环。

### 每日 digest 群推送封顶（PR #101）

`build_daily_digest` 原本无长度上限——波动大的日子（movement 完全无 cap）会刷出一张超长卡。两层封顶：单 combo movement 行封 `DIGEST_MOVEMENT_TOPN`（按 空降/窜升/暴跌/收入异动 重要性保留），全卡按 combo 粒度封 `DIGEST_MAX_ITEMS`，超额折叠成「…另有 N 项，看板查看全部」（不静默丢、标题 total 仍计全部）。商店按钮也纳入新品（市场/厂商各取头条、去重封顶 5），安卓包名拼 Google Play 链接（`_store_url`），纯新品日不再无可点项。

### digest 看板深链 + app_id 粒度忽略（PR #109）

**看板深链**：digest 每条新品行（市场/厂商）+ overflow 折叠行附 `🎯 看板定位` 链接，点击进新品页定位高亮该 app。后端 `_dashboard_focus_url(app_id, view)` 拼 `{DASHBOARD_BASE_URL}/newcomers?focus=<app_id>&view=<market|publisher>`。

- `DASHBOARD_BASE_URL`（看板对外基址，不含末尾斜杠；**敏感，见 `backend/.env` / `.env.example`，不进 git**）。**留空 = 不拼任何深链，digest 完全向后兼容**——后端无从得知自己的公网址（躲在 caddy 后），必须人工配。改该 env 后须 `compose --env-file .env up -d backend` 重读（`restart` 不生效）。
- 前端 `NewReleases.tsx`：mount effect 从 `window.location.search` 读 `focus`/`view`，切视图 → 轮询等滚动容器 `<main>` 布局完成（首帧 `clientHeight` 可能为 0，`scrollIntoView` 此时空操作）→ instant 滚动定位 → CSS `focus-flash` 高亮淡出（含 `prefers-reduced-motion` 降级）。**深链参数只能在 mount effect 里读、不能用 `useState` 惰性初始化**——lazy 路由 + Suspense 下惰性初始化有取值竞态（实测 `focus` 取到 null）。

**app_id 粒度忽略**：新品卡「忽略」按钮在有发行商名时下拉两选项——「忽略整个发行商」（`kind=publisher`，corp_squash 归一覆盖全厂）/「仅忽略此 app」（`kind=app_id`，只滤这一款）；无名退回 app_id 粒度。复用既有 `POST /publishers/ignores`，零新接口、零迁移。

### 数据新鲜度

`/history` 返回 `as_of_by_combo`（各 combo 最近快照日，来自 `game_rankings.MAX(date)`）；前端给 ≥3 天滞后的 combo 渲染 stale 提示条，≥14 天转红。让用户看清「JP weekly 数据截至 N 天前」而非误以为是今日榜。

### 应用商店雷达（互补层）

`/newcomers/appstore`（`itunes_releases.py` + `gp_releases.py`）：扫已建档主体的开发者账号清单 diff，捞**未上榜的软启动新品**——榜单检测永远看不到的早期信号。免费 iTunes lookup / GP 页 JSON-LD，零 ST 配额。详见 [`PUBLISHERS.md`](PUBLISHERS.md) 辅助端点表。

**两侧「国家」口径不对称（钉钉卡片文案别误读）**：iOS 走 `itunes lookup?country=<sf>`，`country` 是硬过滤——只返回该 storefront **真能搜到/下到**的 app，逐区轮询，`storefronts` 列即真实可见区（卡片显示「可见区 US」「⚠️ 仅 JP 可见」可信）。GP 走开发者主页 `/store/apps/dev?id=...&gl=us`，这页本质是**该开发者全量目录**，`gl=us` 只影响语言/货币、对逐国过滤很弱，`storefronts` 列恒为 `gp`。故 GP **无可靠逐国信号**，卡片只标「🤖 Google Play · 美区视角」（= 我们从美区查到的口径），**不等于美区在架**——别把它当真实上架国去「修」成具体国家。

### 榜类型 chart_type（ADR 0001，alembic 0026）

`game_rankings` 有 `chart_type` 维度（`'grossing'` 收入榜 / `'free'` 下载榜），唯一约束含 chart_type（五元组）。**改任何读 `game_rankings` 的查询必看**：所有「收入榜口径」读路径（今日榜 / 详情趋势 / movement / 厂商聚合 / sibling）都**显式过滤 `chart_type='grossing'`**（用 `app.models.game.CHART_GROSSING` 常量），两榜不得混入同一趋势/聚合。下载榜采集由 `FREE_CHART_COMBOS`（空=全关，默认关）门控、`with_sales=False`、`board='free'`，与收入榜同 cadence。

**新品检测按 chart_type 各自 baseline（切片 2）**：`_first_appearances`/`detect_newcomers`/`detect_publisher_newcomers` 接 `chart_type` 参数（默认 grossing）。`record_market_newcomers` 对开了下载榜的 combo 两榜各检出各落库（`market_newcomer_log.chart_type`，alembic 0027，四元组唯一）。`/newcomers/history?chart=grossing|free|all`（默认 grossing，前端零回归）。

**digest 下载榜段只推 is_slg=True**：`build_free_newcomer_lines`（⬇️【下载榜新品 · SLG】段）按 is_slg 门控钉钉推送——下载榜噪声大，非 SLG 仍入库 + 看板可见但不进卡片（口径差异刻意，与「收入榜故意不按 is_slg 过滤」相对）。详见 [ADR 0001](adr/0001-rankings-chart-type-free-chart.md)。

**前端（切片 3）**：新品页加「收入榜 / 下载榜 / 两榜」筛选 chip（默认收入榜，对应 `/history?chart=`），下载榜检出卡片打 ⬇️「下载榜」徽标（`GroupedNewcomer.anyFree`）。

### 竞品新品实机玩法视频自动搜集（ADR 0002，alembic 0029）

新品检出后定时搜 YouTube 实机玩法视频候选落库，前端新品抽屉展示 + 人工删噪。独立 daily job（03:50 UTC）`services/newcomer_video.py::sync_newcomer_videos` 调 **YouTube Data API**（独立配额、零 ST，`YOUTUBE_API_KEY` 在 `backend/.env`）。query **游戏名加引号精确匹配**防通用/短名拆词噪声（prod 实测 `탑 로드` 裸搜全是 Million Lords/赛马娘）。两表 `newcomer_video`（候选）+ `newcomer_video_search`（搜索台账=去重锚点 + 当日上限 80）；残余同名噪声靠前端「删」。详见 [ADR 0002](adr/0002-newcomer-gameplay-video-autosearch.md)。

### tracked iOS 竞品版本变更追踪（ADR 0003，alembic 0030+0031）

每日 digest 流程开头内联 `services/version_tracker.py::check_tracked_versions` 重查 tracked iOS games 的 iTunes 版本（零 ST、批量 lookup），变了写 `game_histories(event_type='version')` + 进 digest「版本更新」全局段 + 详情页时间线（前端 `EVENT_TYPE_CONFIG` 已渲染）。首次填基线不算变更（防刷屏）。**iOS-only**（GP 页无版本源）。HK tracked games 多用 **GP 包名**作 app_id、iTunes 查不到 iOS，靠 `Game.ios_track_id`（人工核对的精确 trackId）补；没补的跳过（弃 iTunes search 兜底——同名歧义大）。详见 [ADR 0003](adr/0003-ios-version-tracking.md)。

---

## 标签库 + 产品作用域（PR #113，alembic 0024+0025）

素材标签库是「一级维度（dimension） → 二级选项（option）」两层结构。一级维度可以是
text 型（下挂枚举值，如「路型」「角色」）或 date 型（打标签时选日期，如「投放时间」）。

### 产品作用域（per-product scope）

每个**维度和选项各自**可挂一份产品作用域名单（junction 表 `tag_dimension_products` /
`tag_option_products`，FK→ `tag_dimensions.id` / `tag_options.id`，ondelete=CASCADE）。
**空名单 = 通用**（对所有产品可见，= 现有种子 7 维度 48 选项的默认状态）；非空 = 仅
名单内 `app_id` 可见。门禁白名单语义：填了名单就只放名单内。

打标签时维度+选项两层叠加过滤（`GET /api/tags/dimensions?app_id=<X>`）：

1. 维度层：`无 dim 名单 OR dim 名单含 X` → 收敛 dimensions
2. 选项层：在显示出的维度内，`无 opt 名单 OR opt 名单含 X` → 收敛 options

典型场景：「角色」维度通用，但「爱丽丝」只属于 A 游戏、「鲍勃」只属于 B 游戏；
看 A 的素材打标签时角色维度只列爱丽丝，看 B 时只列鲍勃，互不混淆。

### API 接口语义

`POST /api/tags/dimensions` / `PUT /api/tags/dimensions/{id}`（option 端点同套）
入参 `app_ids: list[str]` 三态：
- **不传字段（None）= 不动**：partial update，保留现有作用域名单
- **空数组 `[]` = 改回通用**：清空名单
- **非空数组 = replace-all**：去重 + 保序覆盖

`GET /api/tags/dimensions` 接 `?app_id=<X>`：
- 给 `app_id`（打标签 / 浏览态）→ 按作用域过滤
- 不给（管理态）→ 返回全部，响应里每条带 `app_ids: list[str]`，前端据此渲染
  「通用 / 仅 N 个产品」徽标

`GET /api/tags/aggregate?dimension_id=<D>&app_id=<X>` 的桶集合同样按选项作用域收敛
——口径与 Materials 分面栏、打标签编辑器一致，避免「分面里看不见的标签在聚合里出现」。

`PUT /api/tags/scope/batch`（S4，产品视角批量改作用域）：入参
`{ dimensions: [{id, app_ids}], options: [{id, app_ids}] }`，一次原子事务里对每条做
replace-all（与单条 PUT 同语义），前端只发改动行。任一 id 不存在 → 404 整体回滚（不
静默跳过，避免前端脏状态被掩盖）。解决「标签多、逐个改单产品作用域麻烦」的批量诉求。

### 现役 UI 入口

- **标签库管理页**（`TagsManage.tsx`）：维度编辑面板「适用产品」picker（搜索 + chip
  + 滚动候选）；二级标签 chip 内嵌「⚙ N / Globe」入口点开 modal 编辑选项作用域。
- **标签库管理页·产品视角**（S4，同页「标签视角 / 产品视角」切换）：选一个产品 →
  一屏列出所有维度+选项，每行勾选框做 **通用 ⇄ 该产品专属** 的翻转，底部一次保存
  （走上面的 batch 端点）。语义刻意只覆盖这一种干净翻转：**多产品 / 属于别的产品的
  复杂作用域只读展示（🔒 + 产品名），不让一键勾选误覆盖**——白名单是加法语义，
  一键 clobber 会抹掉别人名单，故这类仍回「标签视角」精细改。
- **素材库** / **AI 解析** 编辑面板：`StructuredTagEditor` 接 `appId` prop，
  当前素材所属游戏自动收敛维度+选项。
- **素材库分面栏**：`facetable` `useQuery` key 含 `filterGame`，选游戏时按作用域收敛。

### 级联清理（应用层显式）

SQLite 默认不强制 FK 级联，删一级维度时显式连带：`material_tag_values` → 该维度下
所有选项的 `tag_option_products` → `tag_options` → `tag_dimension_products` → 维度本体。
删二级选项时显式删 `tag_option_products`。详见 `backend/app/routers/tags.py` 删除端点。

---

## 相关文档

- [`PUBLISHERS.md`](PUBLISHERS.md) — 厂商主体方法论 + 资本系速览（业务知识）
- [`DEPLOY.md`](DEPLOY.md) / [`ROLLBACK.md`](ROLLBACK.md) / [`BACKUP.md`](BACKUP.md) / [`MIGRATION.md`](MIGRATION.md) — 运维 runbook
- [`ANALYSIS.md`](ANALYSIS.md) — 素材 AI 分析流程
