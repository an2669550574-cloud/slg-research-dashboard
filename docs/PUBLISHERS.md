# 厂商主体（Publishers / 资本系）调研与建档

把 SLG 发行商归并成「主体 → 海外发行壳 → 资本集团」的调研系统。本文是**方法论 + 当前资本系速览**；数据模型与判定逻辑的权威定义在代码里。

## 系统在哪

| 关注点 | 位置 |
|---|---|
| 数据模型（7 表：entities / aliases / app_ids / sources / itunes_artists / itunes_apps / relations） | `backend/app/models/publisher.py`（docstring 是权威说明） |
| is_slg 判定 + 内存索引 + 起步种子 | `backend/app/services/slg_publishers.py` |
| 溯源分级（一手/二手） | `backend/app/services/provenance.py` |
| 免费 iTunes 雷达同步 | `backend/app/services/itunes_releases.py` |
| API（CRUD + /products） | `backend/app/routers/publishers.py`（前缀 `/api/publishers`） |
| 前端集团/列表/图谱/资本树 | `frontend/src/pages/PublishersManage.tsx` + `frontend/src/lib/equityGraph.ts` |

## 数据存哪、怎么改

- 实体是**运行态 DB 数据，不是代码**。种子 `SEED_PUBLISHERS` 只在空表时灌入；prod 已有数据，改动**走 publishers API 直写 prod，不发版、不进 git、零 ST 配额**。
- 写入配方：
  ```bash
  ssh hk-prod
  cd /opt/slg-research-dashboard
  docker compose -f docker-compose.prod.yml exec -T backend python -   # 调 http://localhost:8000/api/publishers
  # header: x-api-key = 容器内环境变量 $API_KEY（不要回显其值）
  ```
  端点：`POST /`（建主体，可带 aliases/app_ids）· `POST /{id}/{aliases,app-ids,itunes-artists,sources,relations}` · `PUT /{id}` · `DELETE …`。
  写 alias/app_id 后内存索引自动刷新；新建 iTunes artist 后跑 `sync_itunes_releases()` 建基线（免费 lookup，验证 artistId 能解析）。

## 建档 / 溯源方法论

1. **数据驱动找缺口**：扫 `game_rankings` 里有收入、却没被任何 alias/app_id 归属的发行商 = 漏网厂 / 潜在集团成员。
2. **游戏名指认母体**：旗下产品名最能定公司（三国志战略版→灵犀/阿里；Wolf Game→爱奇艺；Lands of Jail→益世界）。
3. **关系类型按证据强弱**：`wholly_owned`（收购公告/100%）> `controlling`（媒体桥 + 同开发者账号）> `affiliate`（仅聚类/弱）> `minority`（纯参股，**不并组**）。查不到股权登记就别用 wholly_owned。
4. **溯源分级**：registry / official_filing / official_platform / official_domain = 一手；media / reference / analysis / self_report = 二手。归属断言尽量挂一手；查不到就标 unverified，别臆测。
5. **资本方 / 集团根**：纯控股母体设 `is_slg=false`（标「资本方」）。集团 = 控制级 + 品牌型关联（`GROUP_EDGE_TYPES` = wholly_owned/controlling/affiliate）连通分量 ≥2。
6. **命名**：中国厂尽量用中文名（「中文 English」式，如「库卡游戏 Qookka」「游族 YOOZOO」）。

## 当前资本集团速览（2026-06-19，prod=#79；97 实体 / 36 关系 / 17 资本方）

- **途游游戏 Tuyoo** → EVISTA(SLG·新加坡)/Ark Game(HK)/Tuyoo Online HK/Tuyoo Games HK
- **灵犀互娱（阿里）** → 库卡游戏 Qookka ｜ **益世界** → Just Game ｜ **新奇互娱（爱奇艺）** → Special Gamez
- **FunPlus** → KingsGroup + Puzala ｜ **三七互娱** → 37GAMES GLOBAL + BUILDING-BLOCKS
- **Stillfront**（6 子）→ eRepublik / Goodgame / Babil / 6waves / KIXEYE
- **MTG** → InnoGames / Plarium ｜ **Savvy** → Scopely / Moonton ｜ **世纪华通** → 点点 → Century
- **腾讯**（均 minority·不并组）⤳ 元趣娱乐 10.17% / StarUnion 20% / Level Infinite(wholly_owned) ｜ **中文传媒** → 智明星通(ELEX)
- **元趣娱乐 First Fun** → Funfly + Omnilojo（Last Z/Dark War，com.readygo.* 同壳+Parkview Square 同楼，2026-06 溯源）+ 江娱互动
- **九鼎无双 89Trillion** → Fastone Games（Art of War: Legions 出海壳，2026-06 溯源）
- **华纳兄弟游戏 Warner Bros. Games**（多品类大厂，is_slg=False，仅 GoT Conquest/Dragonfire 4 个 app_id 钉，2026-06-19 建并合并掉旧 [28]）
- 2026-06-19 Top100 漏网补：**LIGHTNING STUDIOS**（Game of Kings）/ **GAMEGEARS**（Aliens vs Zombies: Invasion）/ **Immersive Games HK**（Last Beacon: Survival）独立小厂
- 单主体（无第二壳）：**网易**（率土之滨用 app_id 钉，策略榜其余多为非 SLG）；**IGG**；**莉莉丝**（+Farlight）

