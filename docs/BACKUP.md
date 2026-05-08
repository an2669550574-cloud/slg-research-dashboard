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

加到 crontab（每天 02:00 UTC）：

```cron
0 2 * * * cd /opt/slg-research-dashboard && ./scripts/backup.sh >> /var/log/slg-backup.log 2>&1
```

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

`backup.sh` 落地后，建议把 `backups/` 同步到对象存储。例如 rclone：

```bash
# /etc/cron.d/slg-offsite
0 3 * * * root rclone sync /opt/slg-research-dashboard/backups remote:slg-backups --max-age 30d
```

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
