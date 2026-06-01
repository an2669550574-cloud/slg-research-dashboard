# 回滚

把生产从「当前版本」退回「上一个能跑的版本」。适用于自托管单机部署。

回滚分两种情况，先判断你属于哪种：

| 情况 | 判断 | 走哪节 |
|---|---|---|
| **只回代码** | 新版本只改了代码 / 前端 / 配置，没动数据库 schema | [一、纯代码回滚](#一纯代码回滚) |
| **代码 + 迁移** | 新版本带了 alembic 迁移（`backend/alembic/versions/` 多了文件），且新迁移**不向前兼容** | [二、带迁移的回滚](#二带迁移的回滚) |

> 拿不准动没动 schema？对比两个版本：
> `git diff <prev-tag> HEAD -- backend/alembic/versions/` —— 有输出就是动了迁移。

## 一、纯代码回滚

绝大多数回滚属于这种。后端启动时只会 `alembic upgrade head`（向前），不会自动 downgrade，所以**退代码不会动数据**。

```bash
cd /opt/slg-research-dashboard

# 1. 退到上一个 tag（或具体 commit）
git fetch --tags
git checkout <prev-tag>      # 例如 rollback-20260601-1143，或直接写 commit sha

# 2. 重建并重启
docker compose -f docker-compose.prod.yml --env-file .env up -d --build

# 3. 等健康检查（约 30 秒）
sleep 30 && docker compose -f docker-compose.prod.yml ps
```

回滚验证见末尾 [验证](#验证)。

> **打回滚 tag 的习惯**：每次部署前给当前 main 打个 tag（如 `rollback-<date>-<time>`），出事就能一行 checkout 回去，不用翻 git log 找 sha。

## 二、带迁移的回滚

只有当新版本的迁移**改了已有列 / 删了列 / 改了类型**（即旧代码连不上新 schema）时才需要 downgrade。纯新增表 / 新增可空列的迁移，旧代码忽略它即可，按[纯代码回滚](#一纯代码回滚)走就行。

**顺序很关键：先 downgrade 迁移，再退代码。** 反了的话旧代码会先尝试 `upgrade head` 把 schema 又顶回去。

```bash
cd /opt/slg-research-dashboard

# 0. 先备份！downgrade 可能丢列数据，无法逆转
./scripts/backup.sh
# → backups/slg_research-<时间戳>.db.gz

# 1. 在当前（新）容器里把迁移退到目标版本
#    <target-rev> = 旧代码对应的 alembic revision（旧迁移文件头部的 revision 号）
docker exec slg_backend alembic downgrade <target-rev>
#    或只退一步：docker exec slg_backend alembic downgrade -1
#    查看当前版本：docker exec slg_backend alembic current
#    查看历史：    docker exec slg_backend alembic history

# 2. 再退代码
git fetch --tags
git checkout <prev-tag>

# 3. 重建重启（旧代码启动时 upgrade head 会停在 <target-rev>，因为它就是旧代码的 head）
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
sleep 30 && docker compose -f docker-compose.prod.yml ps
```

### 如果 downgrade 出错 / 数据已损坏

别硬修，直接用备份恢复——更快也更稳：

```bash
# restore 会停 backend → 替换 db → 重启，全程约 10 秒
# 它还会把当前 DB 重命名为 .pre-restore.<时间戳> 留底
./scripts/restore.sh backups/slg_research-<回滚前那份>.db.gz
```

恢复用的备份必须是**新迁移跑之前**那一份，否则恢复回来的还是新 schema。

## 验证

回滚后确认服务正常（命令同 [DEPLOY.md 四、查看状态](DEPLOY.md)）：

```bash
# 健康检查，应返回 {"status":"ok"}
curl -sk https://<SLG_DOMAIN>/api/health

# 鉴权请求应返回数据（API_KEY 来自根目录 .env）
curl -sk -H "X-API-Key: $(grep ^API_KEY .env | cut -d= -f2)" \
  https://<SLG_DOMAIN>/api/games/ | head -c 200

# 当前迁移版本，应停在旧代码的 head
docker exec slg_backend alembic current

# 看错误日志有没有异常
docker logs slg_backend --tail 100 | jq -r 'select(.level=="ERROR")'
```

浏览器打开站点点几下核心页面，确认前端能正常请求（前端 bundle 里编进了 `VITE_API_KEY`，回滚旧前端会用旧 key，与后端同名变量一致即可）。

## 相关文档

- [DEPLOY.md](DEPLOY.md) —— 部署 / 更新
- [MIGRATION.md](MIGRATION.md) —— 换机迁移（含各阶段「故障回滚」表）
- [BACKUP.md](BACKUP.md) —— 备份 / 恢复脚本细节
