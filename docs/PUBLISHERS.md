# 厂商主体（Publishers / 资本系）调研与建档

把 SLG 发行商归并成「主体 → 海外发行壳 → 资本集团」的调研系统。本文是**方法论 + 当前资本系速览**；数据模型与判定逻辑的权威定义在代码里。

## 系统在哪

| 关注点 | 位置 |
|---|---|
| 数据模型（7 表：entities / aliases / app_ids / sources / itunes_artists / itunes_apps / relations） | `backend/app/models/publisher.py`（docstring 是权威说明） |
| is_slg 判定 + 内存索引 + 起步种子 | `backend/app/services/slg_publishers.py` |
| 溯源分级（一手/二手）+ tier 派生 | `backend/app/services/provenance.py` |
| 跨平台同款合并（iOS+Android 去重）规则 | `backend/app/services/sibling_match.py`；publishers router 通过 `_dedup_siblings` 接入 |
| 免费 iTunes 雷达同步 + **按 app_id 反解 iOS 开发者账号**（`resolve_artist_for_app`，**多 storefront 兜底** us→jp/kr/cn/tw/hk，治日韩限定 SLG 美区失明，#165） | `backend/app/services/itunes_releases.py` |
| **按安卓包名反解 GP 开发者账号**（`resolve_gp_developer_for_package`，抓详情页解析 `dev?id=`，#166；与 iOS 反解同形供雷达建议统一处理） | `backend/app/services/gp_releases.py` |
| 雷达覆盖建议候选（iOS 数字 app_id / 安卓包名，各 = pinned ∪ alias 匹配产品） | `backend/app/routers/publishers.py` `_ios_suggest_candidates` / `_gp_suggest_candidates` |
| API（CRUD + 子资源 + 聚合） | `backend/app/routers/publishers.py`（前缀 `/api/publishers`） |
| 前端集团/列表/图谱/资本树 | `frontend/src/pages/PublishersManage.tsx` + `frontend/src/lib/equityGraph.ts` |
| brief 戳记折叠（抽屉里把【调研更新 …】戳记折叠到「调研历史 N」） | `frontend/src/lib/briefStamps.ts` + 单测 `briefStamps.test.ts` |

## 辅助端点（零 ST 配额）

