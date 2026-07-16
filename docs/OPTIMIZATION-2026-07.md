# 新品监测 + 厂商主体模块优化计划（2026-07-04 产品审查）

> 一次「产品视角 + 第一性原理」的模块审查产出。**这是路线图 / 判案笔记，不是权威 runbook**——机制怎么工作看 [`ARCHITECTURE.md`](ARCHITECTURE.md) / [`PUBLISHERS.md`](PUBLISHERS.md) / [`adr/`](adr/)。已实现项在「状态跟踪」勾掉，其余是 backlog。
>
> 方法：先摸清两模块从「检出 → 富化 → 分发 → 人工反馈」的完整链路（代码 grounding），再按情报系统的价值轴评估空白，最后按成本/价值排序。

---

## 一、第一性原理：这套系统在生产什么

情报系统的价值 = **覆盖 × 及时 × 信噪比 × 可行动性 × 持续性**，约束 = ST 配额（硬）+ 一个人维护（硬）+ LLM 小成本（软）。对照五轴的诚实评估：

| 轴 | 现状 | 评分 |
|---|---|---|
| 覆盖 | 收入/下载双榜 × 10 combo + 商店雷达（iOS 64 / GP 32 账号）+ 微信文章 + YT 视频，四层来源对称 | ★★★★★ 近饱和 |
| 及时 | US 日更 T+3h 进卡；次市场双周（配额刻意取舍） | ★★★★ 已到配额边界 |
| 信噪比 | is_reentry / 老品双门控 / SLG 门控 / 忽略名单 / 幂等守卫，一年迭代打磨到位 | ★★★★ 剩长尾 |
| 可行动性 | 同赛道 ⚔️ / 建档闭环 / 深链，有决策锚点 | ★★★☆ |
| **持续性** | **新品检出后即「阅后即焚」，无生命周期跟踪** | **★★ 最大结构缺口** |

**核心判断**：检出漏斗（发现新东西）经一年迭代已很强、边际收益递减；结构性空白在漏斗**下游**——事件流没有升维成态势感知。三个断层：

1. **新品断层**：检出 → 当天 digest 一条 → 沉淀 `market_newcomer_log` → **遗忘**。除非它冲进收入榜 Top20（movement 口径），否则第 2~30 天没人知道它起飞还是死了。领导看到「新品 X 上架」的下一个问题「它后来怎么样了」，系统答不上。检出快照（`market_newcomer_log`）和后续每日轨迹（`game_rankings`）两张表就是没连起来，零 ST 就能接。
2. **富化断层**：最早期信号（商店雷达检出的软启动新品，买量调研最佳窗）反而富化最少——雷达检出落 `publisher_itunes_apps`，**不进** `market_newcomer_log` 管道，没有中文摘要 / subgenre / 视频 / 文章联动。信号越早价值越高，现状倒挂。
3. **分类断层**：`subgenre_cn` 玩法子品类只有「新品」才有，tracked 8 竞品和 movement 老竞品全 NULL → 同赛道 ⚔️ 对老竞品结构性失效（`ARCHITECTURE.md` 标「属预期」，实为低成本可解锁）；赛道级视图（数字门 SLG 整体在热还是冷）无从谈起。

厂商主体模块单独看：档案建设（归属/溯源/雷达/缺口）已成熟收敛（缺口近清零、雷达 96 账号），短板是**静态档案库**——107 实体有「它是谁」，没有「它最近在干嘛」。

---

## 二、优化方案（P0 → P2）

### P0-1 新品生命周期追踪 ⭐ 本计划核心（补最大断层）

`market_newcomer_log`（检出快照，[`models/newcomer.py:16`](../backend/app/models/newcomer.py)）× `game_rankings`（检出后每日名次，[`models/game.py:35`](../backend/app/models/game.py)）按 `(country,platform,app_id,chart_type)` join，读时计算走势。纵切片三步：

