# 部署指南

适用于自托管单机部署。多副本需要换掉 `app/cache.py` 的内存缓存为 Redis、把 SQLite 换为 Postgres。

## 一、准备机器

- Linux 主机，Docker 24+ 与 Docker Compose plugin
- 80 / 443 端口对外开放
- 把目标域名 A 记录指向主机 IP（Caddy 会自动签发 Let's Encrypt 证书）

## 二、配置

```bash
git clone <this repo>
cd slg-research-dashboard

# 1. 后端 .env
cp backend/.env.example backend/.env
# 编辑：填上 SENSOR_TOWER_API_KEY、ANTHROPIC_API_KEY、把 USE_MOCK_DATA 改 false

# 2. 根目录 .env（compose 用）
cp .env.example .env
# 编辑：填 SLG_DOMAIN、SLG_TLS_EMAIL、API_KEY、CORS_ORIGINS
```

`API_KEY` 是同一个值，会被前端构建时编译进去（VITE_API_KEY），同时后端读取同名变量做鉴权。

## 三、启动

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

首次启动 Caddy 会向 Let's Encrypt 申请证书（约 30 秒）。访问 `https://<SLG_DOMAIN>` 验证。

后端容器启动时会自动 `alembic upgrade head`；如果 `games` 表为空，会插入 8 款 mock 游戏作为起步集。

## 四、查看状态

```bash
# 容器
docker compose -f docker-compose.prod.yml ps

# 后端日志（已是 JSON 格式，可以 jq 过滤）
docker logs slg_backend --tail 200 -f | jq -r 'select(.level=="ERROR")'

# 缓存状态
curl -H "X-API-Key: $API_KEY" https://<SLG_DOMAIN>/api/cache/stats

# 健康检查
curl https://<SLG_DOMAIN>/api/health
```

## 五、更新

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

后端镜像重启时 alembic 会跑新迁移；前端会重新构建并把新 VITE_API_KEY 编进去。

## 六、回滚

```bash
git checkout <prev-tag>
docker compose -f docker-compose.prod.yml up -d --build
# 如果迁移不可向前兼容，需要先 alembic downgrade（见 ROLLBACK.md）
```

## 七、备份

参考 [`docs/BACKUP.md`](BACKUP.md)。

## 常见问题

**Caddy 一直拿不到证书？**
- 确认域名 A 记录解析正确：`dig <SLG_DOMAIN>`
- 确认 80 端口可达（Let's Encrypt HTTP-01 校验）
- 看 Caddy 日志：`docker logs slg_caddy`

**前端登录后所有请求都 401？**
- 容器构建时没传 VITE_API_KEY；rebuild：`docker compose -f docker-compose.prod.yml build frontend --no-cache`

**后端 502？**
- 看 healthcheck：`docker compose -f docker-compose.prod.yml ps backend` 状态是否 `healthy`
- 数据库目录权限：`./data` 必须可写