| 端点 | 用途 | 是否前端可见 |
|---|---|---|
| `GET /api/publishers/` | 全部主体（含一手源 tier、关系、product_count、top_products、best_rank） | 是（主页） |
| `GET /api/publishers/{id}` | 单主体详情 | 是（抽屉） |
| `GET /api/publishers/{id}/products?days=30` | 旗下产品聚合（跨平台 sibling 去重 + 雷达合并） | 是（抽屉「旗下 SLG 产品」） |
| `GET /api/publishers/health` | 数据健康度自检（tier 分布 + 待补/命名/复核 backlog + 总量 + **雷达覆盖率 iOS+GP 双侧**：`total_itunes_artists`/`entities_without_itunes_artist` + `total_gp_artists`/`entities_without_gp_artist`，#186 补 GP 侧） | 是（顶部 HealthChip「一手 N%」+ tooltip 含「iOS 雷达覆盖 X/Y」+「GP 雷达覆盖 X/Y」） |
| `GET /api/publishers/gaps?days=30&limit=20` | 未归属高收入 publisher（按累计收入降序，已扣除忽略名单）。**带置信信号**：`days_on_chart`（名下最持久 app 上榜天数=桶内 max distinct date，持续=真厂/一日闪现=噪声）+ `genre`/`summary_cn`（gaps→`market_newcomer_log` 数据回流，按代表 app_id join） | 是（顶部「调研缺口」折叠卡，行内显品类/连续上榜天数/📝摘要） |
| `GET /api/publishers/download-leads?days=90&limit=20` | **下载榜早期信号（待建档新厂线索）**：`market_newcomer_log` 里 chart_type=free、is_slg=false、非 reentry、genre 含 strateg 的新品，扣忽略名单、跨市场同 app 收敛+富化回填。**再过一道 LLM 玩法子品类门控**（#253）：`genre` 是 Play 商店的分类字段、开发者可随便挂——实测找茬解谜/塔防肉鸽/竞技卡牌都挂成 Strategy，9 条线索里 6 条是噪声。改用同表已有的 `subgenre_cn`（读商店描述判玩法机制，经 `resolve_subgenres` 三级优先解析）滤掉**明确非 SLG** 的行。**刻意用黑名单 `AUDIT_CLEAR_NON_SLG` 而非白名单 `SLG_CORE_SUBGENRES`**，与探测层（雷达/RSS，#242）相反——那边是推送门控、推错直达领导群故宁可不推；这里是维护者 backlog、漏了就永远错过建档，故只滤有明确反证的：未分类与「三消合成」（P&S 类三消+SLG 混合品的落点）都放行。被滤条目不静默丢弃：`include_non_slg=true` 连同返回并标 `non_slg`，UI 折叠可查、仍带「建主体」按钮供误杀时当场救回。比 grossing 缺口更早（软启动期/装机量先起）；与 digest 方案① 共用同一忽略名单。**额外跑读时归属（`_load_entity_matchers`+`resolve_entity`，与新品监测页同口径）排除已归属已建档主体的 app**——存档 is_slg 是检出时点快照、永不回写，否则先检出后建档的 app 会永远赖在「待建档」里（#168，见下方坑） | 是（顶部「📥 下载榜早期信号」折叠卡，建主体/忽略[app_id 粒度]） |
| `GET /api/publishers/itunes-artist-suggestions?limit=25` | **雷达覆盖建议（iOS + GP 双侧，#166）**：对未接雷达的 is_slg 主体，从其 app（pinned ∪ alias 匹配产品，旗舰优先）免费反解开发者账号给一键接入候选。**iOS** 走 iTunes lookup 反解 artistId（多 storefront 兜底）；**GP** 走详情页解析 `dev?id=` 反解开发者 id（治 GP-only SLG 在面板失明——很多 SLG 只在 Google Play）。两侧各自独立判覆盖（一主体可同时缺两侧 → 两条建议）；账号跨平台全局去重、每主体每平台一条、显式扫描触发。接入复用 `POST /{id}/itunes-artists`（按 `platform` 路由）。响应行带 `platform`('ios'/'gp') | 是（顶部「📡 雷达覆盖建议」折叠卡，行带 iOS/GP 徽标、「接入雷达」；⚠️多品类大厂会出现，需人工跳过） |
| `GET/POST/DELETE /api/publishers/ignores` | 缺口忽略名单（kind=publisher/app_id 两粒度）；POST 对 publisher 归一成 corp_squash 键存储、幂等。**同时被 /gaps、/download-leads、detect_newcomers（digest 方案①）三处共用** | 是（缺口卡「忽略」按钮 + 「已忽略 N」恢复） |
| `POST/PUT/DELETE …` | 主体 + 5 类子资源（aliases/app_ids/itunes-artists/sources/relations）CRUD；写后内存 is_slg 索引自动刷新 | 是（抽屉编辑） |

> **prod 直改配方（不发版、零 ST；prod 现在 Cloudflare 后需绕过 CF/Bot-Fight）**：服务器上
> `D=$(grep ^SLG_DOMAIN .env|cut -d= -f2); K=$(grep ^API_KEY= .env|cut -d= -f2-)` 后：
> 改 is_slg → `curl --resolve $D:443:127.0.0.1 -k -H "X-API-Key:$K" -X PUT https://$D/api/publishers/{id} -d '{"is_slg":false}'`；**（#224 起）改 is_slg=false 即从白名单摘除该主体全部 alias**——`load_index_from_db` 现按 `entity.is_slg` 门控加载 alias（旧行为=加载全部 alias、只能靠删 alias 收噪声，已修）。**app_id 钉选不受此门控**（钉选语义=单品即 SLG，多品类大厂 is_slg=false + pin 单品的范式照常生效）。
> 删误钉 pin → `DELETE https://$D/api/publishers/{entity_id}/app-ids/{row_id}`（行 id 查 `publisher_app_ids`，非 app_id）。
> **⚠️ 降级 is_slg=false 必须同一轮查 pin**（已犯两次）：`SELECT * FROM publisher_app_ids WHERE entity_id={id}`。alias 会被旗标门控自动摘除、**pin 不会**（钉选语义=单品即 SLG，刻意不门控）——降级 entity 后残留的 pin 会让该 app **继续**被判 SLG 推领导群。2026-07-05 降级 CyberJoy 等 9 个时只删了 Larks/Blue Planet 两个 pin，漏掉 CyberJoy→Galaxy Defense（塔防），直到 2026-07-15 领导卡上又见它才发现、补删。**2026-07-16 起有自动化兜底**：周察卡「🧭 白名单卫生自检」段（`services/publisher_audit.py`）每周用 LLM 玩法分类交叉审计 pin/alias，矛盾 ⚠️ 提示人工复核（兜底≤7 天，但降级当轮手查仍是第一防线）。
> 删 alias → `DELETE https://$D/api/publishers/{entity_id}/aliases/{alias_id}`（行 id 查 `publisher_aliases`）——针对「is_slg=true 主体的某条 alias 过宽误命中」（is_slg=false 主体已被上面的旗标门控整体摘除，无需删 alias）。
> `--resolve …127.0.0.1` 直连源站绕 CF；端点自动 `load_index_from_db`。是「多品类大厂/母体错标 is_slg=1 全量放噪声」的订正手段。**订正史**：2026-07-05 降级 Moonton/CyberJoy 等 9 个 + 删 2 误钉 pin（治 digest 外文噪声刷屏）；2026-07-06 删 Tilting Point(22)/Rudel(104) 两个「有 alias 但名下无追踪 SLG、实测 newcomer/movement 全空」的休眠 alias（预防性，回滚=POST 回 keyword）——同轮核查确认 Level Infinite(27)/Scopely(29)=pin-only 不泄漏、Stillfront(24) alias 只命中真 SLG(Supremacy: Call of War)故留。

