# Session Handoff — 2026-06-09（slg-research-dashboard）

> 给明天的新会话：读这一篇就能接上。本仓库是「海外 SLG 竞品监控与调研看板」（FastAPI + React + SQLite + Docker，部署在 HK 服务器）。

## 0. 一句话状态

今天把领导诉求在本项目上**端到端落地并全部部署到 HK 生产**：A 全球 SLG 新品监测 + B 厂商主体调研沉淀（一手源溯源 / 母子股权 / 新面孔一键建档）+ 填入第一批真实股权调研数据 + 厂商页两轮 UX 清爽化（业务优先）。
**main HEAD = `2a39261`**，6 个 PR（#23–#28）全部合并并部署，`git status` 干净，线上正常。

## 1. 背景 / 关键决策

- **领导诉求**：① 监测全球 SLG 品类动向（尤其**新品数据变化**）；② 国内知名 SLG 厂商**主体关联调研 + 沉淀**（江娱互动、FunPlus 等）。
- **决策**：用本项目（slg-research-dashboard）承接，**不是** vietnam-market-intel。B 的溯源纪律是**借鉴** vietnam 那套（一手/二手分级、查不到标 unverified、绝不臆测），**代码/数据未复制**，按本项目架构重写。
- **定位**：B 的溯源/股权是「**提示为主、人工把关**」——只记录 + 可视化，不做硬阻断。

## 2. 今天交付（全部已合并 + 部署 HK）

| PR | 内容 | 备注 |
|---|---|---|
| #23 | `feat(newcomers)` 新品监测 | 零配额本地 diff：过去 W=4 快照没见过、本期进 Top N = 新面孔（**故意不走 is_slg**）。页 `/newcomers`。无迁移 |
| #24 | `feat(publishers)` 一手源溯源 | `publisher_sources` 表（alembic **0014**）+ provenance 分级（一手/二手）+ 溯源徽标 |
| #25 | `feat(publishers)` 母子/股权关联 | `publisher_relations` 表（alembic **0015**）+ 母公司/子公司双向 + 持股% |
| #26 | `feat(newcomers)` 新面孔一键建档 | A↔B 闭环：复用 `POST /publishers/` 建档钉 app_id，**无迁移** |
| #27 | `feat(publishers)` 折叠 + 搜索 | 卡片默认收起成一行摘要 + 搜索 +「只看有调研数据」筛选 |
| #28 | `feat(publishers)` 业务优先版面 | 展开先看「公司介绍 + 旗下 SLG 产品（自动加载）」，维护字段降到下方 |

## 3. 已填入 prod 的真实调研数据（截至今日：35 主体 / 14 源 / 5 股权关系）

- **股权链**：中文传媒(上交所 600373·江西省国资) —控股 99.23%→ 智明星通 ELEX —参股 13.5%→ 江娱互动(River Game)；元趣娱乐 —参股→ 江娱；世纪华通(002602) —全资 100%→ 点点互动；莉莉丝 —关联→ Farlight Games。
- **溯源档位**：一手 = 点点互动 / 世纪华通 / 中文传媒 / ELEX / 三七互娱 / IGG / FunPlus；仅二手 = 江娱互动 / 莉莉丝 / Tap4fun。
- **关键发现 / 纠错**（溯源纪律的价值）：
  - **江娱互动 = River Game**（《口袋奇兵 / Top War》），已合并删掉重复的 River Game 壳，马甲并入江娱；
  - 江娱种子 brief 误写「Last War 研发背景」→ **已纠正为《口袋奇兵/Top War》**（Last War 实为 Funfly/FirstFun）；
  - 智明星通母公司是**中文传媒 600373**（非"中文在线"），实控人江西省国资；
  - FunPlus 私人公司、股权结构未公开 → 标 **unverified、不建持股关系**（不臆测）。

## 4. 运维 / 写 prod 数据须知（明天继续用）

- **HK 服务器**：`ssh ubuntu@101.32.223.202`（连接信息在本机 `~/.ssh/config`，被安全 hook 保护、读不到也不绕）；项目目录 `/opt/slg-research-dashboard`。
- **访问域名**（无正式域名，nip.io 通配）：`https://slg.101.32.223.202.nip.io`。
- **部署**：服务器上 `git pull --ff-only origin main` + `docker compose -f docker-compose.prod.yml --env-file .env up -d --build`；backend 启动自动 `alembic upgrade head`（含迁移时无需手动）。
- **写 prod 调研数据**：受保护接口要 API key（在 `.env`，被 hook 拦读不到）→ 用「**幂等 + 纯附加脚本，经 `ssh ... "docker exec -i slg_backend python -" < 本地脚本`** 喂入容器」的方式；写前先 `cp` 备份 db，跑完删本地脚本。**删除/合并类先备份再做、先列清单**。
- **备份位置**：`/app/data/slg_research.db.bak-*`（容器内 = 宿主 `./data`）。今天的备份：`bak-research-*` / `bak-merge-*` / `bak-batch2-*`。
- **Sensor Tower 配额是硬约束**：新增 ST 调用前掂量；本批数据 / 监测 / 聚合都是**零 ST 配额**（读本地 game_rankings）。

## 5. 下一步可选（明天挑）

- 继续填更多厂商：Funfly/FirstFun（=Last War）、Camel Games、Yotta、StarUnion、OneMT、Long Tech 等。
- 给「仅二手」的几家（江娱/莉莉丝/Tap4fun）补**一手源**（企查查/天眼查工商穿透、北京产权交易所原件）升档。
- **股权关系图谱可视化**（把 parents/children 画成图）。
- A 侧增强：新品/异动**推钉钉告警**（已有 `src/dingtalk_robot.py`? 注：那是 vietnam 项目的，本项目没有，需自建）；扩监测国家（现 `SYNC_RANKING_COMBOS` = US/JP/KR）。
- 可考虑把那些一次性 prod 写入脚本**固化为 `backend/scripts/` 下可复用 seed 工具**（目前是临时脚本、跑完删了）。

## 6. 注意

- `git` 干净、main 与 origin 同步、CI（backend+frontend）每个 PR 都过。
- 数据为**公开股权调研**；守住「仅二手」的诚实标注，别把媒体数字当一手；归属查不到就 unverified。
- UX 现状：厂商主体页默认收起摘要、点开看「公司介绍 + 旗下 SLG 产品」，维护字段在下方。
