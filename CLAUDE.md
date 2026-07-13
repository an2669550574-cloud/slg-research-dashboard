# 项目约定（slg-research-dashboard）

SLG 竞品调研看板：后端 FastAPI + SQLAlchemy 2(async) + SQLite + Alembic；前端 React 18 + TS 5 + Vite + TanStack Query + Tailwind。
数据源是 Sensor Tower（配额受限）+ 公司统一 LLM 网关（素材 AI 分析 / 创意迁移）。

> 本文件是**项目级硬规则**，进 git、对所有协作者与工具生效。**不要在此写任何敏感信息**（服务器 IP、域名、容器名、API key、Sentry DSN、网关地址等）——这些只存在于 `.env` / 运维私有渠道，本文件只引用「见 .env」。

## 本地开发

```bash
# 后端（默认 USE_MOCK_DATA=true，本地用 mock 数据、鉴权关闭）
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（dev server :3000，已把 /api 代理到 localhost:8000）
cd frontend && npm install && npm run dev
```

- 后端启动会自动 `alembic upgrade head`；`games` 表为空时插入 mock 起步集。
- 接真实数据：`backend/.env` 把 `USE_MOCK_DATA=false` 并填 key（见 `backend/.env.example`）。

## 校验

```bash
cd frontend && npm run build   # tsc -b && vite build，类型错误会在这暴露
cd frontend && npm run lint    # eslint：react-hooks/rules-of-hooks 崩页门（0 error 才过）
cd frontend && npm run test    # vitest
cd backend  && pytest
```

- **改前必看的防线现状**：
  - **React hooks 顺序**：抽屉 / 弹层组件所有 hooks 必须写在任何 early return **之前**；否则 prop 切换时 hook 数量变化会崩页。**已有 eslint 门**：`npm run lint`（`react-hooks/rules-of-hooks`）作为 error 拦这类违规、CI 强制；`exhaustive-deps` 只 warn（依赖遗漏不阻断 CI，改后自查）。
  - **CJK 数据**：素材 / 上传 / 文件流相关功能，验证必须用**中文测试数据**（lint / tsc / vitest 都抓不到）。纯 ASCII 夹具漏过中文名导致 `Content-Disposition` 500 的真 bug。

## Git / PR 流程