## 数据存哪、怎么改

- 实体是**运行态 DB 数据，不是代码**。种子 `SEED_PUBLISHERS` 只在空表时灌入；prod 已有数据，改动**走 publishers API 直写 prod，不发版、不进 git、零 ST 配额**。
- 写入配方：
  ```bash
  ssh hk-prod
  docker exec slg_backend python -c '
  import os, urllib.request, json
  H = {"x-api-key": os.environ["API_KEY"], "Content-Type": "application/json"}
  # 调 http://localhost:8000/api/publishers/...
  '
  # ⚠️ 容器内无 curl，用 python urllib；批量直写时统一脚本 → scp → docker cp → exec python
  ```
  写 alias/app_id 后内存索引自动刷新；新建 iTunes artist 后跑 `sync_itunes_releases()` 建基线（免费 lookup，验证 artistId 能解析）。

## 建档 / 溯源方法论

1. **数据驱动找缺口**：扫 `game_rankings` 里有收入、却没被任何 alias/app_id 归属的发行商 = 漏网厂。端点 `GET /api/publishers/gaps?days=30&limit=20`（零 ST 配额、按累计收入降序、按 publisher 名归一合并）。**前端 UI 已抬回**（厂商主体页顶部「调研缺口」折叠卡）——曾因稳态噪声 ≫ 信号（top 20 里 ~17 个是已知非 SLG 巨头：Niantic/Supercell/EA/Chess.com/NetEase 荒野/KRAFTON 等）在 #84 撤掉，现配套「缺口忽略名单」后重新上线：每行可「建主体」（预填 publisher 名为初始 alias）或「忽略」（确认非 SLG 则从缺口剔出）。
   - **忽略名单**（`publisher_ignores` 表 + `/ignores` 端点）：两种粒度——`kind=publisher` 存 `name_match.corp_squash` 归一键（"Niantic, Inc." 与 "Niantic Inc" 折叠成一条）；`kind=app_id` 只剔某一款 app（同发行商其它 app 仍进缺口）。与 is_slg 判定无关，只影响缺口提示。前端「已忽略 N」可折叠恢复。
