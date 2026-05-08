# Backend

FastAPI + SQLAlchemy (async) + SQLite，参考根目录 [README](../README.md) 获取整体说明。

## 起步

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

## 数据库迁移

启动时会自动 `alembic upgrade head`。手动操作：

```bash
alembic --config alembic.ini history          # 查看版本
alembic --config alembic.ini upgrade head     # 升级
alembic --config alembic.ini downgrade -1     # 回退一版
alembic --config alembic.ini revision --autogenerate -m "your message"
```

## 测试

```bash
pytest -v
```

测试用临时 SQLite 文件，不会污染 dev 库。

## 模块速览

- `app/main.py` — FastAPI 入口；CORS、`X-API-Key` 鉴权依赖、scheduler 生命周期都挂在 `lifespan`
- `app/config.py` — `pydantic-settings` 集中读环境变量；`cors_origin_list` 计算属性区分通配/白名单
- `app/database.py` — async engine + 启动时 `alembic.command.upgrade`
- `app/security.py` — `require_api_key` 依赖，未配置 `API_KEY` 时直通
- `app/scheduler.py` — APScheduler；`sync_daily_rankings` 每日 02:30/02:35 UTC 抓 US iOS / Android
- `app/services/sensor_tower.py` — Sensor Tower 客户端 + mock；所有外网调用都 try/except 后落 mock
- `app/services/appstore.py` — iTunes Search API（公开）；用于游戏元信息补全
- `app/services/ai_history.py` — Claude 调用 + 本地 mock；解析失败/网络失败回落 `DEFAULT_HISTORY`

## 添加新端点的清单

1. 在 `app/models/` 加/改 SQLAlchemy 模型
2. `alembic --config alembic.ini revision --autogenerate -m "..."` 生成迁移
3. 在 `app/schemas/` 写 Pydantic 输入/输出 schema（输出 schema 必须 `model_config = ConfigDict(from_attributes=True)`）
4. 在 `app/routers/` 写路由，**绑定 `response_model`**（绝不要 `return obj.__dict__`）
5. 在 `tests/` 加集成测试（参考 `test_games.py`）
6. 跑 `pytest` + `python -m py_compile $(git ls-files '*.py')` 确认通过