- 所有改动走 **feature 分支 → PR → squash merge → 线性 main**，不直接 push main。
- Commit message 用英文，遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` / `test:`）。
- 不要 `git push --force` 到 main，也不要主动建议。
- 部署前给当前 main 打 `rollback-<date>-<time>` tag，方便一行回退。

## Sensor Tower 配额（核心约束）

ST 调用受**公司池**与本地软护栏双重限制，配额是这个项目最硬的约束。新增任何 ST 调用前先掂量额度。

- **默认选省配额方案**：涉及 ST 的功能，优先做「配额最省」的设计，而不是「数据最新」。
- **能读本地库就别打 live ST**：详情页 / 对比页的排名趋势**故意**从本地 `game_rankings` 表出（零配额），不要「修」回 live ST。
- 同步节奏（市场 / 周期）是刻意调过的，别擅自加密。
- 配额相关现状与历次调整记录在提交历史与运维笔记，改动前先确认当前水位。

## LLM 集成

- 必须走**公司统一 LLM 网关**（OpenAI 兼容），key 与地址在 `backend/.env`，不进 git。
- 不要直连 Anthropic / OpenAI 官方端点。

## UI / 设计系统

改任何 UI 前先读现有组件，遵守「情报终端」设计系统硬约束：

- 字体**只用自托管**，不要引 Google Fonts CDN。
- 暗色为默认主题；URL 参数 `?theme=light|dark` 可覆盖 localStorage（用于无头截图等场景，默认行为不变）。
- 页面统一用 `PageHeader` 组件 + 设计 token（颜色 / 间距走 Tailwind 语义类，不写魔法值）。
- 改动保持可回滚（小步、单一职责）。

## 运维 runbook（`docs/`）

做相关操作前先读对应文件，别凭记忆重推：

| 文件 | 用途 |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 架构「为什么是这样设计的」：ST 配额体系（省配额同步节奏 US 每日 / 次市场双周 + 公司池软预留 + 本项目软护栏 200/月）+ 设计系统硬约束 + 新品监测/每日 digest 机制（is_reentry / 各 TOPN 配置 / 滞后横幅 / 重要度排序+今日要闻 / 领导群vs维护者**双卡分发**(audience+target 路由) / **markdown 转义**(`_md_name`) / **同赛道**(竞品玩法子品类 `subgenre_cn` vs `own_products.match_subgenre` 精确匹配，原题材关键词太宽泛已降级回退) / **微信文章联动**(四层来源对称+关键词优先级) / **微信登录续期**(#173 钉钉按钮→`/wechat-login` 扫码、透明反代) / **新品生命周期**(OPTIMIZATION-2026-07 六项全零 ST：走势追踪 `compute_trajectories`/周察卡/一键晋升 tracked/subgenre 回补 `app_subgenre`/雷达 `chart_type='radar'` 影子行富化/赛道脉搏 `subgenre-pulse`)）+ 标签库 + 产品作用域（维度/选项各自挂 app_id 名单，空 = 通用）。**改相关代码前必读** · 产品审查 rationale 见 [`docs/OPTIMIZATION-2026-07.md`](docs/OPTIMIZATION-2026-07.md) |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | 部署 / 更新 |
| [`docs/CONFIG.md`](docs/CONFIG.md) | 配置速查表：118 项里 prod 调过的**活配置** vs 纯默认 + 为何调 + **改后怎么生效**（`restart` 不重读 .env！`--env-file up -d` 才重建；`API_KEY` 要重构前端）+ 坑 |
| [`docs/ROLLBACK.md`](docs/ROLLBACK.md) | 回滚（纯代码 / 带迁移两种路径） |
| [`docs/BACKUP.md`](docs/BACKUP.md) | 备份 / 恢复 |
| [`docs/MIGRATION.md`](docs/MIGRATION.md) | 换机迁移 |
| [`docs/ANALYSIS.md`](docs/ANALYSIS.md) | 素材 AI 分析流程 |
| [`docs/PUBLISHERS.md`](docs/PUBLISHERS.md) | 厂商主体/资本系调研建档：方法论 + 系统在哪表（含 `/health` 自检[带雷达覆盖率] + `/gaps` 缺口[带 days_on_chart + newcomer 回流置信信号] + `/download-leads` 下载榜早期信号 + `/itunes-artist-suggestions` 雷达覆盖建议[iOS+GP 双侧反解开发者账号] + `/ignores` 忽略名单 + sibling 去重 + brief 戳记折叠）+ 资本集团速览 + 命名易混淆点 |
| [`docs/SECURITY-2026-07.md`](docs/SECURITY-2026-07.md) | 安全加固计划 + 执行进展（威胁模型：CT 日志暴露 → 前端 bundle 含 API_KEY → 媒体 HMAC 复用同一 key）。**已落地** #194 LLM 成本护栏（记账修全 3 表 + 日 $5/月 $30 + 触顶告警）/ #195 媒体签名解耦（`MEDIA_SIGNING_SECRET`）/ P1-2 域名+CF；**认证裁定不做**（受众内部） |
| [`docs/SECURITY-CADDY-DOMAIN.md`](docs/SECURITY-CADDY-DOMAIN.md) | 域名+Cloudflare 执行手册。**Part B 已执行 2026-07-05**：prod 现服务 `https://slgradar.uk`（CF 橙云代理 + 源站 CF-only 防火墙 `cf-firewall.service`，nip.io 退役）。含改 is_slg / 部署 / 回滚配方（Part A Caddy 认证按裁定不做） |
| [`docs/adr/`](docs/adr/) | 架构决策记录（难回滚 / 易困惑 / 有取舍的决策判案笔记）：0001 = 榜单加 `chart_type` 维度并行采集下载/免费榜；0002 = 新品自动搜集竞品实机玩法视频（YouTube，已上线）；0003 = tracked iOS 竞品版本变更追踪（需求②，已上线；Android 无版本源故 **iOS-only**，进 GameHistory + 每日 digest）；0004 = tracked iOS 竞品分地区上线时间对照（需求② 子项③，已上线；**iOS-only**，专用表 `game_region_release` + 周级 job + GameDetail 区块）；0005 = RSS 早鸟信号层（次市场新品零 ST 日级补偿：Apple 旧版 genre RSS 日拉 JP/KR 策略畅销 diff `rss_chart_seen` 台账，真早鸟落 `chart_type='rss'` 影子行 riding 富化管道 + 仅维护者卡「⚡ 早鸟」段，**绝不写 game_rankings**）；0006 = 厂商开发者账号盯新品=**上榜前探测层**（商店雷达升格，与 0005 并列：账号覆盖 134 + digest 雷达段每日推送+📰（维护者有则即显 / 领导仅平淡日），切片 3 主动 SLG 分类未做） |

- 部署：PR squash 合入 main 后，服务器 `git pull --ff-only` + `docker compose -f docker-compose.prod.yml --env-file .env up -d --build`；backend 启动自动跑迁移，无需手动 alembic。
- `API_KEY` 来自**根目录 `.env`**（compose `--env-file`），不是 `backend/.env`；同一个值会被前端构建时编进 `VITE_API_KEY`。