2. **游戏名指认母体**：旗下产品名最能定公司（三国志战略版→灵犀/阿里；Wolf Game→爱奇艺；Lands of Jail→益世界）。
3. **关系类型按证据强弱**：`wholly_owned`（收购公告/100%）> `controlling`（媒体桥 + 同开发者账号）> `affiliate`（仅聚类/弱）> `minority`（纯参股，**不并组**）。查不到股权登记就别用 wholly_owned。
4. **溯源分级**：registry / official_filing / official_platform / official_domain = 一手；media / reference / analysis / self_report = 二手。归属断言尽量挂一手；查不到就标 unverified，别臆测。**官方主域名最稳一手**（每家公司都有官网、URL 持久、`official_domain` 类型可直升 primary tier）。
5. **资本方 / 集团根**：纯控股母体设 `is_slg=false`（标「资本方」）。集团 = 控制级 + 品牌型关联（`GROUP_EDGE_TYPES` = wholly_owned/controlling/affiliate）连通分量 ≥2；纯参股（minority）不并组（否则腾讯参股 10% 会把元趣系吞进腾讯系）。**报表口径（2026-07-20）**：集团**成员名单**由 `publisher_relations` 推导（`services/publisher_groups.py`，后端/前端同一套 `GROUP_EDGE_TYPES`，改一处要改两处），不落库；`publisher_entities.group_label` 只存**组名**（手工填在组内任一成员上、根优先，如元趣娱乐#35 → 「元趣系」，空则回退根主体名）。透出 `/publishers/` 每条带 `group_id`（=根 id）/`group_name`；消费：厂商主体页集团卡标题 + CSV「资本集团」列 + 月报「🏢 资本集团动态」段。
6. **多品类大厂模式**：旗下既有真 SLG 又有非 SLG（Warner Bros / Bandai Namco / Koei Tecmo / Level Infinite 等），用 `is_slg=False` + 按 `app_id` 精确钉 SLG 单品（绝不能用 alias 否则会把非 SLG 拉进来污染合计榜）。
7. **命名**：中国厂尽量用中文名（「中文 English」式，如「库卡游戏 Qookka」「游族 YOOZOO」）。
8. **negative finding 戳记**：调研验证「无关系/无母体」也是结果。用 `【调研负面发现 YYYY-MM-DD】` 或 `【复查 negative YYYY-MM-DD】` 追加到 brief 锁死研究分支，下次别再回头查（抽屉里会折叠到「调研历史 N」）。

## 当前资本集团速览（2026-06-30；~107 实体 / 32 忽略 / **iOS 雷达 64 账号 + GP 雷达 32 账号**；tier_primary 103/107）

> 雷达 2026-06-30 一轮核查后：iOS 59→64（接 6waves/gumi/星辉Rastar/英雄互娱/Rudel）、GP 28→32（接安卓-only 真 SLG：EasyTech/LIGHTNING/iFun/Immersive）。深圳九九（Falcon Poker，扑克误标）已改 `is_slg=false` + 删错 pin，不在 SLG 口径。
> **2026-07-09 ADR 0006 切片 1 批量扩张：96→134（iOS 67 / GP 67）**——走 `/itunes-artist-suggestions` 一键候选批量接 38 个（含 37GAMES/KingsGroup/Efun 双端/Dragonest/犀牛互动 Rhinos 等），**跳过 3 个坑**：浙江华娱→BILIBILI 账号 ×2（发行账号≠研发主体，接了全是 B 站非 SLG 噪声）、Machine Zone→"Epic War"（账号名对不上存疑）。07-11 验收全部基线化零误报。**仍不接（勿回头）**：Level Infinite/Scopely/Tilting Point（资本系）+ Bilibili/华娱（多品类发行账号）；BUILDING-BLOCKS 唯一真空档（建议端点反解不出，待手工）。

- **途游游戏 Tuyoo** → EVISTA(SLG·新加坡)/Ark Game(HK)/Tuyoo Online HK/Tuyoo Games HK
- **灵犀互娱（阿里）** → 库卡游戏 Qookka ｜ **益世界** → Just Game ｜ **新奇互娱（爱奇艺）** → Special Gamez
- **FunPlus** → KingsGroup + Puzala ｜ **三七互娱** → 37GAMES GLOBAL + BUILDING-BLOCKS
- **Stillfront**（6 子）→ eRepublik / Goodgame / Babil / 6waves / KIXEYE
- **MTG** → InnoGames / Plarium ｜ **Savvy** → Scopely / Moonton ｜ **世纪华通** → 点点 → Century
- **腾讯**（均 minority·不并组）⤳ 元趣娱乐 10.17% / StarUnion 20% / Level Infinite(wholly_owned) ｜ **中文传媒** → 智明星通(ELEX)
- **元趣娱乐 First Fun** → Funfly + Omnilojo（Last Z/Dark War，com.readygo.* 同壳+Parkview Square 同楼）+ 江娱互动
- **九鼎无双 89Trillion** → Fastone Games（Art of War: Legions 出海壳）
- **GDEV (NASDAQ:GDEV)** → GAMEGEARS（AI-powered game studio within GDEV，2026-06-20 溯源建档）
- **多品类大厂（is_slg=False，按 app_id 钉真 SLG 单品）**：
  - **华纳兄弟游戏 Warner Bros. Games** → GoT Conquest / Dragonfire 4 个 app_id
  - **万代南梦宫 Bandai Namco** → キングダム 覇道（与 Koei Tecmo 联名，App Store publisher 字段是 BNE 故归 BNE）
  - **光荣特库摩 KOEI TECMO** → 信長の野望 覇道