| 切片 | 交付 | 成本 |
|---|---|---|
| ① 后端走势读时计算 | `/newcomers/history`（[`routers/newcomers.py:273`](../backend/app/routers/newcomers.py)）每行附 `trajectory`：当前名次 / 峰值名次 / 是否掉榜 / 追踪天数 / 趋势枚举。纯 join 本地表，零迁移零 ST | 小 |
| ② 前端走势列 | 新品卡（[`NewReleases.tsx:355`](../frontend/src/pages/NewReleases.tsx)）加「检出 #63 → 现 #38 ↗ / 已掉榜 ✝」标记 + 可按「仍在爬升」筛选 | 小 |
| ③ digest 周报卡「📈 新品周察」 | 周一发回顾卡：近 30 天检出 SLG 新品的存活/爬升/死亡分层，起飞者列名次轨迹。**两卡都发**——正是领导「后来怎么样了」的答案 | 中（周 job；如需去重台账则小迁移） |

价值：把 digest 从「每日事件通知」升维到「态势感知」，全部零 ST。
验证：拿 prod 真实 log 回放，人工核 5 个已知案例（Top General / Order of Kings）轨迹正确性（最糟样本铁律）。

### P0-2 高潜新品「一键晋升 tracked」

tracked games 只有 8 个静态老竞品，版本追踪（ADR 0003）/ 分地区（ADR 0004）/ 详情页趋势只覆盖它们。新品判定值得深跟后，要人工建 `Game` + 手工查 `ios_track_id`，实际从没发生。

方案：新品抽屉加「转入深度追踪」按钮 → 自动建 `games` 行。**关键便利**：iOS 新品 `app_id` 本身就是数字 trackId，`ios_track_id` 全自动填；安卓留白（与既有 iOS-only 口径一致）。晋升后自动获得版本追踪 + 分地区 + 详情页趋势。
成本：小（POST 端点复用 Game CRUD + 前端按钮）。零 ST。
注意：`games` 被多模块消费（素材/对比页），晋升的新品会进全站下拉——确认这是期望（大概率是，竞品就该全站可见）。

### P1-1 商店雷达软启动新品接入富化管道（修倒挂）

雷达非基线新上架（软启动期 SLG）只有一条裸 alert（[`routers/newcomers.py:128` /appstore](../backend/app/routers/newcomers.py)）+ 平淡日折叠行，无 summary_cn / subgenre / 视频。

方案：雷达 ingest 判「真新上架」（is_baseline=false）且主体 is_slg 时，补写一行进 `market_newcomer_log`（source 标 `radar`，rank NULL）→ 汇入现有中文化 / subgenre / 视频 / 文章四条富化流，前端加「雷达检出」徽标。
成本：中。两个口径问题：① 该行无 country/rank（各消费方已容忍 NULL）；② 后续真上榜时 `(country,platform,app_id,chart_type)` 唯一约束——需想清雷达行与榜单行是合并（上榜更新 rank）还是并存，小心不破幂等。
量级：雷达新上架 ~1~2 条/周，LLM/YT 增量可忽略。

### P1-2 subgenre 存量回补（解锁同赛道全覆盖 + 赛道脉搏）

同赛道 ⚔️（`ARCHITECTURE.md` § digest 同赛道）只对「曾作为新品被分类过」的竞品生效；movement 老熟人（领导最常看到的行）全 NULL 不命中。⚔️ 的价值不在认识它，而在**每次异动时提示「这是打我们赛道的」**。

方案：一次性脚本给三类存量 app 补 subgenre（走 `newcomer_i18n` 同一 LLM 调用与受控词表）：tracked 8 + 近 90 天 movement 常客（几十个）+ 雷达非基线 app。落 `market_newcomer_log.subgenre_cn` 不合适（它们不是 newcomer）→ 需新家：给 `games` 加列（只覆盖 tracked）或建轻量 `app_subgenre` 表（覆盖任意 app_id，**推荐**，纯加表迁移）。
顺带解锁「赛道脉搏」——月度新品按 subgenre 分布，可并入 P0-1 周报。
成本：中（迁移 + 脚本 + `_match_own_product` 读路径加 fallback 源）。LLM 一次性 ~100 条、每天增量个位数。
验证：最糟样本——已知答案老竞品人工核 10 个再全量。

