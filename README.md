# SLG Research Dashboard

海外 SLG（策略类手游）竞品监控与调研仪表盘。聚合 Sensor Tower 排行、下载与收入数据，结合 Claude 自动生成的发展历程，为出海产品研究团队提供一站式视图。

> **状态**：内部工具，仍在迭代。后端默认走 mock 数据，接入真实 Sensor Tower API 后即为生产可用。

---

## 功能一览

- **仪表盘**：今日榜单概览、Top 8 收入/下载柱图、统计卡片
- **排行榜**：按国家（8 国）、平台（iOS / Android）、关键字筛选；支持 CSV 导出
- **游戏详情**：3 条趋势图（收入/下载/排名）、AI 历程时间轴、素材库
- **游戏对比**：选择 2-3 款游戏叠加趋势曲线
- **素材库**：YouTube / TikTok / Meta Ads 创意链接收藏
- **游戏管理**：从 iTunes 一键拉取元信息后录入；支持删除追踪
- **明暗主题 + 中英双语 + Toast 通知 + CSV 导出 + 自定义日期范围**

---

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI · SQLAlchemy 2 (async) · SQLite + aiosqlite · APScheduler · Alembic · Anthropic SDK · httpx |
| 前端 | React 18 + TypeScript 5 · Vite 5 · TanStack Query 5 · Recharts · Tailwind CSS 3 |
| 部署 | Docker · docker-compose · Nginx 静态托管 |
| 测试 / CI | pytest + pytest-asyncio · GitHub Actions |

---

## 目录结构

```
.
├── backend/                # FastAPI 服务
│   ├── app/
│   │   ├── main.py         # 应用入口（CORS、鉴权、scheduler 生命周期）
│   │   ├── config.py       # 环境变量 / settings
│   │   ├── database.py     # async engine + alembic upgrade on boot
│   │   ├── security.py     # X-API-Key 中间件
│   │   ├── scheduler.py    # APScheduler 每日同步任务
│   │   ├── models/         # SQLAlchemy 模型
│   │   ├── routers/        # 路由（games / history / materials）
│   │   ├── schemas/        # Pydantic 输入/输出 schema
│   │   └── services/       # 外部 API 封装（Sensor Tower / iTunes / Claude）
│   ├── alembic/            # 数据库迁移
│   ├── tests/              # pytest 集成测试
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── frontend/               # React 前端
│   ├── src/
│   │   ├── App.tsx         # 路由 + 侧边栏
│   │   ├── pages/          # 5 个主页面
│   │   ├── lib/            # api / utils / csv / theme
│   │   └── i18n/           # 中英文字典 + useT() hook
│   ├── nginx.conf
│   ├── Dockerfile
│   └── package.json
├── mock-server/            # （可选）独立 Node mock，用于纯前端开发
├── docker-compose.yml
└── .github/workflows/ci.yml
```

---

## 本地起步（开发模式）

### 后端

```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # 第一次需要复制
uvicorn app.main:app --reload --port 8000
```

启动时会自动 `alembic upgrade head`，并在 `games` 表为空时回填 8 款 mock 游戏。

打开 http://localhost:8000/docs 查看 OpenAPI 文档。

### 前端

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:3000，Vite 会把 `/api/*` 代理到后端 `localhost:8000`。

### 跑测试

```bash
cd backend
pytest -v
```

---

## 环境变量

后端 `.env`（参考 [`backend/.env.example`](backend/.env.example)）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./slg_research.db` | 数据库连接 |
| `USE_MOCK_DATA` | `true` | 留 true 时所有 Sensor Tower 调用走 mock |
| `SENSOR_TOWER_API_KEY` | _空_ | 真实 key，填上后自动切真接口；调用失败时回落 mock |
| `ANTHROPIC_API_KEY` | _空_ | Claude key（用于 AI 历程生成） |
| `API_KEY` | _空_ | 留空时所有端点免鉴权（仅本地）；填值后所有端点要求 `X-API-Key` 头部匹配；`/api/health` 始终免鉴权 |
| `CORS_ORIGINS` | `*` | 逗号分隔白名单；通配符不与 `credentials=true` 同时启用 |

前端 `.env`（在 `frontend/` 目录下）：

| 变量 | 说明 |
|---|---|
| `VITE_API_KEY` | 与后端 `API_KEY` 相同；构建时注入到 axios 默认头 |

---

## 主要 API

启动后访问 http://localhost:8000/docs 看交互式文档。常用端点：

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/health` | 健康检查（免鉴权） |
| `GET` | `/api/games/` | 游戏列表（支持 `q`、`platform`、`country`、`publisher`、`sort_by`、`order`、`limit`、`offset`；返回头 `X-Total-Count`） |
| `POST` | `/api/games/` | 创建游戏；iOS 数字 ID + 缺字段会自动 iTunes 补全 |
| `POST` | `/api/games/lookup?app_id=...` | iTunes 元信息预览（不入库） |
| `DELETE` | `/api/games/{app_id}` | 删除游戏（关联历程/素材保留） |
| `GET` | `/api/games/{app_id}/metrics` | 30 天/自定义区间趋势 (`days` 或 `start_date`+`end_date`) |
| `GET` | `/api/games/rankings` | 当日榜单 |
| `POST` | `/api/games/sync-rankings` | 手动触发一次定时任务 |
| `GET` | `/api/games/seed` | 一键写入 8 款 mock 游戏（仅初始化用） |
| `GET / POST / DELETE` | `/api/history/...` | 时间轴 CRUD + AI 同步 |
| `GET / POST / PUT / DELETE` | `/api/materials/...` | 素材库 CRUD（支持筛选 / 分页） |

---

## 部署（开发版 docker-compose）

```bash
docker compose up -d --build
# 前端: http://localhost
# 后端: 通过 nginx 反代到 frontend 容器内 /api/*
```

> **注意**：当前 `docker-compose.yml` 没有 HTTPS、没有外部反代；生产部署需要补一层 Caddy / Traefik。计划后续提供 `docker-compose.prod.yml`。

---

## 计划中的工作

- 结构化日志（JSON + 请求 ID）
- 限流（slowapi）
- Sensor Tower 响应缓存（命中数据库，避免日均同步撞配额）
- 生产部署模板：docker-compose.prod.yml + Caddy 自动签发
- DB 备份脚本
- Sentry 集成 + 深度健康检查（DB / Sensor Tower 可达性）

---

## 贡献

欢迎 PR。提交前请确保：

```bash
cd backend && pytest -v          # 后端测试通过
cd frontend && npm run build     # 前端 TS + Vite 构建通过
```

CI 会自动跑这两步加上 alembic 升降级 smoke test。