- **日系本土策略（is_slg=True，单主体）**：**Asobism**（東京·中野；城とドラゴン 实时对战 RTS，累计 2000万+下载，2026-06-22 缺口溯源建档，一手源 asobism.co.jp）——日系本土长青策略，非出海 4X 建国 SLG，作日系策略参考追踪
- **新出海 SLG（2026 新品监测建档，is_slg=True，单主体）**：**DEQU**（新加坡发行壳 DEQU PTE. LTD.，中文名「王于兴师」疑中国团队出海；Order of Kings / 王の勅命，2026-03 全球上线的 4X×RTS 融合 MMO，一手源 orderofkings.com）——母体/资本系未确认待查。**新品监测捞出未识别真 SLG 的范例**（见下方坑）
  - **浙江华娱网络·东风工作室**（entity #112，2026-07-07 下载榜早期信号建档）：《三国：谋定天下》研发商，韩版 삼국지: 천하결전（Three Kingdoms: Battle Under Heaven），制作人聆风、团队多为重度 SLG 玩家。六职业体系（진군/신행/천공/기좌/청낭/병참）国战 SLG＝攻城+同盟+实时大规模战；2024-06-13 中国上线当日 iOS 畅销前三、B站发行首款 SLG、2024 国产 SLG 最大黑马，韩国 2026-07-02 出海（BILIBILI HK）。**Bilibili 仅独家代理发行**——榜单 publisher=BILIBILI 是发行商非研发商，故 pin 双端 `6757360957`+`com.bhk.newslgkr` **不用 bilibili alias**（多品类巨头避污染）。一手源 newslg-kr.biligames.com / gamemeca gid=1776429 / inven news=312815。**又一个「下载榜早期信号捞出真 SLG 新厂」范例（同 DEQU）。**
  - **Ember Storia / エンバーストーリア**（2026-07-07 同轮，pin 到 **gumi #80**）：SQUARE ENIX 发行、**Orange Cube 研发**（东京独立 SLG 工作室）的 COK-like 基建+大规模盟战+真 PvP 略夺 SLG，日媒定性「村ゲー」。归 gumi 因其前作《Crystal of Reunion》（同 Orange Cube，包名 `jp.co.gu3.orange01` 的 gu3/gumi 血缘）**早已 pin 在 gumi #80**；SQUARE ENIX 是多品类巨头故只 pin 双端 `1634520180`+`com.square_enix.android_googleplay.emberstoria`、不建 alias。
  - **4399（#116，is_slg=0）+ 犀牛互动 Rhinos Games（#117，is_slg=1）+ 关系 #43**（2026-07-09 公众号线索「Hell Asylum」追查建档）：**命名易混淆——Hell Asylum: Last Warden 的 GP 开发者是 Rhinos（rhinosgames.com，犀牛互动，快手关联方为二股东），不是 4399**；4399 是其发行/投资系（rel #43 affiliate），非同一主体。4399 按多品类大厂范式 is_slg=0 + pin 自有 EOC 双端（`1630067618`+`com.eockr.google`，iOS 开发者账号 artistId 967114195 名下仅 EOC 一款）；Rhinos pin `com.us.dyjycbt.google`（Hell Asylum，末日收容所 SLG，2026-07 GP 封测 cbt）+ 已接 GP 雷达账号 `Rhinos`。**暂 pin-only 不建 alias**（封测包名不稳，正式上线换包名再重 pin）。取证：iTunes 6 区搜索 + artist 反查 + GP 原始 HTML 抠 `dev?id=`。**「看榜监测对封测新品全链失明」的实锤案例 → 直接催生 ADR 0006。**
- **独立小厂**：**Rudel** (キングダム 頂天) / LIGHTNING STUDIOS (Game of Kings) / GAMEGEARS / Immersive Games HK / 等
- **单主体（无第二壳）**：**网易**（率土之滨用 app_id 钉，⚠️ 勿加 NetEase Games alias——荒野行動是 BR 误进策略榜，加 alias 会污染合计）；**IGG**；**莉莉丝**（+Farlight）

## 重要经验 / 坑

