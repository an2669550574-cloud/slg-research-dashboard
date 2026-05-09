# 服务器迁移

把整个站点从一台主机搬到另一台（换云厂商、换 IP、换地区）。预估 30–60 分钟，期间只有 DNS 切换那一刻是真停机（如果有真域名）；用 nip.io 临时域名时无所谓——反正访问入口也跟着 IP 走。

## 必须搬的三件东西

| 项 | 在哪 | 怎么搬 |
|---|---|---|
| 代码 | GitHub | 新机器 `git clone` |
| **SQLite 主库** | `data/slg_research.db` | `scripts/backup.sh` 打包 → scp → `scripts/restore.sh` 恢复 |
| **凭据** | `.env` + `backend/.env` | scp（**不在 git 里，必须手动带**） |

不需要带的：Caddy 证书（LE 自动重签）、Docker 镜像（`--build` 重建）、Python venv / node_modules（都在镜像里）。

## 一、新服务器准备

```bash
# AlmaLinux / RHEL
dnf install -y git
dnf -y install dnf-plugins-core
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker

# Ubuntu / Debian
apt update && apt install -y git docker.io docker-compose-plugin
systemctl enable --now docker
```

如果云厂商有"安全组"，开放 `80/tcp`（LE 验证用，必须）+ 你计划的 HTTPS 端口（默认 443，本项目当前用 8443 是因为旧机器的 SUI 占了 443——新机器没 SUI 就用回 443）。

## 二、旧机器：备份数据

```bash
cd /opt/slg-research-dashboard

# 1. 在线快照 SQLite（无需停 backend）
./scripts/backup.sh
# → backups/slg_research-<时间戳>.db.gz

# 2. 凭据文件单独打包（注意：scripts/backup.sh 不动 .env）
tar czf env-bundle.tar.gz .env backend/.env
ls -la backups/*.db.gz env-bundle.tar.gz
```

## 三、传输到新机器

```bash
# 在旧机器上跑
scp backups/slg_research-*.db.gz NEW_HOST:/tmp/
scp env-bundle.tar.gz NEW_HOST:/tmp/
```

或者用 rsync（数据量大时更友好，断了能续）：

```bash
rsync -avz --progress backups/ env-bundle.tar.gz NEW_HOST:/tmp/
```

## 四、新机器：拉代码 + 恢复

```bash
git clone https://github.com/an2669550574-cloud/slg-research-dashboard.git /opt/slg-research-dashboard
cd /opt/slg-research-dashboard

# 1. 解凭据
tar xzf /tmp/env-bundle.tar.gz -C /opt/slg-research-dashboard/

# 2. 改 SLG_DOMAIN（如果换 IP 了 + 用 nip.io）
# 旧:  SLG_DOMAIN=slg.199.193.126.3.nip.io
# 新:  SLG_DOMAIN=slg.<新 IP>.nip.io
nano .env

# 3. 准备 data/ 目录（restore 脚本依赖它存在）
mkdir -p data

# 4. 启动一次让镜像构建好（容器会因为 DB 不存在自己建空 schema，没关系）
docker compose -f docker-compose.prod.yml up -d --build

# 5. 等容器健康（约 30 秒）
sleep 30 && docker compose -f docker-compose.prod.yml ps

# 6. 用旧库覆盖新库
mkdir -p backups
cp /tmp/slg_research-*.db.gz backups/
./scripts/restore.sh backups/slg_research-*.db.gz
# 脚本会停 backend → 替换 db → 重启 backend，全程约 10 秒
```

恢复完后跑健康检查：

```bash
# 应返回 {"status":"ok"}
curl -sk https://$(grep ^SLG_DOMAIN .env | cut -d= -f2):$(grep ^SLG_HTTPS_PORT .env | cut -d= -f2 || echo 443)/api/health

# 应返回带数据的列表（用旧机器同样的 API_KEY）
curl -sk -H "X-API-Key: $(grep ^API_KEY .env | cut -d= -f2)" \
  https://$(grep ^SLG_DOMAIN .env | cut -d= -f2):$(grep ^SLG_HTTPS_PORT .env | cut -d= -f2 || echo 443)/api/games/ | head -c 200

# 配额数字应跟旧机器对得上
curl -sk -H "X-API-Key: $(grep ^API_KEY .env | cut -d= -f2)" \
  https://$(grep ^SLG_DOMAIN .env | cut -d= -f2):$(grep ^SLG_HTTPS_PORT .env | cut -d= -f2 || echo 443)/api/quota/
```