### P1-3 digest 既有 backlog 收口（引用，非新发现）

跨 combo 新品去重 + 四全局段统一封顶（路线图 P1.2/P1.3，webhook 已配、已解冻）。**前置：先攒几张真实领导卡长度样本再调参**（最糟样本铁律）。领导卡刚发几天，建议再观察一周。收入连涨姊妹项（checkpoint backlog）同理待需求触发。

### P2（锦上添花 / 待需求确认）

| 项 | 内容 | 判断 |
|---|---|---|
| 厂商动态区块 | 厂商抽屉加「近 90 天动态」：新品数 / 雷达新上架 / 旗下最好名次变化，全本地聚合 | 价值中；先看 P0-1 周报是否已够 |
| /health GP 雷达覆盖计数 | 现只统计 iOS 覆盖（[`routers/publishers.py:607`](../backend/app/routers/publishers.py)），GP 侧无独立计数 | 一行级小修，顺手做 |
| ~~own_products 补录~~ | ~~同赛道机制建好但我方只录 1 条（无尽火线）；多产品线录入即扩 ⚔️ 覆盖~~ | **✅ 2026-07-16 完成**（途游扩档 1→5 行：Blade War/My War/The War for Survival=基地建设SLG、Tavern Master=城建模拟；⚔️ 覆盖 4→约 70 app。运营动作零代码，走 `POST /api/products/`） |
| 手机轻量分享页 | 深链白屏是领导体验第一断点（间歇性），服务端轻页最对症 | 2026-06-28 已裁「先观察」；领导反馈点不开则第一个复活 |
| 命名回填 50+ 英文主体 | 既有 backlog | 运营动作，随调研顺手 |

---

## 三、明确不做（已裁决 / 已砍，勿重提）

soft-launch ST 雷达（烧配额，2026-06-27 否）· 视频停用词表（软删语料=0，路线已废）· 视频「置顶」（连删都没人用）· NEWCOMER_WINDOW 4→8 · 下载榜门控放开 · 短 alias 防线 / 5 按钮折叠（44-agent 审查证伪）· 缺口 ML 置信模型（个位数缺口，过度工程）· L3 跨 entity 同款合并 · 待建档段 LLM 语义门控（候选 ~1/天不值当）。

---

## 四、落地顺序 + 状态跟踪

```
第一批（一个 PR，全零 ST 零迁移）：
  P0-1① 走势读时计算 → P0-1② 前端走势列 → P2 health GP 计数（顺手）
第二批：
  P0-1③ 新品周察周报卡 + P0-2 一键晋升
第三批（各带小迁移，独立 PR）：
  P1-2 subgenre 回补（app_subgenre 表） → P1-1 雷达富化接入
持续观察触发：
  P1-3 digest 去重/封顶（攒一周领导卡长度后）· 手机分享页（领导反馈触发）
```

排序逻辑：第一批用最小成本先让「新品后来怎么样」在看板可见（验证数据质量）；第二批变成领导可感知的周报（价值兑现点）；第三批才动 schema。每批独立可回滚。

> **✅ 全部条目已部署 HK prod（截至 2026-07-05，runtime `#191`/`4bd9a14`→`27ef216`）。各机制的权威「怎么工作」说明已毕业进 [`ARCHITECTURE.md`](ARCHITECTURE.md) § 新品监测 + 每日 digest（本文件保留为审查 rationale + 落地流水）。**