- **资本数据反映 2025-26 并购，可能比训练知识新**：Plarium→MTG（2025 Aristocrat 转卖）、Moonton→Savvy（字节转卖）——「疑似挂错」**先验证再动**。
- **股东册多在付费墙后**（ACRA BizFile；opengovsg/recordowl 只给 officer 数量）→ 海外壳归属常只能靠 media + 开发者账号佐证，标 `controlling` 不标 `wholly_owned`。
- **安卓包名钉慎用**：若该包在 game_rankings 是未富化行（name/publisher 空），钉它会在产品抽屉顶出一条空名 $0 裸行；优先用 alias，iOS 用数字 id 钉。
- **`app_id pin` = 产品级 SLG 标记，独立于 `entity.is_slg`（反直觉，#165）**：`load_index_from_db` 把**全部** alias/app_id 灌进 is_slg 内存索引，**不按 `entity.is_slg` 门控**——这是**故意的**。多品类巨头（KOEI/华纳/万代，`entity.is_slg=false`）钉特定 app_id 来标「这一款是真 SLG」（光荣三國志），运行时 `is_slg(app_id)` 据此返回 True。**推论**：要让某个误标产品不再算 SLG（如深圳九九的扑克 Falcon Poker 被新品监测误建档+误钉），正解是**删那个错 pin**，不是改 loader（改 loader 会误伤巨头的合法产品级 pin），也不是只改 `entity.is_slg`（光改 entity flag 不动 pin，运行时 is_slg 仍 True）。
- **巨头多主体扫描结论**：策略榜未归属的高收入发行商绝大多数是**非 SLG**（Niantic/Supercell/Chess.com/EA/PUBG/NetEase 荒野/KRAFTON/KONAMI/Cygames/Wizards/Voodoo/Highbrow 等），勿误归。
- **App Store「전략/Strategy」标签是缺口 + 新品噪声主因**：2026-06-22 把最后 9 条缺口全部三角化清零——8 行经验证是误挂 strategy 标签的非 SLG（社交推理 Mafia42 标 strategy+board / 合成·roguelike RPG / 挂机 RPG / 麻将雀魂 / 回合制收集 Summoners War），全部 publisher 粒度忽略；唯一真策略是 Asobism 城とドラゴン（已建档）。**判 genre 别信 App Store 单一 strategy 标签，看 `genres` 全列 + 实际玩法**（iTunes lookup `genres` 字段 + 旗下产品名最准；宝可梦对战/Order of Kings/Infinity Kingdom **全标 genre=Strategy**，genre 字段不可用于区分）。5minlab 是 Krafton 全资子（2022 收购）但旗下是合成/roguelike RPG 非 SLG，**母体大不等于产品是 SLG**。**2026-06-30 又裁两条缺口（同款噪声、publisher 粒度忽略 id=31/32）**：**Snowprint Studios AB**（Warhammer 40K: Tacticus＝回合制小队战棋 hex 格+gacha，非 4X 建国；母体 MTG 已在库，→InnoGames/Plarium）＋ **Darkwinter Software Co., Ltd.**（少女前线2:追放＝回合制战棋 RPG+gacha；散爆 Sunborn 发行子公司，旗下少前/云图计划均无 4X SLG）——均非核心 SLG 且资本系已被覆盖，建档零收益。
- **新品监测复用同一忽略名单过滤噪声（2026-06-22，PR #99 已部署）**：`detect_newcomers`/digest 接 `publisher_ignores`（与 /gaps 同口径）剔除确认非 SLG（宝可梦对战刷屏/扑克/塔防），**但不按 is_slg 白名单过滤**——白名单滞后会误杀真新厂。范例：**DEQU《Order of Kings》就是新品监测以 is_slg=false 浮现、人工溯源确认是真出海 RTS-SLG 后建档的**（详见 [`ARCHITECTURE.md` § 缺口忽略名单过滤](ARCHITECTURE.md)）。给新品打 is_slg=false ≠ 非 SLG，可能是「未识别的真新厂」线索。
- **`market_newcomer_log.is_slg` 列是检出时点快照、永不回写（结构性坑，#168）**：行入库时按当时索引算一次 is_slg 存档，**之后建档/pin app_id/加 alias 都不回写历史行**。所以同一 app 不同行 is_slg 可能不一致（free 榜早检出=false、建档后 grossing 榜检出=true）。**凡是直读这个存档列判「是否已收录」的代码都要警惕陈旧**，正确做法是叠加**读时归属**（`_load_entity_matchers`+`resolve_entity`，app_id pin 或 publisher 命中 alias）活算。已知踩坑：`/download-leads` 端点曾只读存档列，导致已 pin 的 `com.more.lastshelter.gp`（龙创悦动 IM30）在新品监测页显示「已归属」、却仍出现在「待建档新厂线索」——#168 给端点补上读时归属修掉。**digest 的同名「待建档新厂线索」段无此坑**：它走 `detect_newcomers` 实时 `is_slg()` 算、不读存档列。
- **跨平台 sibling 去重**（PR #88 初版，#91/#92 修「同 publisher」判定）：iOS+Android 同款合并成一行 product。**两个去重入口口径不同**：
  - **厂商抽屉/列表** `_dedup_siblings`（`routers/publishers.py`）：调用方已 entity-scoped（alias/app_id 预过滤为单 entity），**不再校验 publisher 字符串等价**，仅名字 prefix 子序列匹配 ≥5 字符即合并（PR #91）。修了同公司不同法人/简写发两平台（"TOP GAMES INC."×"TG Inc."、"IGG SINGAPORE PTE. LTD."×"IGG.COM"）漏合——曾跨 26 主体漏合 46 个 product row。
  - **详情页/coverage/metrics** `find_sibling_app_ids`（`services/sibling_match.py`）：扫全表无 entity scope，用 `publisher_aliases` 把两个 publisher 字符串各自映射到 entity，**同 entity 或 normalize 等价**即视为同 publisher（PR #92）。
  - 共同规则：CJK-only 本地化名（normalize 后为空）不参与合并；名字 prefix ≥5 字符。`Valor Legends: Idle RPG` + `ベイラーレジェンド` 这类「Latin 不在头部」暂不合，已知权衡。
  - **连写/法人后缀归一**（`services/name_match.corp_squash`）：alias↔publisher 的 token 子序列匹配在连写名上错位（`Topgames.Inc`=["topgames","inc"] 配不上 alias `top games`=["top","games"]）。补一条 **squash 等值**回退——双方去掉纯法人后缀（Inc/Ltd/PTE/LLC… 不含 games/group/studio 等描述词）后拼成无分隔串比较，is_slg / list / gaps / products / sibling 五处统一受益。**只等值不子串**：子串会让 `igg` 误命中 `Trigger Games`，破坏 word-boundary。
