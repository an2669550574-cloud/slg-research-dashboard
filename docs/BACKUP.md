# 备份与恢复

## 自动备份

```bash
chmod +x scripts/backup.sh scripts/restore.sh
./scripts/backup.sh
# → backups/slg_research-20260601T020000Z.db.gz
```

策略：
- 用 `sqlite3 .backup` 在线快照（无需停后端）
- gzip 压缩 + ISO 时间戳命名
- 默认保留最近 14 份；用 `KEEP=30 ./scripts/backup.sh` 调整

## 定时执行

加到 crontab。当前生产（HK）实际跑的是每天 **04:00 UTC**（= 12:00 CST，刻意排在 app 同步窗口之后），并通过 `COS_BACKUP_DIR` 触发离站镜像、日志落 `backups/backup.log`：

```cron
0 12 * * * cd /opt/slg-research-dashboard && COS_BACKUP_DIR=/lhcos-data/slg-backups /bin/bash scripts/backup.sh >> /opt/slg-research-dashboard/backups/backup.log 2>&1
```

> 时区取决于宿主机：上面 `0 12` 是机器本地时区为 CST 时的写法（对应 04:00 UTC）。机器若是 UTC，直接写 `0 4 * * *`。

或者用 systemd timer（推荐生产环境，错误能上 journald）：

```ini
# /etc/systemd/system/slg-backup.service
[Unit]
Description=SLG Research DB backup
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/slg-research-dashboard
ExecStart=/opt/slg-research-dashboard/scripts/backup.sh
```

```ini
# /etc/systemd/system/slg-backup.timer
[Unit]
Description=Daily SLG Research DB backup

[Timer]
OnCalendar=*-*-* 02:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now slg-backup.timer
```

## 异地备份

`backup.sh` 内置离站逻辑：设了环境变量 `COS_BACKUP_DIR` 就把本次 `.db.gz` 再拷一份到该目录，并增量镜像 `data/materials/`。脚本会先校验该目录确是**活的 fuse.cosfs 挂载**——cosfs 掉挂后挂载点退化成本地空目录，绝不能把"异地副本"静默写进本地磁盘，所以掉挂时只告警跳过、本地备份照常保留。

当前生产（HK）即用此法：腾讯云 Lighthouse 把 COS 桶以 cosfs 挂在 `/lhcos-data`，cron 行内联 `COS_BACKUP_DIR=/lhcos-data/slg-backups`（见上「定时执行」）。素材镜像**不删远端**（删本地不连带删异地，留恢复余量，故离站会留下本地已删的孤儿文件，属预期）。

> 备选方案：若不用 cosfs，也可单独加一条 rclone 同步——
> `0 3 * * * root rclone sync /opt/slg-research-dashboard/backups remote:slg-backups --max-age 30d`

## 恢复

```bash
./scripts/restore.sh backups/slg_research-20260601T020000Z.db.gz
```

脚本会：
1. 提示确认（输入 `yes`）
2. 停 `slg_backend` 容器
3. 把当前 DB 重命名为 `.pre-restore.<时间戳>` 留作回滚
4. 解压恢复 + `PRAGMA integrity_check`
5. 重启容器

## 验证恢复

恢复后立即跑：

```bash
curl https://<SLG_DOMAIN>/api/health
curl -H "X-API-Key: $API_KEY" https://<SLG_DOMAIN>/api/games/ | head
```

确认返回 200 且数据数量符合预期。

## 注意

- SQLite 单机部署的备份足够；生产长期运营建议迁移 Postgres，用 `pg_dump` 走逻辑备份 + WAL archiving 做 point-in-time 恢复
- `backups/` 目录要放到 `.gitignore`（已在内）

## 迁移到新服务器

`scripts/backup.sh` 产出的 `.db.gz` 也是迁移流程的输入。换机器时把它 + `.env` + `backend/.env` 一起 scp 到新主机，再走 [`docs/MIGRATION.md`](MIGRATION.md) 的步骤。
