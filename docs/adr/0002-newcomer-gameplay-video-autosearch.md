# ADR 0002：新品监测自动搜集竞品实机玩法视频（YouTube Data API）

- 状态:**Accepted — 1a/1b/1c 已实现、部署 HK 并上线生效（2026-06-26）；query 调优已应用。后续仅「置顶」去噪未做**
- 日期:2026-06-26
- 关联:领导对新品监测的需求 ①（竞品新品自动搜集实机玩法视频）；`docs/ARCHITECTURE.md`「新品监测 + 每日情报 digest」；省配额哲学（`docs/CLAUDE.md` ST 配额节）

> ADR = Architecture Decision Record。只记录**难回滚、易让后来者困惑、且有真实取舍**的架构决策。格式:状态 / 背景 / 决策 / 后果 / 备选方案 / 待定问题。
> **本 ADR 是给后续会话（可能换账号继续）的交接判案笔记**：方案 B 已实现 1a/1b/1c 并部署上线（2026-06-26）；接手前先读「当前进度」段看最新状态。

---

## 背景

领导对新品监测提了三条原始需求（2026-06，口头；记录见 memory `project_newcomer_boss_requirements`）：

1. **新 SLG 监测到后，自动搜集其「实机玩法视频」** ← 本 ADR 解决这条
2. 自动整理分地区上线时间 / 版本更新 / 排行显著变化（部分已落地）
3. 把苹果/谷歌详情页有效信息填充网站（PR #117 切片 3 已交付）

需求 ① 此前完全空白。现有 `services/product_analyze.py` 只分析**我方自有产品**手动上传的素材，作用对象和触发方式都不对，不能复用。

**难点**：竞品实机玩法视频不在 App Store / GP 详情页（那是厂商宣传片预告），真正的「实机玩法」基本在 **YouTube**（TikTok 偏短、检索难、无好 API）。所以问题收敛为：**怎么按游戏名自动找到 YouTube 上的实机视频**。

这是项目里**首次引入「主动出站搜索」**——与现有「零 ST、纯读本地、免费源富化」哲学不同，绕不开新外部依赖（YouTube Data API）和一套新配额。

## 决策

走**方案 B：真·自动搜集**（用户 2026-06-26 拍板，否决了纯 A「一键查」与全自动 C「LLM 筛选」，见备选方案）。

```
detect 新品(已有，零 ST)
   └─→ 新增:对每个新 app_id 调 YT Data API search.list("游戏名 gameplay")
         └─→ 取前 N 条(标题/缩略图/videoId/频道/时长)落候选表
               └─→ 前端抽屉「实机视频」段展示，人工置顶/删噪
```

**核心原则:YouTube 是独立配额，完全不碰 ST**——不破坏「新品监测零 ST」哲学，只新增一套自管的 YT 配额。

### 纵切片拆分（每片可独立验收）

| 切片 | 端到端行为 | 验收 |
|---|---|---|
| ✅ **1a 后端搜索服务**（2026-06-26 done，commit 68291ff） | `services/youtube_search.py`:`search_gameplay_videos` 调 YT search.list 返回候选；`evaluate_search_gate` 纯函数判去重/日上限/排次日（护栏逻辑就位，状态喂数据待 1b 接 db） | ✅ pytest 4 passed:中文游戏名夹具（CJK）、无 videoId 过滤、key 缺失短路、请求异常吞掉、护栏三态 |
| ✅ **1b 落库 + 触发挂载**（2026-06-26 done，全量 468 passed） | 两张表 `newcomer_video`（候选）+ `newcomer_video_search`（搜索台账=去重锚点+当日计数；「待搜」隐式 = log 里近 LOOKBACK 天未搜的 app，无队列状态机）；`services/newcomer_video.py::sync_newcomer_videos` drain 由**独立 daily scheduler job**（03:50 UTC）触发——非 record 内联（解耦、不拖垮同步、配额可控）。alembic 0029 两表 create。 | ✅ pytest 5 passed（落库/去重/日上限/no-op/lookback）+ 迁移 up/down smoke + 全量回归 |
| ✅ **1c 前端展示**（2026-06-26 done） | 后端读端点 `GET /newcomers/videos?app_id=` + 删端点 `DELETE /newcomers/videos/{id}`（零 ST，删后不会被搜回——台账已记 done）；前端 `NewcomerVideoSection`（market + publisher 两抽屉共用）：缩略图卡 + 标题 + 频道/日期 + 跳 YT + 人工删噪。无候选整段不渲染。**人工「置顶」未做**（去噪用删即够，置顶需加列迁移，留后续）。 | ✅ preview 实测（视频段渲染 + 删交互 3→2 持久化 + 无 console error）；后端 6 测试；前端 build 过；hooks 全在 early return 前 |