- **L3 跨 entity 同款维持现状不合**：同款 iOS/Android 被建档到**两个不同 entity**（如 Puzzles & Survival：iOS=BUILDING-BLOCKS / Android=37GAMES，两者都是三七互娱全资子）——**故意不合**。业务上抽屉视角正确（点母体「三七互娱」看集团合计），技术上 relation-based transitive merge 边界难定（minority 算不算 / 多层嵌套 / 跨多集团根）。
- **SQL `MAX(name)` 偏向 CJK**：同 iOS app_id 在多市场返回本地化名时，`MAX` 按 Unicode 排序会偏向 CJK 字符；publishers router 的 `_ranking_pairs` + `list_publisher_products` 已改为 `COALESCE(MAX(CASE WHEN country='US'),MAX)` 优先 US 名解决。

## Backlog（按价值排序）

1. ~~**缺口忽略名单**~~ ✅ **已做**（`publisher_ignores` 表 + `/ignores` 端点 + 前端「忽略」按钮/「已忽略 N」恢复 + 缺口 UI 抬回，alembic 0023）：top 20 里 17 个非 SLG 巨头现可一键剔出，缺口收敛到可操作信号。两种粒度（publisher squash 键 / app_id）。详见上方「数据驱动找缺口」。
2. ~~**publisher 名归一鲁棒化**~~ ✅ **已做**（`services/name_match.corp_squash` squash 等值回退，wired 进 is_slg/list/gaps/products/sibling 五处）：`Topgames.Inc`↔`top games` 已命中，`Trigger Games` 仍不误命中。详见上方 sibling 去重条。
3. **关系挂源 FK**（小价值）：`PublisherRelation` 加 `source_id` 可选 FK 让关系绑证据；需 alembic 迁移。
4. ~~**雷达覆盖建议 / 下载榜早期信号 / 缺口置信信号**~~ ✅ **已做**（#155→#158，2026-06-29 全上线，零 ST/无迁移）：第一性原理审查挖出的 3 个跨模块杠杆点——B 雷达覆盖（`/itunes-artist-suggestions` 反解开发者账号，pinned ∪ alias 匹配产品）/ C 下载榜早期信号（`/download-leads`）/ A 缺口置信（`/gaps` 加 `days_on_chart` + newcomer_log 回流）。详见上方「辅助端点」表。**取舍记录**：grounding 实测 A/C 当前 backlog 近空（grossing 缺口 2~5、下载榜线索 1 且已建档），按产品决定作面向未来基础设施先建好；B 才是真有量（75 未覆盖主体 → 48+ 可建议）。**未上 ML 置信模型**（对个位数缺口=过度工程）。

