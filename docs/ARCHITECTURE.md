# 架构说明（slg-research-dashboard）

> 「为什么是这样设计的」的权威说明。改动相关代码前必读，避免「修回去」踩坑。
> Runbook（怎么操作）在同目录其它 .md；产品业务知识在 [`PUBLISHERS.md`](PUBLISHERS.md)。

---

## Sensor Tower 配额体系

ST API 调用受**两层约束**：公司池 3000 次/月（多团队共享，真硬上限）+ 本项目 200 次/月（本地软护栏 `SENSOR_TOWER_MONTHLY_LIMIT`，防本项目 bug 烧穿公司池）。两者都要看，**做账以 3000 池为基准**。

### 同步节奏（省配额核心）

历史用量 ~360/月，经 PR #7 / #8 / #9 三轮压缩。**同步节奏是刻意调过的，别擅自加密**——每加密一档次市场就多吃公司池。

**当前真实月用量（2026-06-29 核算）**：节奏扩到 10 combo 收入榜 + US/JP/KR 下载榜后，自动同步约 **156/月（次市场双周）/ 182/月（次市场周级）**——拆账见下表。此前本地上限误设 100（6-combo 时代遗留），导致每月中旬（~16-17 号）就烧穿、后半月全站回退历史快照；本次抬到 **200** 并默认关闭历史回填修复。占公司池仅 ~6%，软预留护栏仍兜底。

**月用量拆账**（30.4 天/月，周级≈4.3 次/月、双周≈2.2 次/月；每个 combo 每同步日：收入榜列表 1 + 销量 1[仅主市场到点日] + 下载榜列表 1[仅 `FREE_CHART_COMBOS`]）：

| 类别 | combo 数 | 频率 | 小计/月 |
|---|---|---|---|
| 收入榜列表·主（US×2） | 2 | 每日 | 60.8 |
| 收入榜列表·次（JP/KR/DE/RU×2） | 8 | 周级→双周 | 34.4→17.2 |
| 收入榜销量·主（US×2） | 2 | 周级 | 8.6 |
| 下载榜列表·主（US×2） | 2 | 每日 | 60.8 |
| 下载榜列表·次（JP/KR×2） | 4 | 周级→双周 | 17.2→8.7 |
| **自动同步合计** | | | **182→156** |

US 每日双榜（121.6/月，占 67%）是新品检出命脉、不可砍；省配额最安全杠杆=**次市场 7→14（双周）**，省 ~26/月、纯 `.env` 松绑。

| 项 | 配置 | 值（code 默认 → **prod 实际**） |
|---|---|---|
| 全集 combo | `SYNC_RANKING_COMBOS` | 默认 6 组（US/JP/KR×ios/android）→ **prod `backend/.env` 扩到 10 组**（加 DE/RU×ios/android） |
| 主市场 | `SYNC_RANKING_COMBOS_PRIMARY` | `US:ios,US:android`（每日全量同步） |
| 主市场周期 | `SYNC_PRIMARY_INTERVAL_DAYS` | 1（US **每日**） |
| 次市场周期 | `SYNC_SECONDARY_INTERVAL_DAYS` | 默认 30 → **prod 覆盖为 7（周级）**；JP/KR/DE/RU 走这档。**建议放宽到 14（双周）** 在 200 上限下留余量（省 ~26/月） |
| 销量 | `SALES_FETCH_INTERVAL_DAYS` | 7（每个拉榜日顺带，且仅主市场） |
| 公司池水位 poll | `SENSOR_TOWER_ACCOUNT_USAGE_TTL_HOURS` | 1（PR #41 后；不计配额所以可频繁） |
| 本地硬上限 | `SENSOR_TOWER_MONTHLY_LIMIT` | **200**（覆盖 ~156-182/月自动同步 + 手动余量；回填已默认关、不再靠它挡停 backfill） |
| 历史回填 | `RANK_BACKFILL_ENABLED` | **False（默认关）**——一次性活已补齐；抬高月上限后若开会自动复活吃余量 |

**prod 有 env 覆盖**（`backend/.env`：`SYNC_RANKING_COMBOS`=10 组 + `SYNC_SECONDARY_INTERVAL_DAYS`）——改后须 `docker compose --env-file .env up -d backend` 才会重读（`restart` 不行）。**部署本次改动后核对**：仪表盘「本项目」应显示 `Y/200`；若仍 `Y/100`，说明 prod `backend/.env` 有显式 `SENSOR_TOWER_MONTHLY_LIMIT=100` 覆盖，删掉该行再重读。次市场双周需把 `.env` 的 `SYNC_SECONDARY_INTERVAL_DAYS=7` 改 `14`。当前池水位看 QuotaBanner / 状态记忆，别在此写死数字。回滚锚点：#8=`rollback-20260601-0255`、#9=`rollback-20260601-1143`。

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

`QuotaBanner` 双行展示：**公司账户本月 X/3000**（来自 `/v1/api_usage`，落 `sensor_tower_snapshots` 表 `cache_key=__sys:account_usage__`）+ **本项目 Y/200**（本地 `api_quota_monthly`）。容器色调由「更紧约束」决定，通常是公司线。