### 前置 blocker（实现前必须先备齐）

1. ✅ **YouTube Data API v3 key**（2026-06-26 已就位、本地验证可用）:变量名定为 **`YOUTUBE_API_KEY`**，放 `backend/.env`。免费配额 **1 万 units/天**，`search.list` = **100 units/次** → **约 100 次搜索/天**。本地实调中文 query 返回 HTTP 200、CJK 正常。
2. ⬜ `.env.example` 加 `YOUTUBE_API_KEY=` 占位（开发切片 1a 时一并做，未做）。
3. ✅ **服务器出站确认**（2026-06-26 部署后实测）:HK backend 容器访问 `googleapis.com` 可达（HTTP 400 = Google 应答，无效 key 被拒属预期）。

## 后果

**正面**
- 补齐领导需求 ① 的全空白；竞品新品一检出就自动攒好实机玩法视频候选，省人工搜集。
- YT 独立配额，不动 ST 池水位。

**负面 / 代价**
- 首次引入主动出站搜索 + 一套新外部配额要自管（YT 1 万 units/天）。
- **带 schema 迁移**（新表）：部署前按 `docs/ROLLBACK.md` 打 `rollback-` tag。
- 配额风险：每日新品检出量可能逼近 100 搜/天上限 → 必须有软护栏（见待定问题 2）。
- 视频命中质量靠 YT 搜索相关性 + 人工去噪（B 不做 LLM 自动判真，避免 C 的误判 + LLM 网关费）。

**回退**:YT key 留空 = 搜索服务返回空、不触发、不落库（与 enrich `found=False` 同哲学）；`newcomer_video` 表保留无害。

## 备选方案（已否决）

| 方案 | 否决原因 |
|---|---|
| **A 纯一键查**（前端实时拼 `youtube.com/results?search_query=游戏名+gameplay` 深链，零 API 零落库） | 不「搜集」任何视频，只给人工搜索入口；不满足领导「自动搜集」诉求。曾作为推荐的最小起步方案，用户选择直接上 B |
| **C 全自动 + LLM 筛选**（B 基础上把候选喂 LLM 判真实机玩法并自动置顶） | LLM 误判风险 + 撞太石网关 $50/天预算；人工去噪已够，暂不上。留作 B 跑通后的可选增强 |
| 复用 `materials` 表存候选 | 与我方买量素材语义混淆；倾向新建独立 `newcomer_video` 表解耦（待定问题 1） |

## 设计决策（2026-06-26 已拍板）

1. **落库**:✅ **新建独立 `newcomer_video` 表**（与我方买量素材 `materials` 解耦，语义清晰）。字段:`app_id` / `video_id` / `title` / `thumbnail` / `channel` / `url` / `rank`(候选序) / `created_at`；建议 `video_id` 或 `(app_id, video_id)` 唯一防重。
2. **配额护栏**:✅ 三条并用 ——
   - ① **同 app_id 不重复搜**（落库前查该 app_id 是否已搜过，搜过即跳；去重锚点 = `newcomer_video` 有无该 app_id 行，或单独「已搜 app_id」标记）。
   - ② **每日搜索硬上限 80 次**（留余量，免费池 100/天；按 UTC 日计数）。
   - ③ **超额排次日**（当日触达 80 后，新检出 app_id 进「待搜」队列/标记，次日配额恢复再搜，不静默丢）。
