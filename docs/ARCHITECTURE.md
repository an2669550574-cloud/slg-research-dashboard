# 架构说明（slg-research-dashboard）

> 「为什么是这样设计的」的权威说明。改动相关代码前必读，避免「修回去」踩坑。
> Runbook（怎么操作）在同目录其它 .md；产品业务知识在 [`PUBLISHERS.md`](PUBLISHERS.md)。

---

## Sensor Tower 配额体系

ST API 调用受**两层约束**：公司池 3000 次/月（多团队共享，真硬上限）+ 本项目 500 次/月（本地软护栏 `SENSOR_TOWER_MONTHLY_LIMIT`，防本项目 bug 烧穿公司池）。两者都要看，**做账以 3000 池为基准**。

### 当前稳态：~19 次/月（占公司池 0.6%）

历史用量 ~360/月，经 PR #7 / #8 / #9 三轮压缩到 ~19/月。**同步节奏是刻意调过的，别擅自加密**。

| 项 | 配置 | 值 |
|---|---|---|
| 全集 combo | `SYNC_RANKING_COMBOS` | US/JP/KR × ios/android（6 组） |
| 主市场 | `SYNC_RANKING_COMBOS_PRIMARY` | `US:ios,US:android` |
| US 榜周期 | `SYNC_PRIMARY_INTERVAL_DAYS` | 7（周级） |
| JP/KR 榜周期 | `SYNC_SECONDARY_INTERVAL_DAYS` | 30（月级） |
| 销量 | `SALES_FETCH_INTERVAL_DAYS` | 14（双周，且仅主市场） |
| 公司池水位 poll | `SENSOR_TOWER_ACCOUNT_USAGE_TTL_HOURS` | 1（PR #41 后；不计配额所以可频繁） |
| 本地硬上限 | `SENSOR_TOWER_MONTHLY_LIMIT` | 50（低于 backfill floor 150 → 回填自动停） |

配置都在 `backend/app/config.py` 默认值（无 env 覆盖）。回滚锚点：#8=`rollback-20260601-0255`、#9=`rollback-20260601-1143`。

用量构成：US 2 组×周 (~8.7) + JP/KR 4 组×月 (~4) + US 销量双周 (~4.3) + 水位 poll (~2) ≈ **19/月**。

### 关键架构决策

**每次 sync 打几次 ST**：拉榜 1 次（`get_all_rankings_today` → `_cached_get`）+ 销量 1 次（`get_sales_batch`，Top20 批量一次拿全，仅销量到点日且主市场才打）。Android 补名字/图标走 Google Play 商品页抓取，**不吃 ST 配额**（只有 IP 封禁风险）。

**cadence 门控用纯函数 `date.toordinal() % interval_days == 0`**，无持久化游标，跨重启/多副本一致。代码在 `scheduler._due_by_interval` / `_combo_due_today` / `_sales_due_today`。

**销量仅主市场**：`_scheduled_sync` 里 `with_sales = is_primary and _sales_due_today(today)`，次市场恒 `False`。JP/KR 销量改走详情页**按需** ST（库未覆盖才打 1 次）。

**rank 长期趋势读本地 `game_rankings` 表（零配额）**——所以次市场月级也不影响趋势图，只是榜单"截至日"会旧。详情/对比页排名趋势**故意**走本地表，别"修"回 live ST。

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
| `NEWCOMER_HISTORY_TOPN` | 100 | 检出沉淀口径（`market_newcomer_log`），比日报宽，页面可筛 Top50/100 |

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

### 新厂商线索 CTA（PR #104）

digest 里 `is_slg=false` 的市场新面孔，经忽略名单过滤后多是**真·未识别厂商线索**而非噪声。`build_newcomer_lines` 给这类行升级文案「⚠️ 新厂商待识别 · 建议建档」并**行内附商店页直达**（`_store_url` 拼不出则只留文案）——底部 ActionCard 按钮全局封顶 5、每 combo 仅 1 条，线索未必挤得进，行内链接保证每条都有「立即去看」入口。已归属主体的厂商新品行不打 CTA。

### 微信文章 ↔ 新品名匹配（PR #105）

digest 给新品行附行业公众号文章（`_match_articles_to_apps`）。匹配走 `_name_matches` 而非裸 substring：**拉丁名**词边界 + 大小写无关（"Last War" 不再误挂 "Last Warning"）；**非拉丁名**按非空白字符数设最小长度 `WECHAT_MATCH_MIN_NAME_LEN`（默认 2，砍单字"城"噪声、保留"原神"）。**刻意不引停用词表**——多字通用名（韩文"탑 로드"）无分词仍可能误挂，观察实际误挂率再定。

### 数据新鲜度

`/history` 返回 `as_of_by_combo`（各 combo 最近快照日，来自 `game_rankings.MAX(date)`）；前端给 ≥3 天滞后的 combo 渲染 stale 提示条，≥14 天转红。让用户看清「JP weekly 数据截至 N 天前」而非误以为是今日榜。

### 跨市场去重（前端展示，PR #103/#106）

同款全球游戏（同 app_id：iOS=数字 trackId、Android=GP 包名，跨国一致且两端永不撞键）在多 combo 各检出一次，`/history`（全市场视图）与 `/publishers`（厂商新品视图）都逐市场返回多条。前端 `lib/newcomerGrouping.ts`（`groupByApp` / `groupPublisherByApp`）按 app_id 合并成一张卡 / 一行 + 多市场徽标：**最佳（最小）名次**为 headline、**最早检出**为日期、抽屉 / tooltip 列各市场名次。纯前端分组，API 未动，CSV 仍导逐市场全量（不丢粒度）。与既有**跨平台** sibling 去重（`sibling_match.py`，iOS×Android 同游戏）是**不同轴**。

### 检出日志保留（PR #103）

`market_newcomer_log` 检出即落库、只增不减（读路径只按 `days` 筛）。`prune_newcomer_log` 每日 03:45 UTC（回填后、备份前）删 `first_detected_at` 早于 `NEWCOMER_LOG_RETENTION_DAYS`（默认 365，≤0 关闭）的行，避免表无限膨胀。

### 性能：跨 combo 查询预加载（PR #106）

digest 与 `/api/newcomers/` 在 combo 循环**外**各预加载 `publisher_ignores`（ignore_keys）与主体匹配器（matchers）一次，传入 `detect_newcomers` / `detect_publisher_newcomers`（参数早已预留），避免每 combo 重查（原 ~10 combo × 4 次冗余小查询）；digest 的中文归属 pass 复用同一份 matchers。行为不变，纯减查询。

### 应用商店雷达（互补层）

`/newcomers/appstore`（`itunes_releases.py` + `gp_releases.py`）：扫已建档主体的开发者账号清单 diff，捞**未上榜的软启动新品**——榜单检测永远看不到的早期信号。免费 iTunes lookup / GP 页 JSON-LD，零 ST 配额。详见 [`PUBLISHERS.md`](PUBLISHERS.md) 辅助端点表。

---

## 相关文档

- [`PUBLISHERS.md`](PUBLISHERS.md) — 厂商主体方法论 + 资本系速览（业务知识）
- [`DEPLOY.md`](DEPLOY.md) / [`ROLLBACK.md`](ROLLBACK.md) / [`BACKUP.md`](BACKUP.md) / [`MIGRATION.md`](MIGRATION.md) — 运维 runbook
- [`ANALYSIS.md`](ANALYSIS.md) — 素材 AI 分析流程