**TTL 演化的坑**：初始 6h → 配额收缩期按「该端点大概率自计公司池」的假设加码到 336h（14 天）→ **结果月初重置后拉到的 0/3000 快照"新鲜"半个月，横幅整月冻结 0%**，2026-06-11 当 bug 报上来。同日实锤端点不计公司池 → PR #41 把 TTL 降回 **1h**。**教训：用「保守假设」设的旋钮要在假设被实测推翻后回头改**。

**双闸门 try_consume**：先查 `_org_remaining_cached()`，公司池剩余 ≤ `SENSOR_TOWER_ORG_RESERVE`（默认 30）时直接返 False，不走本地计数。**目的是让出最后几次给其他团队**（不是怕 ST 拒，refund 已能兜）。无 account_usage 快照时保守放行。

**状态分类** `_classify_state(remaining)`：`normal`（>100）/ `low`（31~100）/ `reserved`（≤30）。常量 `SENSOR_TOWER_ORG_LOW_THRESHOLD=100`、`SENSOR_TOWER_ORG_RESERVE=30`。

**前端常驻 `<GlobalQuotaAlert />`**：normal 不渲染、low 黄条、reserved 红条；复用 `['quota']` queryKey 自动去重。文案：low="公司 ST 配额仅剩 N 次，本页面数据可能无法拉取到最新值"；reserved="公司 ST 配额已耗尽，本项目暂停调用 API；所有页面数据均来自历史快照"。

### 改 ST 逻辑前的检查清单

- [ ] 这次改动会新增多少 ST 调用？折算成月用量是多少？
- [ ] 能不能从本地 `game_rankings` / `games` 表出？（默认答案：能）
- [ ] 改 `SYNC_*_INTERVAL_DAYS` 之前——你确认要加密同步？理由是什么？
- [ ] 要补未完历史回填：临时把 `RANK_BACKFILL_ENABLED=True` + `SENSOR_TOWER_MONTHLY_LIMIT` 调到 >`RANK_BACKFILL_QUOTA_FLOOR`(150) 再跑，补完两者都调回（回填默认关，见上）。

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

### movement「空降」回归门控（is_reentry，P1.4）

上面是 **newcomers**（全榜首次出现）的回归判定；**movement**（`detect_movement`，收入榜 Top `COMPETITOR_ALERT_TOPN`=20 进退）另有同名异源的回归问题：它只比 today vs **上一可用日**两快照，老 SLG 短暂跌出 Top20 又回来会被错标「🆕 空降」并打高分顶上今日要闻（prod 实测 US/iOS top 榜 ~32% app 有出榜又回缺口；某日 2 个 new_entrant **全是**回归）。

`detect_movement` 加一道历史窗：上一可用日**之前** `COMPETITOR_REENTRY_WINDOW_DAYS`(默认 30) 天内曾在 Top20 的 app_id → new_entrant 打 `is_reentry=True`（每 combo 多一条本地查询，零 ST；窗口取 `[cutoff, prev_date)` 避开当期对比日；`=0` 关此判定；只看 `rank<=topn` 的历史，仅榜尾出现过不算回归）。渲染层（`release_alerts.py`）：文案「🆕 空降」→「🔄 重回」（`build_movement_lines` + `_highlight_line` 两处），重要度 ×`_REENTRY_PENALTY`(0.4) **降权**——高名次回归仍可冒头、不硬排除，但不再压过真首发污染今日要闻。surge/drop/revenue_spike 无回归概念、不受影响。

### 空卡分支：平淡日心跳 vs 数据未就位告警（P1.1）

`send_daily_digest` 原本 maintainer 卡为空时只 `logger.info` 静默——掩盖了「同步烧穿/失败导致全卡空」与「真平淡日」的本质区别。现以**硬锚核心 US/iOS**（`_core_synced`：该 combo `movement` 非 None 或 `market.as_of==today` = 今日有新快照）二分：

- **数据未就位**（核心 US/iOS 今日无快照）：`logger.error`→Sentry + 发克制维护者兜底卡（`build_data_not_ready_card`）。**不受开关控制**——这是管道故障告警。次市场双周非同步日不会误触发（US/iOS 每日有数据 → 卡非空 → 根本不进此分支）；仅「全卡空 + US/iOS 今日无快照」= 真故障时触发。
- **真平淡日**（核心已同步、确无事）：默认静默；`DIGEST_HEARTBEAT_ENABLED`(默认 False) 开才发「今日平静」心跳卡（`build_heartbeat_card`，两群同发）。**推领导群后再开**——领导看不到卡会误读「是不是坏了」；测试群只有本人、天天收无聊心跳没意义。

### 缺口忽略名单过滤（2026-06-22）

全市场新品（`detect_newcomers`）+ 检出沉淀 + digest 复用 `publisher_ignores`（与 [`/gaps`](PUBLISHERS.md) **同一名单同一口径** `_tokens`+`corp_squash`），剔除**人工逐条确认的非 SLG 噪声**——误挂 App Store「strategy」标签的麻将 / 扑克 / 塔防 / 宝可梦对战等。

