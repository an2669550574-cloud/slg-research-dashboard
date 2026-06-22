# 厂商主体（Publishers / 资本系）调研与建档

把 SLG 发行商归并成「主体 → 海外发行壳 → 资本集团」的调研系统。本文是**方法论 + 当前资本系速览**；数据模型与判定逻辑的权威定义在代码里。

## 系统在哪

| 关注点 | 位置 |
|---|---|
| 数据模型（7 表：entities / aliases / app_ids / sources / itunes_artists / itunes_apps / relations） | `backend/app/models/publisher.py`（docstring 是权威说明） |
| is_slg 判定 + 内存索引 + 起步种子 | `backend/app/services/slg_publishers.py` |
| 溯源分级（一手/二手）+ tier 派生 | `backend/app/services/provenance.py` |
| 跨平台同款合并（iOS+Android 去重）规则 | `backend/app/services/sibling_match.py`；publishers router 通过 `_dedup_siblings` 接入 |
| 免费 iTunes 雷达同步 | `backend/app/services/itunes_releases.py` |
| API（CRUD + 子资源 + 聚合） | `backend/app/routers/publishers.py`（前缀 `/api/publishers`） |
| 前端集团/列表/图谱/资本树 | `frontend/src/pages/PublishersManage.tsx` + `frontend/src/lib/equityGraph.ts` |
| brief 戳记折叠（抽屉里把【调研更新 …】戳记折叠到「调研历史 N」） | `frontend/src/lib/briefStamps.ts` + 单测 `briefStamps.test.ts` |

## 辅助端点（零 ST 配额）

| 端点 | 用途 | 是否前端可见 |
|---|---|---|
| `GET /api/publishers/` | 全部主体（含一手源 tier、关系、product_count、top_products、best_rank） | 是（主页） |
| `GET /api/publishers/{id}` | 单主体详情 | 是（抽屉） |
| `GET /api/publishers/{id}/products?days=30` | 旗下产品聚合（跨平台 sibling 去重 + 雷达合并） | 是（抽屉「旗下 SLG 产品」） |
| `GET /api/publishers/health` | 数据健康度自检（tier 分布 + 待补/命名/复核 backlog + 总量） | 是（顶部 HealthChip「一手 N%」+ tooltip） |
| `GET /api/publishers/gaps?days=30&limit=20` | 未归属高收入 publisher（按累计收入降序，已扣除忽略名单） | 是（顶部「调研缺口」折叠卡） |
| `GET/POST/DELETE /api/publishers/ignores` | 缺口忽略名单（kind=publisher/app_id 两粒度）；POST 对 publisher 归一成 corp_squash 键存储、幂等 | 是（缺口卡「忽略」按钮 + 「已忽略 N」恢复） |
| `POST/PUT/DELETE …` | 主体 + 5 类子资源（aliases/app_ids/itunes-artists/sources/relations）CRUD；写后内存 is_slg 索引自动刷新 | 是（抽屉编辑） |

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
5. **资本方 / 集团根**：纯控股母体设 `is_slg=false`（标「资本方」）。集团 = 控制级 + 品牌型关联（`GROUP_EDGE_TYPES` = wholly_owned/controlling/affiliate）连通分量 ≥2。
6. **多品类大厂模式**：旗下既有真 SLG 又有非 SLG（Warner Bros / Bandai Namco / Koei Tecmo / Level Infinite 等），用 `is_slg=False` + 按 `app_id` 精确钉 SLG 单品（绝不能用 alias 否则会把非 SLG 拉进来污染合计榜）。
7. **命名**：中国厂尽量用中文名（「中文 English」式，如「库卡游戏 Qookka」「游族 YOOZOO」）。
8. **negative finding 戳记**：调研验证「无关系/无母体」也是结果。用 `【调研负面发现 YYYY-MM-DD】` 或 `【复查 negative YYYY-MM-DD】` 追加到 brief 锁死研究分支，下次别再回头查（抽屉里会折叠到「调研历史 N」）。

## 当前资本集团速览（2026-06-21，prod=#88 e1323f4；101 实体 / 37 关系 / 20 资本方 / ~237 source；tier_primary 100%）

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
- **独立小厂**：**Rudel** (キングダム 頂天) / LIGHTNING STUDIOS (Game of Kings) / GAMEGEARS / Immersive Games HK / 等
- **单主体（无第二壳）**：**网易**（率土之滨用 app_id 钉，⚠️ 勿加 NetEase Games alias——荒野行動是 BR 误进策略榜，加 alias 会污染合计）；**IGG**；**莉莉丝**（+Farlight）

