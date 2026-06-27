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
cd frontend && npm run test    # vitest
cd backend  && pytest
```

- **tsc + vitest 抓不到的两类 bug，改前必看**：
  - **React hooks 顺序**：抽屉 / 弹层组件所有 hooks 必须写在任何 early return **之前**；否则 prop 切换时 hook 数量变化会崩页，静态检查抓不到。
  - **CJK 数据**：素材 / 上传 / 文件流相关功能，验证必须用**中文测试数据**。纯 ASCII 夹具漏过中文名导致 `Content-Disposition` 500 的真 bug。

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
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 架构「为什么是这样设计的」：ST 配额体系（省配额同步节奏 US 每日 / 次市场周级 + 公司池软预留）+ 设计系统硬约束 + 新品监测/每日 digest 机制（is_reentry / 各 TOPN 配置 / 滞后横幅）+ 标签库 + 产品作用域（维度/选项各自挂 app_id 名单，空 = 通用）。**改相关代码前必读** |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | 部署 / 更新 |
| [`docs/ROLLBACK.md`](docs/ROLLBACK.md) | 回滚（纯代码 / 带迁移两种路径） |
| [`docs/BACKUP.md`](docs/BACKUP.md) | 备份 / 恢复 |
| [`docs/MIGRATION.md`](docs/MIGRATION.md) | 换机迁移 |
| [`docs/ANALYSIS.md`](docs/ANALYSIS.md) | 素材 AI 分析流程 |
| [`docs/PUBLISHERS.md`](docs/PUBLISHERS.md) | 厂商主体/资本系调研建档：方法论 + 系统在哪表（含 `/api/publishers/health` 自检端点 + `/api/publishers/gaps` 缺口端点 + `/ignores` 缺口忽略名单 + sibling 去重 + brief 戳记折叠）+ 资本集团速览 + 命名易混淆点 |
| [`docs/adr/`](docs/adr/) | 架构决策记录（难回滚 / 易困惑 / 有取舍的决策判案笔记）：0001 = 榜单加 `chart_type` 维度并行采集下载/免费榜；0002 = 新品自动搜集竞品实机玩法视频（YouTube，已上线）；0003 = tracked iOS 竞品版本变更追踪（需求②，已上线；Android 无版本源故 **iOS-only**，进 GameHistory + 每日 digest）；0004 = tracked iOS 竞品分地区上线时间对照（需求② 子项③，已上线；**iOS-only**，专用表 `game_region_release` + 周级 job + GameDetail 区块） |

- 部署：PR squash 合入 main 后，服务器 `git pull --ff-only` + `docker compose -f docker-compose.prod.yml --env-file .env up -d --build`；backend 启动自动跑迁移，无需手动 alembic。
- `API_KEY` 来自**根目录 `.env`**（compose `--env-file`），不是 `backend/.env`；同一个值会被前端构建时编进 `VITE_API_KEY`。