与上面「**故意不按 is_slg 过滤**」不冲突，是**两类信号的精准切分**：is_slg 白名单滞后维护、会漏掉真新厂（如新出海 SLG），按它过滤是误杀；忽略名单是逐条人工确认的非 SLG，过滤安全，且**不影响未建档的真 SLG**——不在名单里的新厂仍照常浮现（DEQU《Order of Kings》就是这样被新品监测捞出、2026-06-22 建档的）。`detect_publisher_newcomers`（已建档主体新品）天然 entity-scoped，不受影响。

**两处过滤口径（PR #99/#101）**：`detect_newcomers`（live `/` + 检出沉淀 sink + digest）在**检测时**过滤；`/history`（前端「全市场新面孔」视图读的就是这个）在**读时**过滤——后者让前端点「忽略」后该发行商行**立即消失**（日志行原样保留、不删表）。前端新品卡 is_slg=false 时同时给「建档」+「忽略」双动作：建档转主体、忽略写 `publisher_ignores`，与缺口卡同一闭环。

### 每日 digest 群推送封顶（PR #101）

`build_daily_digest` 原本无长度上限——波动大的日子（movement 完全无 cap）会刷出一张超长卡。两层封顶：单 combo movement 行封 `DIGEST_MOVEMENT_TOPN`（按 空降/窜升/暴跌/收入异动 重要性保留），全卡按 combo 粒度封 `DIGEST_MAX_ITEMS`，超额折叠成「…另有 N 项，看板查看全部」（不静默丢、标题 total 仍计全部）。商店按钮也纳入新品（市场/厂商各取头条、去重封顶 5），安卓包名拼 Google Play 链接（`_store_url`），纯新品日不再无可点项。**全局段同款封顶（#141）**：实机视频段封 `DIGEST_VIDEO_TOPN`(5)、单 combo 市场「待识别新厂」(is_slg=false) 行封 `DIGEST_MARKET_LEAD_TOPN`(3)，超额各折叠成「…另有 N 个…」一行（次市场周级同步日一次涌进几十个未识别新面孔/几十条视频，逐条列会刷长卡；实测峰值卡 10772→7509 字符 −30%，榜单异动原样保留）。

### digest 看板深链 + app_id 粒度忽略（PR #109）

**看板深链**：digest 每条新品行（市场/厂商）+ overflow 折叠行附 `🎯 看板定位` 链接，点击进新品页定位高亮该 app。后端 `_dashboard_focus_url(app_id, view)` 拼 `{DASHBOARD_BASE_URL}/newcomers?focus=<app_id>&view=<market|publisher>`。

- `DASHBOARD_BASE_URL`（看板对外基址，不含末尾斜杠；**敏感，见 `backend/.env` / `.env.example`，不进 git**）。**留空 = 不拼任何深链，digest 完全向后兼容**——后端无从得知自己的公网址（躲在 caddy 后），必须人工配。改该 env 后须 `compose --env-file .env up -d backend` 重读（`restart` 不生效）。
- 前端 `NewReleases.tsx`：mount effect 从 `window.location.search` 读 `focus`/`view`，切视图 → 轮询等滚动容器 `<main>` 布局完成（首帧 `clientHeight` 可能为 0，`scrollIntoView` 此时空操作）→ instant 滚动定位 → CSS `focus-flash` 高亮淡出（含 `prefers-reduced-motion` 降级）。**深链参数只能在 mount effect 里读、不能用 `useState` 惰性初始化**——lazy 路由 + Suspense 下惰性初始化有取值竞态（实测 `focus` 取到 null）。

**app_id 粒度忽略**：新品卡「忽略」按钮在有发行商名时下拉两选项——「忽略整个发行商」（`kind=publisher`，corp_squash 归一覆盖全厂）/「仅忽略此 app」（`kind=app_id`，只滤这一款）；无名退回 app_id 粒度。复用既有 `POST /publishers/ignores`，零新接口、零迁移。

### digest 渲染格式 + 手机端链接可达性（PR #133）

**渲染格式（钉钉 markdown 坑）**：新品行的「meta 引用块 + 📝摘要 + 🔗/🎯链接 + 📰文章」若用单 `\n` 续行，钉钉**手机端**会把引用块后的续行 lazy-continuation 吸进同一引用块、并把换行折叠成空格 → 全黏成一坨（真机样卡验证）。故各「段」必须用 `\n\n` 空行分隔（`_block` helper），meta 用 `_meta_inner`（纯内容）拼独立 `> ` 引用段；movement 行无后续续行、仍用 `_meta_line` 行尾 `\n> ` 拼接（不黏）。链接合并成一行（`_link_line`：🎯看板 · 📰文章）减少续行。开头 `_digest_tldr` 一句话总览（异动/新品/版本/新区/视频/待建档计数），领导先有锚点。combo 标题用 `_market_label`（不带「畅销榜」后缀），避免与下属【下载榜新品】子段口径打架。国旗 / `---` / `> ` 引用块真机渲染正常，无需规避。

**手机端链接可达性（关键约束）**：公司网络下钉钉**电脑端能开外网、手机端打不开**（App Store / Google Play / YouTube）。钉钉 ActionCard 是同一份 markdown、**无法按客户端区分链接**，故按可达性分类标注：