## 重要经验 / 坑

- **资本数据反映 2025-26 并购，可能比你的训练知识新**：Plarium→MTG（2025 Aristocrat 转卖）、Moonton→Savvy（字节转卖）都是最新口径——「疑似挂错」**先验证再动**。
- **股东册多在付费墙后**（ACRA BizFile；opengovsg/recordowl 只给 officer 数量）→ 海外壳归属常只能靠 media + 开发者账号佐证，标 `controlling` 不标 `wholly_owned`。
- **安卓包名钉慎用**：若该包在 game_rankings 是未富化行（name/publisher 空），钉它会在产品抽屉顶出一条空名 $0 裸行；优先用 alias，iOS 用数字 id 钉。
- **巨头多主体扫描结论**：策略榜未归属的高收入发行商绝大多数是**非 SLG**（Niantic/Supercell/Chess.com/EA/PUBG…），勿误归。

## Backlog（可选续做）

- 长尾小壳母体溯源未果（资料稀缺/独立）：GameTree（GameBeans/天地劫）、STONE3、7 Pirates（Ark of War）、CyberJoy、KOOFEI、Larks、Blue Planet Joy、iFun、LIGHTNING STUDIOS、GAMEGEARS、Immersive Games HK。2026-06 复查仍独立：Bekko Games、Heyshell、LEME Games、GameBear、爱悠龙 HeroNow(Skydragon/LOVEGAME)、长沙乐糖网络。
- 命名未中文化（等找到中文主体名/母体再回填）：60+ 主体仍英文 name，三类：(a) **海外厂没官方中文名**——FunPlus / IGG / Top Games / Scorewarrior / Tilting Point / Machine Zone / Stillfront / InnoGames / JoyCity / Level Infinite / Scopely / Plarium / MTG / Savvy / Kefir / Goodgame / Babil / KIXEYE / 6waves / gumi / OpenMind / Bekko / NDREAM / Million Victories / eRepublik 等；(b) **海外发行壳保留英文区分壳身份**——Funfly / Omnilojo / Farlight / GAME SPARK / VoyagerOne / 37GAMES GLOBAL / BUILDING-BLOCKS / KingsGroup / Puzala / EVISTA / Ark Game / Tuyoo Online HK / Tuyoo Games HK / Special Gamez / Just Game Technology / 9z Games / Fastone Games 等；(c) **独立小厂母体未公开**——见上一条 backlog。2026-06-19 已中文化批：智明星通 ELEX / 友塔网络 Yotta Games / 沐瞳科技 Moonton / 成都卓杭 DHGames / 海彼 HABBY / 苏州语崛 Genesis Network / **龙腾简合 OneMT**（brief 明证：福州龙腾简合网络技术有限公司）。
- ⚠️ 同源未合并：**[16] 龙腾简合 OneMT** 与历史种子里 "龙腾简合 Long Tech"（[10] ID 已空）应是同一家公司不同发行品牌（OneMT=中东《苏丹的复仇》/ Long Tech=《Last Shelter》《Rise of Castles》）。当前 Long Tech 品牌产品靠 `long tech` alias 命中（挂在某个仍存在主体上），未独立成主体。要不要把 Long Tech 单独建主体并标 affiliate 关系到 OneMT，留作 backlog。
- iOS/安卓深尾净新小厂：DELUXE/Rudel、SINCETIMES、TeamQuest、AppQuantum、SuperMagic 等（价值递减）。
- 待证线索：Larks(Idol Company) 疑与 Sea War(江锋聂) 同源（未点名报道暗示，2026-06 复查仍无实锤）；**元趣娱乐除江娱互动外还投了龙创悦动**（未建、未挂，记一笔）。
- 缺口：途游 Fire War（官网列、现网查无，未建）；灵犀台湾壳 青鸟 Sialia（未入库）；Machine Zone 可挂 AppLovin（外资财务母体，未做）。