## 五、DNS 切换（仅当用真域名）

新机器跑通后再切，**不要**在还没验证前就动 DNS。

- 用 nip.io：不需要切——域名本身就嵌了 IP，访问 `slg.<新 IP>.nip.io` 即可
- 用真域名：DNS 控制台把 A 记录从旧 IP 改到新 IP，TTL 设过 5 分钟内的值。LE 证书会在新机器自动重签

## 六、Caddy 证书

新机器**第一次启动**时 Caddy 会自动向 LE 申请新证书：
- 如果用真域名：DNS 切到新 IP 后 LE 才能验证（HTTP-01），所以**先切 DNS 再等约 1 分钟**就能拿到证书
- 如果用 nip.io：直接申请，无依赖

观察 Caddy 申请进度：

```bash
docker logs slg_caddy --tail 50 -f | grep -iE "obtain|cert|acme|error"
```

看到 `certificate obtained successfully` 就齐活。

## 七、旧机器下线

**至少观察 24 小时再彻底关停**，避免发现新机器有问题但已经回不去：

```bash
# 旧机器：先停容器，但保留数据 + .env，留作回滚
cd /opt/slg-research-dashboard
docker compose -f docker-compose.prod.yml down
# 再观察一天，浏览器访问新域名 + 检查配额数字稳定

# 一切正常 → 拷贝最终一份备份留存
./scripts/backup.sh
scp backups/slg_research-*.db.gz LOCAL_OR_S3

# 确认安全后才能销毁旧机器
```

## 故障回滚

不同阶段卡住的回滚动作：

| 卡在哪 | 回滚动作 |
|---|---|
| 新机器装 Docker / clone 失败 | 旧机器在跑、DNS 没动 → **0 影响**，慢慢排查 |
| `docker compose up -d --build` 失败 | 同上 |
| 数据 restore 失败 | restore 脚本会把当前 DB 重命名为 `.pre-restore.<时间戳>`；删掉新 DB、把 `.pre-restore.*` 改回 `slg_research.db`、重启 backend |
| DNS 已切但新机器有问题 | DNS A 记录改回旧 IP；旧容器还在跑就直接生效（约 5 分钟内） |
| 旧机器已经销毁、新机器有问题 | 用最后一份 `backups/*.db.gz` 在另一台机器照本文重装 |

## 常见坑

- **API_KEY 不能改**：前端构建时被烤进 bundle，浏览器侧已下发的版本只能跟旧 API_KEY 对话。换 API_KEY 等于让所有现有访问者断线重新拿前端
- **`data/` 目录权限**：scp 进来的文件可能 owner 是 root，docker 内 uvicorn 进程是非 root 用户。如果 backend 报 "unable to open database file"：`chown -R 1000:1000 data/`（看具体镜像里的 uid）
- **8443 vs 443**：迁到没有 SUI 的新机器时，可以把 `SLG_HTTPS_PORT` 删掉（用默认 443），但要同步删 / 改 `CORS_ORIGINS` 里的端口段。**不动也无所谓**，8443 一样能跑

## 升级建议（永久解决"换机即换域名"）

买个真域名（10 元 / 年），DNS 指向当前服务器：

1. 阿里云 / Cloudflare / Namecheap 注册域名
2. 后台加 A 记录：`slg.example.com → 当前 IP`
3. 改 `.env`：`SLG_DOMAIN=slg.example.com`
4. `docker compose -f docker-compose.prod.yml up -d`
5. Caddy 自动申请 LE 证书

以后再迁移就只是改 DNS A 记录，**0 停机**——浏览器侧无感知。