## 命名 backlog（等找到中文主体名再回填，2026-06-21 状态）

50+ 主体仍英文 name，三类：
- (a) **海外厂没官方中文名**：FunPlus / IGG / Top Games / Scorewarrior / Tilting Point / Machine Zone / Stillfront / InnoGames / JoyCity / Level Infinite / Scopely / Plarium / MTG / Savvy / Kefir / Goodgame / Babil / KIXEYE / 6waves / gumi / OpenMind / Bekko / NDREAM / Million Victories / eRepublik / Rudel 等
- (b) **海外发行壳保留英文区分壳身份**：Funfly / Omnilojo / Farlight / GAME SPARK / VoyagerOne / 37GAMES GLOBAL / BUILDING-BLOCKS / KingsGroup / Puzala / EVISTA / Ark Game / Tuyoo Online HK / Tuyoo Games HK / Special Gamez / Just Game Technology / 9z Games / Fastone Games 等
- (c) **独立小厂母体未公开 / 复查 negative 戳记锁死**：CyberJoy / KOOFEI / Larks / Blue Planet Joy / iFun / LIGHTNING STUDIOS / GAMEGEARS (现挂 GDEV) / Immersive Games HK / STONE3 / 7 Pirates / Life Game / Bekko Games / Heyshell / LEME / GameBear / 爱悠龙 HeroNow / 长沙乐糖网络 / Sea War (江锋聂) 等

已中文化 8 个：智明星通 ELEX / 友塔网络 Yotta Games / 沐瞳科技 Moonton / 成都卓杭 DHGames / 海彼 HABBY / 苏州语崛 Genesis Network / 龙腾简合 OneMT / 亦樹遊戲 GameTree（GameBeans/天地劫 台湾发行壳，2026-06-20 溯源）。

## 命名易混淆点（写错过、要记住）

- **"龙腾简合 Long Tech" ≠ [16] 龙腾简合 OneMT**：业内 "Long Tech Network Limited"（《Last Shelter》《Rise of Castles》出版方）**实属 [11] 龙创悦动 IM30 的海外马甲**，`long tech` alias 现挂在 [11]。改 [16] OneMT 主体时**不要**把 Long Tech 系产品挂过去。早期种子主体「龙腾简合 Long Tech」已废（[10] ID 空），PR#81 初稿也犯过同样错误。
  - **[11] IM30 现有两个海外发行壳**（防「为啥 IM30 挂这些 alias」困惑）：`long tech`（LONG TECH NETWORK，Last Shelter: Survival / Last Empire War Z / Rise of Castles）＋ `last origin studio`（**LAST ORIGIN STUDIO LIMITED**，Last Shelter: War Z / `com.more.lastshelter.gp`，末日丧尸 4X SLG）。后者 2026-06-27 由下载榜新品监测以 is_slg=false 浮现 → 隐私政策托管于官方域名 `im30.net` 实锤归属（同 DEQU 范例）→ alias + app_id 建档进 [11]，关系 `controlling`（同官方域名运营受控，无股权登记故不用 wholly_owned）。IM30 是纯末日 SLG 厂、旗下壳用 **alias 门控**（与多品类大厂的 app_id 钉相反）。
- **HABBY × 腾讯传闻已查证否定**（2026-06-20）：HABBY 公开融资轮次仅大观资本/北极光/真格，**无腾讯任何阶段记录**。brief 已戳记锁死，勿再回头查。
- **NetEase Games 不应给「网易」加 alias**：荒野行動是 BR 误进策略榜，加 alias 会污染合计。网易主体（id=70）现有用 app_id 钉率土系列的设计是对的。
- **腾讯 (id=38) `app_ids=[]` 是正确状态**：腾讯出海 SLG 通过 Level Infinite / Proxima Beta / 子工作室 publisher 发行已正确归属到 [27] Level Infinite 等；publisher 字段含 "Tencent" 的只有 PUBG (BR)，**不要强行钉**。
- **Tap4fun = 成都创人所爱 = 原尼毕鲁**（2017 IPO 被否后改名）。**Kingdom Guard 属 Tap4fun 非 OneMT**；**Days of Empire 属 OneMT 非友塔**；**骆驼游戏→壳木游戏**（已改名）。
- **ELEX→江娱 13.5% 已于 2022-12 管理层回购退出**（早期记录里曾标在册，已失效）。