| 项 | 状态 | PR | 备注（机制详见 ARCHITECTURE.md） |
|---|---|---|---|
| P0-1① 后端走势 | ✅ 已部署 | #186 | `compute_trajectories` + `/history` 附 `trajectory`；7 单测 |
| P0-1② 前端走势列 | ✅ 已部署 | #186 | 新品卡/抽屉 `TrendBadge` +「仍在爬升/已掉榜」筛选；prod 实拍 |
| P2 health GP 计数 | ✅ 已部署 | #186 | `/health` 加 `total_gp_artists`/`entities_without_gp_artist` |
| P0-1③ 周察周报卡 | ✅ 已部署 | #188 | `build_weekly_newcomer_review` + 周一 04:40 UTC job；两卡都发 |
| P0-2 一键晋升 | ✅ 已部署 | #188 | 抽屉「转入深度追踪」复用 `POST /games/`；纯前端 |
| P1-2 subgenre 回补 | ✅ 已部署 | #189 | `app_subgenre` 表/迁移 0039 + `classify_pending_app_subgenres` + digest fallback |
| P1-1 雷达富化接入 | ✅ 已部署 | #190 | `chart_type='radar'` 影子行 riding 富化 drain；「仅雷达段补 📝」方案 |
| 赛道脉搏视图 | ✅ 已部署 | #191 | `/newcomers/subgenre-pulse` + 前端折叠卡；prod 实拍 25 新品 |
| P1-3 digest 去重/封顶 | ⬜ 观察触发 | — | 攒领导卡长度（唯一未启动项，见 [[project_digest_leader_push_roadmap]]） |

> **第一批落地记录（2026-07-04，本地未部署，PR #186）**：三项全零 ST、零迁移，纯读时计算 + 加派生字段，回滚走纯代码。后端 pytest 601 / 前端 build + vitest 89 全绿。**视觉验证待部署**——走势 chip 需真实多快照榜单历史才显示，本地 mock 数据不足以演示，按项目既有「HK 代理预览」流程在部署后截图确认。
>
> **第二批落地记录（2026-07-04，本地未部署，stacked on #186）**：P0-1③ 周察周报卡（新增周级 job + config 旋钮 `DIGEST_WEEKLY_REVIEW_ENABLED/DAYS/CAP`，零迁移）+ P0-2 一键晋升（纯前端，复用现有 `POST /games/`）。后端 pytest **604**（+3 周察单测）/ 前端 build + vitest 89 全绿。周察卡视觉、晋升后版本/分地区追踪同样待部署后确认。
>
> **第三批落地记录（2026-07-04）**：两项独立 PR。**P1-2 subgenre 回补**（`app_subgenre` 表/迁移 0039 + `classify_pending_app_subgenres` + digest `_subgenres_for_apps` fallback + drain）解锁 ⚔️ 对 movement 老竞品；pytest **609**（+5）。**P1-1 雷达软启动新品接入富化**（用户选「仅雷达段补 📝」方案）：`ingest_artist_apps` 收集 SLG 真新上架 → `record_radar_newcomers` 写 `chart_type='radar'` **影子行**进 `market_newcomer_log`（riding 中文化/subgenre/视频 drain，字段本就随 iTunes lookup 拿到、只 LLM 字段留 NULL），`/history` 排除影子行不进市场网格，📝 摘要回显「商店雷达」区块 + digest 雷达段；config `RADAR_NEWCOMER_ENRICH_ENABLED`，零迁移；pytest **610**（+6）。两 PR 独立于 main，合并顺序 #189 先、P1-1 后（如冲突走 rebase）。

> **赛道脉搏视图落地记录（2026-07-05）**：P1-2 的 stretch。`GET /newcomers/subgenre-pulse?days=N` 近 N 天新品按 `subgenre_cn` 分布 + 环比上一等长窗口（按 app_id 去重、min 检出定窗口、忽略名单过滤、radar 影子行计入）；前端新品页市场视图折叠卡（CSS 横条 + 计数 + 升温↑/降温↓ 箭头，窗口跟随页面 days 筛选）。回答「哪个赛道在冒新品」。零 ST、零迁移（复用 `subgenre_cn`）；pytest **617**（+2）/ 前端 build + vitest 89。**至此 OPTIMIZATION-2026-07 全部条目落地**（除明确不做项）。