- **外网链接（手机端死链）**：🔗 商店页（`_link_line`）/ 🎬 视频（`build_video_lines`）→ 加 `💻` 标识 + 卡片底部图例（`💻 = 需电脑端打开`，仅当卡里有 💻 时挂）。
- **两端可达（看板实为间歇）**：🎯 看板（`slg.*.nip.io` 自建·公网 HTTPS）/ 📰 微信文章（`mp.weixin.qq.com` 国内）→ 不标。⚠️ 看板深链对国内手机实为**间歇可达**非稳定（见下「连锁限制」末条）。
- **底部 ActionCard 按钮**：从商店直链改 **看板深链**（`_dashboard_focus_url`，两端可达、手机也能点），只取头条新品——movement 异动老游戏不在看板新品页、深链定位不到，不进按钮；商店直链在行内保留带 💻（不丢电脑端入口）。未配 `DASHBOARD_BASE_URL` 则无按钮，ActionCard 降级 markdown。
- **连锁限制**：看板详情页里的国外资源（商店截图 mzstatic 图床 / YouTube 视频）手机端在看板内也可能加载不全；视频「播放」本质要客户端能访问 YouTube，无解、只能电脑端。
- **⚠️ 看板深链「两端可达」是乐观假设（2026-06-28 排障证伪）**：HK 境外 IP → 国内手机（移动数据/无代理）跨境拉前端 JS bundle（主包 ~373KB）链路不稳，钉钉 webview 传输中途 `client disconnected` → React 挂载不了 → **整页白屏，连自有文字情报都看不到**；**间歇性**（链路好时能开、差时白屏）。诊断法：`docker logs slg_caddy` 查手机 UA(`AliApp(DingTalk)`) 的 `aborting with incomplete response`。备选治理（均未做，2026-06-28 决议**先观察暂不修**）：① 看板链接也标 💻 ② 后端轻量服务端分享页（绕开重型 SPA，最对症）③ Cloudflare 免备案 CDN ④ 腾讯云跨境加速。**约束**：服务器必须境外（ST API）→ 不能搬国内；nip.io 裸 IP → 国内 CDN 备案走不通。

代码集中在 `build_daily_digest` 拼装层 + `_block` / `_meta_inner` / `_link_line` / `_digest_tldr` helper（`services/release_alerts.py`）。**本轮（2026-06-28）已落地**：重要度排序 + 今日要闻（见下节）、领导群/维护者群双卡分发 + markdown 转义（见「双卡分发」节）、对标我方哪款（见末节）、实机视频/市场待识别折叠减负（#141，见「封顶」节）、领导卡只看 SLG 产品（#143，见「双卡分发」节）。**剩余 digest backlog**：全局段统一封顶预算、emoji 收敛（多个「新」语义重叠）、跨 combo 新品按 app_id 去重、领导卡推送时点前移。（**已落地**：空卡心跳/数据未就位告警 P1.1、movement 空降补 is_reentry 门控 P1.4——见「is_reentry」「空卡分支」节。）

### digest 重要度排序 + 今日要闻置顶（PR #136）

**痛点**：此前 digest 的五处「砍尾」——combo 段排序 / 全局封顶 `DIGEST_MAX_ITEMS` / 单 combo movement 封顶 `DIGEST_MOVEMENT_TOPN` / 商店按钮取头条 / overflow 折叠——一律按 `sync_combos_list` 的**地理顺序**或 movement 的**固定类序**（空降→窜升→暴跌→收入异动）砍。后果：① 末类的大额收入异动会被前类的榜尾长尾空降挤出 movement TopN；② 次市场高名次新品永远排不进 5 个按钮名额；③ 跨 combo 没有「今天最该看的几件事」入口。

**统一打分**（`_event_score` × `_market_weight`，`services/release_alerts.py`）喂这五处：