## 重要经验 / 坑

- **资本数据反映 2025-26 并购，可能比训练知识新**：Plarium→MTG（2025 Aristocrat 转卖）、Moonton→Savvy（字节转卖）——「疑似挂错」**先验证再动**。
- **股东册多在付费墙后**（ACRA BizFile；opengovsg/recordowl 只给 officer 数量）→ 海外壳归属常只能靠 media + 开发者账号佐证，标 `controlling` 不标 `wholly_owned`。
- **安卓包名钉慎用**：若该包在 game_rankings 是未富化行（name/publisher 空），钉它会在产品抽屉顶出一条空名 $0 裸行；优先用 alias，iOS 用数字 id 钉。
- **巨头多主体扫描结论**：策略榜未归属的高收入发行商绝大多数是**非 SLG**（Niantic/Supercell/Chess.com/EA/PUBG/NetEase 荒野/KRAFTON/KONAMI/Cygames/Wizards/Voodoo/Highbrow 等），勿误归。
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

## 命名 backlog（等找到中文主体名再回填，2026-06-21 状态）

50+ 主体仍英文 name，三类：
- (a) **海外厂没官方中文名**：FunPlus / IGG / Top Games / Scorewarrior / Tilting Point / Machine Zone / Stillfront / InnoGames / JoyCity / Level Infinite / Scopely / Plarium / MTG / Savvy / Kefir / Goodgame / Babil / KIXEYE / 6waves / gumi / OpenMind / Bekko / NDREAM / Million Victories / eRepublik / Rudel 等
- (b) **海外发行壳保留英文区分壳身份**：Funfly / Omnilojo / Farlight / GAME SPARK / VoyagerOne / 37GAMES GLOBAL / BUILDING-BLOCKS / KingsGroup / Puzala / EVISTA / Ark Game / Tuyoo Online HK / Tuyoo Games HK / Special Gamez / Just Game Technology / 9z Games / Fastone Games 等
- (c) **独立小厂母体未公开 / 复查 negative 戳记锁死**：CyberJoy / KOOFEI / Larks / Blue Planet Joy / iFun / LIGHTNING STUDIOS / GAMEGEARS (现挂 GDEV) / Immersive Games HK / STONE3 / 7 Pirates / Life Game / Bekko Games / Heyshell / LEME / GameBear / 爱悠龙 HeroNow / 长沙乐糖网络 / Sea War (江锋聂) 等

已中文化 8 个：智明星通 ELEX / 友塔网络 Yotta Games / 沐瞳科技 Moonton / 成都卓杭 DHGames / 海彼 HABBY / 苏州语崛 Genesis Network / 龙腾简合 OneMT / 亦樹遊戲 GameTree（GameBeans/天地劫 台湾发行壳，2026-06-20 溯源）。

## 命名易混淆点（写错过、要记住）

- **"龙腾简合 Long Tech" ≠ [16] 龙腾简合 OneMT**：业内 "Long Tech Network Limited"（《Last Shelter》《Rise of Castles》出版方）**实属 [11] 龙创悦动 IM30 的海外马甲**，`long tech` alias 现挂在 [11]。改 [16] OneMT 主体时**不要**把 Long Tech 系产品挂过去。早期种子主体「龙腾简合 Long Tech」已废（[10] ID 空），PR#81 初稿也犯过同样错误。
- **HABBY × 腾讯传闻已查证否定**（2026-06-20）：HABBY 公开融资轮次仅大观资本/北极光/真格，**无腾讯任何阶段记录**。brief 已戳记锁死，勿再回头查。
- **NetEase Games 不应给「网易」加 alias**：荒野行動是 BR 误进策略榜，加 alias 会污染合计。网易主体（id=70）现有用 app_id 钉率土系列的设计是对的。
- **腾讯 (id=38) `app_ids=[]` 是正确状态**：腾讯出海 SLG 通过 Level Infinite / Proxima Beta / 子工作室 publisher 发行已正确归属到 [27] Level Infinite 等；publisher 字段含 "Tencent" 的只有 PUBG (BR)，**不要强行钉**。
- **Tap4fun = 成都创人所爱 = 原尼毕鲁**（2017 IPO 被否后改名）。**Kingdom Guard 属 Tap4fun 非 OneMT**；**Days of Empire 属 OneMT 非友塔**；**骆驼游戏→壳木游戏**（已改名）。
- **ELEX→江娱 13.5% 已于 2022-12 管理层回购退出**（早期记录里曾标在册，已失效）。