3. **每个新品存条数**:✅ **前 5 条**（`search.list` `maxResults=5`，一次调用 100 units；够人工挑、不刷屏）。

> 实现细节（去重锚点用独立表还是查 `newcomer_video`、待搜队列怎么落、UTC 日计数存哪）留到切片 1a/1b 落码时定，不属架构层。

## 当前进度

- 2026-06-26:方向拍板走 B，本 ADR 落盘。**代码未开始**。
- 2026-06-26:YT API key 已就位并本地验证可用（变量名 `YOUTUBE_API_KEY`，中文 query 实测 HTTP 200 / CJK 正常 / 召回 5 条）。**观察**：query「游戏名 gameplay」召回里混攻略/解说/玩家心态视频，非全是纯实机——印证 B「靠搜索相关性 + 人工去噪」的取舍；开发时 query 可调优（试加 `walkthrough`/`实机`/排除 `直播` 等）。
- 2026-06-26:三处设计点已拍板（独立 `newcomer_video` 表 / 同 app_id 不重复搜 + 日上限 80 + 超额排次日 / 每新品存前 5 条），见「设计决策」段。
- 2026-06-26:PR #117 已合入 main（51fbedb）；从 main 新开分支 `feat/newcomer-gameplay-video`，已落 ADR docs commit（1fa7d9c）+ **切片 1a 完成并推送**（68291ff，pytest 4 passed）。配置含 `YOUTUBE_SEARCH_DAILY_CAP=80` / `MAX_RESULTS=5` / `QUERY_SUFFIX=gameplay`。
- 2026-06-26:**切片 1b 完成**——两表（`newcomer_video` + `newcomer_video_search`）+ 迁移 0029（可逆）+ `sync_newcomer_videos` drain + 独立 daily job（03:50 UTC）+ 5 测试；全量回归 468 passed。实现微调:触发改**独立 daily job**（非 record 内联，解耦防拖垮同步）；新增 `YOUTUBE_SEARCH_LOOKBACK_DAYS=30`（只搜近 30 天检出，防首搜把 365 天历史全量搜爆配额）。
- 2026-06-26:**切片 1c 完成**——读/删端点 + `NewcomerVideoSection`（两抽屉共用）+ i18n；preview 实测渲染/删交互/无 console error，前端 build + 后端 6 测试过。至此 **1a/1b/1c 三切片全完成**，方案 B 端到端打通（检出 → 定时搜 → 落库 → 抽屉展示 → 人工删噪）。
- 2026-06-26:**PR #118 已合 + 部署到 HK**（main 3ede7e4；打了 rollback-20260626-1441 锚点；迁移 0028+0029 自动跑通、两表已建；backend healthy；HK 出站 googleapis.com 实测可达）。
- 2026-06-26:HK `backend/.env` 填了 `YOUTUBE_API_KEY` + force-recreate 重启；prod 端到端验证 `sync_newcomer_videos(daily_cap=3)` = 搜 3 落 15 真实视频，**需求① 上线生效**。定时 job 03:50 UTC 搜剩余。
- 2026-06-26:**query 调优（已应用）**——prod 对比实验定方案：**游戏名加引号精确匹配**。最糟案例 `탑 로드`（Top Lords，通用短名）裸搜 `탑 로드 gameplay` 全是 Million Lords/Bannerlord/赛马娘等拆词噪声；`"탑 로드" gameplay` 大半命中真实机。对独特名（Infinity Kingdom / Order of Kings）引号无害。**否决 `videoDuration=medium`**：实测会把实机（常为 short 片段 / long 完整实况）滤掉、只留中等时长解说。结论 = q 加引号、不加时长过滤；残余噪声靠前端「删」人工去噪（数据源固有局限：YT 内容稀薄的游戏 query 救不了）。
- 后续增强（非阻塞）：「置顶」去噪（去噪用删即够，置顶需加列迁移）。
- 同期 PR #117（切片 3.1+3.2，需求 ③）已推送待合，与本 ADR 无依赖。