- `_event_score(kind, e)`：单事件「强度」分（不含市场权重）。`_rank_height(rank)`（名次越靠前权重越大，0..1）做主轴，叠收入异动 `|pct|` / 窜升跳数。相对序拍定：高名次收入异动 > 头部空降/市场新品 > 大幅窜升 > 榜尾长尾空降/跌出（`test_digest_importance_event_score_ordering` 锁死）。
- `_market_weight(country, platform)`：市场权重 US 1.5 / JP 1.15 / KR 1.1…× 平台 iOS 1.0 / 安卓 0.9。**刻意压窄到 1.0~1.5**——只做轻微倾斜，不能把事件强度整个吃掉（否则今日要闻被核心市场榜尾占满）；KR 的 #1 空降仍压过 US 的 #45 长尾（`test_digest_importance_market_weight_is_gentle_tilt`）。
- **五处的修法**：① combo 段按 `_combo_sort_key`（市场权重为**主键**、combo 内最高单项为辅）排序——核心 US/iOS 永居前列、全局封顶砍的必是次市场；② `build_movement_lines` 内按 `_event_score` 降序再切 `DIGEST_MOVEMENT_TOPN`（combo 内市场权重恒定，故只按事件强度）；③ 按钮 `_ranked_newcomer_buttons` 全局按 `_event_score × 市场权重` 排序取头部新品；④ overflow 计数不变，但砍的已是真·次要项。「核心 combo 永不被封顶挤掉」由排序主键保证，与市场权重量级解耦。
- **跨 combo「📌 今日要闻」置顶**（`build_highlight_lines` + `_highlight_line`）：`_collect_scored_items` 收全 combo 的 movement + 三类新品（下载榜只算 is_slg=True，与推送门控一致；回归项已滤）→ 取重要度 Top `DIGEST_HIGHLIGHTS_TOPN`（默认 5）→ 内联市场标签的紧凑行，放 TL;DR 之后、combo 段之前。**仅当全卡事件数 > TOPN 才渲染**（小卡本身已短、置顶会与正文重复）。覆盖面**仅 ranking 派生的 per-combo 竞品事件**——版本/新区/视频/待建档是各自独立的全局段（本就不受地理顺序挤压），不纳入要闻。
- **对标加权（PR #148）**：`_collect_scored_items` 对命中 `own_matches`（对标我方）的竞品 ×`_OWN_MATCH_BOOST`(2.5) 上浮——竞品打进我方赛道是领导第一决策轴，#139 此前只加 ⚔️ 标签不参与排序（榜尾对标竞品会被次市场高名次长尾挤出今日要闻）。仅影响今日要闻排序，⚔️ 标签照常。
- 入参 `per_combo` 不被 mutate（排序走副本，`test_digest_does_not_mutate_input_order`）；常态下排序与现地理顺序几乎一致（US→JP→KR、iOS→安卓），只有次市场冒大事件才上浮，低惊扰。

### 领导群 / 维护者群双卡分发 + markdown 转义（PR #138）

**命题**：digest 当前发「测试群」（仅维护者本人）；推到**有领导的群**前，得先把「受众 + 可信度」做扎实——否则待建档/微信重登等维护者杂讯会直接进领导群，且 ST 原始游戏名某天带 `[Beta]`/`*` 就破版。这是 44-agent 审查（见 git 历史的 `digest-leader-push-audit` workflow）判定的 P0 最小集。

- **双 target 路由**（`services/dingtalk.py`）：`maintainer`（默认，= 测试群/运维群）+ `leader`（领导群）。`_target_fields(target)` 选 url/secret/label；leader 未独立配（`DINGTALK_WEBHOOK_URL_LEADER` 空）时**回退 maintainer**（任意调用方不报错），但 `leader_target_configured()` 严格判（只看 leader url 是否配），digest 双发据此决定是否真发领导卡——**未配就不发，不把领导版重发进维护者群**。`is_enabled` / `_signed_url` / `_post_payload` / `send_markdown` / `send_action_card` 全加 `target` 透传，默认 `maintainer` 向后兼容。
- **受众剥离双渲染**（`build_daily_digest(audience=)`）：同一份检测数据渲染两遍（**零额外 ST/查询**，`send_daily_digest` 内 `_render('maintainer')` + `_render('leader')`）。leader 卡剥离维护者杂讯：跳过「待建档新厂线索」整段、新品行不拼「建议建档」尾标（`build_newcomer_lines(lead_cta=False)`）、TL;DR 不计「待建档 N」。领导卡只剩竞品/市场情报（异动 + 新品 + 下载榜 SLG + 版本 + 新区 + 视频 + 今日要闻）。
- **领导卡只看 SLG 产品（PR #143）**：领导反馈非 SLG 新品太多。`is_slg` 是**厂商维度**（发行商在不在 SLG 白名单）≠ 产品品类。leader 卡在 `build_daily_digest` **入口一处**过滤 `per_combo`，剥离 market 层 `is_slg=false`「待识别新厂」（含足球/恐怖/塔防等非 SLG + 白名单未收录的真新厂）→ 正文 / TL;DR 计数 / 今日要闻 / 按钮**所有出口统一不含**（一处过滤避免逐出口判漏；不 mutate 入参、浅拷副本）。已识别 SLG 厂的 publisher 新品 + free（已 is_slg 门控）保留；维护者卡（全量 + 建档线索）不过滤。**已知残留**：SLG 厂出的塔防 TD / 消除（is_slg=true 厂商维度去不掉）——口径 B（LLM 中文摘要 `summary_cn` 品类词门控）按需再加，2026-06-28 决议**先口径 A 观察**。
- **维护者杂讯钉死 maintainer**：微信重登提醒（含 ssh 重扫码指令）+ 商店雷达 send 显式 `target="maintainer"`——运维/自检类永不进领导群。
- **主卡失败升 Sentry**（`critical=True`）：digest 主卡是「每日必达」，终态失败（errcode 拒绝 / 网络异常）打 `logger.error` → Sentry，让维护者立刻补；旁路告警（微信/雷达）维持 `warning` 不刷屏。把「静默丢卡 = 信任无声流失」变成「被叫醒补」。
- **markdown 转义**（`_md_name(s, maxlen=32)`）：折叠空白 + 超长截断 + 方括号→圆括号（防 `名](url` 误成链接）+ 转义 `* _ \\` `` ` `` `~`（防加粗错位/代码块），套到所有 markdown **正文文本**名字插值位（movement / 三类新品 / 待建档 / 版本 / 视频 / 新区 / 今日要闻 / 商店雷达 / `_meta_inner` 厂商）。ActionCard 按钮 title 是纯文本不过它；`[锚文本](url)` 文章标题另有 sanitize（只括号替换）。
- **上线开关**：HK `backend/.env` 配 `DINGTALK_WEBHOOK_URL_LEADER`（+ `DINGTALK_SECRET_LEADER` / `DINGTALK_WEBHOOK_LABEL_LEADER`，**敏感不进 git**）→ `compose --env-file .env up -d backend` 重读即生效；不配 = 维持单卡单群（向后兼容）。

### digest 同赛道：竞品和我方哪款同赛道（PR #139 起，玩法子品类精确匹配）

**缺口**：digest 此前只有竞品 `name/rank/revenue`，不告诉领导「这竞品和我方**哪款同赛道** / 威胁我方什么」——领导扫一条异动得自己脑补「跟我们有关系吗、要不要管」。**纯本地零 ST**（分类复用新品中文化的 LLM 调用，零额外 LLM）补上这个决策锚点。

- **措辞「同赛道」而非「对标」**：X 是我方产品，「竞品**对标**我方」方向说反且自抬（对标=后来者拿强者当标杆）；「《我方产品》**同赛道**」把我方当参照系、陈述竞争关系。渲染 `⚔️《X》同赛道`、TL;DR `⚔️ 同赛道 N`。
- **匹配维度从题材关键词 → 玩法子品类（核心精度修复）**：原 `own_products.match_keywords`（题材词如「丧尸/末日/survival」）**先天太宽泛**——「末日」横跨数字门/基地建设/塔防/城建/益智多品类，分不出「数字门玩法 SLG」（无尽火线真赛道）vs「基地建设 SLG」（State of Survival / Last Shelter）。prod 实测：题材匹配命中的全是同题材**不同赛道**的游戏（城建模拟 Frozen City、益智 Boom Blast、塔防…），无一真同赛道。**根因**：匹配的文本（名+摘要）没有玩法机制维度。
- **修法（alembic `0036`，纯加列）**：① `market_newcomer_log.subgenre_cn`——新品中文化（`newcomer_i18n`）时 LLM **同一次调用**多分类一个受控玩法子品类（`SUBGENRE_VOCAB`：数字门SLG/基地建设SLG/国战SLG/塔防/三消合成/城建模拟/放置养成/卡牌RPG/休闲益智/其他，**按核心机制非题材**判；非词表值丢弃为 NULL 不脏库）。② `own_products.match_subgenre`——我方产品的目标子品类（如无尽火线=数字门SLG）。
- **匹配**（`_match_own_product` / `_load_own_products`）：**子品类优先权威**——产品配了 `match_subgenre` 就**只**按 `竞品 subgenre_cn == 产品 match_subgenre` 精确匹配（忽略关键词）；竞品未分类（NULL）/异子品类 → 不命中（宁缺毋滥，正是要去掉的假阳）。未配子品类的产品才回退题材关键词子串匹配（新品用「名+摘要」、movement 老竞品用名）。`send_daily_digest` 先一次查全部候选竞品（新品+movement）的 `subgenre_cn`，再建 `own_matches`，整段 try/except 兜底。
- **前进式（不回填）**：`subgenre_cn` 与 summary 同一次产出，故新品天然带；老行不回填——切到子品类匹配后老假阳行（`subgenre_cn=NULL ≠ 数字门SLG`）**立即不再误标**，无需回填（数据证近期 feed 无数字门新品，回填也捞不到正例），且避开「非词表值→NULL→每天重试烧配额」。真数字门竞品 going-forward 自动分类命中。
- **录入**：「我方产品」页（`ProductsManage.tsx`）编辑面板加**玩法子品类下拉**（受控词表，首选）+ 题材关键词输入框（回退）+ 卡片 chip。子品类的词表前后端两处（`newcomer_i18n.SUBGENRE_VOCAB` + `ProductsManage.SUBGENRE_OPTIONS`）须同步。
- **加权**：`_collect_scored_items` 对命中竞品 ×`_OWN_MATCH_BOOST`(2.5) 上浮今日要闻排序（PR #148）。**领导卡 + 维护者卡都显示**（纯决策信号，不剥离）。
- **局限**：子品类靠 LLM 分类（已用最糟样本验证 Last Shelter→基地建设SLG / Frozen City→城建模拟 / 合成数字门样本→数字门SLG 三者分得开）；movement 老竞品若从未作为新品建档则无 `subgenre_cn`、子品类产品对其不命中（属预期——established 竞品领导本就熟）。

### 新厂商线索 CTA（PR #104）

digest 里 `is_slg=false` 的市场新面孔，经忽略名单过滤后多是**真·未识别厂商线索**而非噪声。`build_newcomer_lines` 给这类行升级文案（带「建议建档」行动指引）并**行内附商店页直达**（`_store_url` 拼不出则只留文案）——底部 ActionCard 按钮全局封顶 5、每 combo 仅 1 条，线索未必挤得进，行内链接保证每条都有「立即去看」入口。已归属主体的厂商新品行不打 CTA。**次市场同步日洪峰治理（#141）**：RU/DE 周级批量同步日一次涌进几十个待识别新面孔（混足球/塔防/恐怖等非 SLG），而 genre 仅本地化大类（`Игры`/`Spiele`，无 Strategy 细分）→ **无法按 genre 精准门控**，故退「限量 `DIGEST_MARKET_LEAD_TOPN`(3) + 折叠剩余」（折叠行带看板核查深链、线索不静默丢）；已识别 SLG（is_slg=true）不受限、多被 publisher 层覆盖。

### 待建档新厂线索段（下载榜，PR #131）

CTA 是**收入榜** `is_slg=false` 线索；**下载榜**（free）另有一套。下载榜新品段（`build_free_newcomer_lines`）只推 `is_slg=True`（装机榜休闲/工具噪声大），代价是漏掉**白名单未收录的真 SLG 新厂**（触发案例：Last Shelter: War Z / IM30 海外新壳 LAST ORIGIN STUDIO，因发行商未建档被门控吞掉）。`send_daily_digest` 补一段聚合：free 榜 `is_slg=false` + `genre` 含 Strategy（忽略名单已滤，再用 genre 压休闲噪声）→ digest 单列「🔍 待建档新厂线索」段（`build_lead_newcomer_lines`）给维护者人工核查建档 → 该厂后续新品自动进正式 SLG 段，形成「提醒→建档→不再漏」闭环。领导端「下载榜新品 · SLG」段仍只推已确认 SLG，互不混淆。**可读性（PR #148，接 #147）**：该段 `genre` 走 `_genre_cn` 转中文、有 `summary_cn` 则补 📝 一句话（lead app ⊆ newcomer_apps，复用 `summaries_by_app` 零额外查询；译文未就位优雅降级不显 📝）——#147 把待识别在新品页默认收起后，这段钉钉日推成了维护者**唯一的建档触点**，最该一眼看懂。**初筛仅 `genre=Strategy`（会漏 genre 缺失/非英文标）是有意从简——候选量极小（实测 ~1/天），观察后再定是否上 LLM 语义门控（成本实测 ~$0.001/天可忽略，但候选太少暂不值当）。**

### 微信文章 ↔ 新品名匹配（PR #105）

digest 给新品行附行业公众号文章（`_match_articles_to_apps`）。匹配走 `_name_matches` 而非裸 substring：**拉丁名**词边界 + 大小写无关（"Last War" 不再误挂 "Last Warning"）；**非拉丁名**按非空白字符数设最小长度 `WECHAT_MATCH_MIN_NAME_LEN`（默认 2，砍单字"城"噪声、保留"原神"）。**刻意不引停用词表**——多字通用名（韩文"탑 로드"）无分词仍可能误挂，观察实际误挂率再定。

### 跨市场去重（前端展示，PR #103/#106）

同款全球游戏（同 app_id：iOS=数字 trackId、Android=GP 包名，跨国一致且两端永不撞键）在多 combo 各检出一次，`/history`（全市场视图）与 `/publishers`（厂商新品视图）都逐市场返回多条。前端 `lib/newcomerGrouping.ts`（`groupByApp` / `groupPublisherByApp`）按 app_id 合并成一张卡 / 一行 + 多市场徽标：**最佳（最小）名次**为 headline、**最早检出**为日期、抽屉 / tooltip 列各市场名次。纯前端分组，API 未动，CSV 仍导逐市场全量（不丢粒度）。与既有**跨平台** sibling 去重（`sibling_match.py`，iOS×Android 同游戏）是**不同轴**。

### 检出日志保留（PR #103）

`market_newcomer_log` 检出即落库、只增不减（读路径只按 `days` 筛）。`prune_newcomer_log` 每日 03:45 UTC（回填后、备份前）删 `first_detected_at` 早于 `NEWCOMER_LOG_RETENTION_DAYS`（默认 365，≤0 关闭）的行，避免表无限膨胀。

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

**新品页 SLG 状态筛选 + 卡片中文摘要（PR #147，`NewReleases.tsx`）**：market 视图加「SLG 状态」筛选 chip——**已识别 SLG / 待识别新厂 / 全部**（带实时计数），**默认只看已识别 SLG**，把 `is_slg=false` 待识别新厂折进独立桶（治次市场非 SLG 噪声默认刷屏催建档）。纯前端按 `gr.rep.is_slg` 过滤分组（`/history` item 已带 is_slg，无需后端改）。卡片直接显 📝 `summary_cn`（原仅抽屉）。筛选 hint 点明 **`is_slg` 是厂商维度、非产品品类**（已识别 SLG 里仍可能混该厂的塔防/消除）。**权衡**：待识别默认收起 → 维护者靠 digest「待建档线索段」（见 § digest）做建档触点，不靠主动点 tab。「按产品品类彻底分真 SLG」（口径 B，LLM 品类门控）决议先不做、观察。

### 竞品新品实机玩法视频自动搜集（ADR 0002，alembic 0029+0032）

新品检出后定时搜 YouTube 实机玩法视频候选落库，前端新品抽屉展示 + 人工去噪。独立 daily job（**02:45 UTC**，排在核心同步 02:30~02:38 之后、digest 03:00 之前，让当天新品视频赶上当天卡）`services/newcomer_video.py::sync_newcomer_videos` 调 **YouTube Data API**（独立配额、零 ST，`YOUTUBE_API_KEY` 在 `backend/.env`）。query **游戏名加引号精确匹配**防通用/短名拆词噪声（prod 实测 `탑 로드` 裸搜全是 Million Lords/赛马娘）。两表 `newcomer_video`（候选）+ `newcomer_video_search`（搜索台账=去重锚点 + 当日上限 80）。**人工去噪走软删**（`newcomer_video.hidden_at`，alembic 0032）：删的不物删而置 `hidden_at`，列表默认 `hidden_at IS NULL`（前端 UX 不变），**保留噪声样本供回溯统计召回率 + 设计停用词**；`GET /newcomers/videos?include_hidden=true` 可取回。digest【新品实机视频】段只取非隐藏候选（数据源固有同名噪声靠前端「删」收）。详见 [ADR 0002](adr/0002-newcomer-gameplay-video-autosearch.md)。

### tracked iOS 竞品版本变更追踪（ADR 0003，alembic 0030+0031）

每日 digest 流程开头内联 `services/version_tracker.py::check_tracked_versions` 重查 tracked iOS games 的 iTunes 版本（零 ST、批量 lookup），变了写 `game_histories(event_type='version')` + 进 digest「版本更新」全局段 + 详情页时间线（前端 `EVENT_TYPE_CONFIG` 已渲染）。首次填基线不算变更（防刷屏）。**iOS-only**（GP 页无版本源）。HK tracked games 多用 **GP 包名**作 app_id、iTunes 查不到 iOS，靠 `Game.ios_track_id`（人工核对的精确 trackId）补；没补的跳过（弃 iTunes search 兜底——同名歧义大）。详见 [ADR 0003](adr/0003-ios-version-tracking.md)。

### tracked iOS 竞品分地区上线时间对照（ADR 0004，alembic 0033）

`game_region_release(app_id, country, release_date)` 存 tracked iOS 竞品在各 storefront 的 iTunes `releaseDate`（随 country 分地区不同，零 ST）。`services/region_launch.py::sync_region_launches` 周级 job（周一 04:20 UTC）逐 country 批量 lookup → **原子 upsert**（SQLite `ON CONFLICT`，抗周级 job 与手动 `POST /games/regions/sync` 并发撞唯一约束）。`GET /games/{app_id}/regions` 按上架日升序（最早区先=soft-launch 区序，NULL 沉底）+ GameDetail「分地区上线」区块（最早区高亮）。**新进某区事件**：`detect_new_region_launches`（digest 内联、webhook 闸门**前**，无 webhook 也积累历史）检测近 `REGION_LAUNCH_RECENT_DAYS`(30) 天新上架的区 → 写 `game_histories(event_type='region_launch')`、按 (app_id,country) 去重 → digest【竞品新区上线】段 + 详情页时间线。**iOS-only**（GP 无上架日源）；`resultCount=0` 记 NULL（区分不了「未上架」vs「该区另一 trackId」，诚实留空）。详见 [ADR 0004](adr/0004-ios-region-launch-tracking.md)。

### 新品详情中文化（LLM 网关，alembic 0034）

商店描述是源区语言（日/韩/英/德/俄），团队读费劲。`services/newcomer_i18n.py::translate_pending_newcomers`（digest 内联、webhook 闸门**前**，前端抽屉也要中文）给新品走太石 LLM 网关（`TAISHI_TEXT_MODEL`=便宜文本模型）**按 app 翻一次**（跨 combo 去重、回写该 app 全部行）→ `market_newcomer_log.summary_cn`（一句话「这是什么游戏」→ digest 新品行 📝 + 抽屉副标题）+ `description_cn`（描述全文中译 → 抽屉默认显示、可切原文）。每日封顶 `NEWCOMER_TRANSLATE_DAILY_CAP`(30)、便宜模型 cost <$0.15/天（不并入 `LLM_DAILY_BUDGET`，量小有意豁免）。`_parse` 用 `raw_decode` 抗尾部脚注 + 截断时抢救 summary（防长描述译文截断→JSON 失败→`summary_cn IS NULL` 永久重译空翻）。`USE_MOCK_DATA` / 无 `TAISHI_API_KEY` → no-op。

**覆盖范围（PR #147）**：原只翻 `is_slg=True`；现**去掉 is_slg gate，扩到「待识别新厂」(`is_slg=false`)**——新品页核查建档 / digest 待建档段都要看懂。控成本三道：① `is_slg DESC` 优先排序（已识别 SLG 先占 cap，digest/领导卡不退化）② 跳过忽略名单（人工确认非 SLG 不翻）③ cap 不变（待识别多的日子翻不完、下次接着，译文未就位处优雅降级不显 📝）。

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
