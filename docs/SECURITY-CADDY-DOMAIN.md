# 安全加固执行手册：Caddy 认证 + 真域名/Cloudflare（P0-1 / P1-2）

> 配套 [`SECURITY-2026-07.md`](SECURITY-2026-07.md) 的**两项需人工执行**的加固。都动 prod 入口层，
> 有「配错即全站不可达」风险 → 每步给了 `caddy validate` 前置校验 + 回滚。**先在钉钉真机验证**
> 再依赖（手机端可达性本就脆，见 [[project_dashboard_mobile_reachability]]）。
>
> 脱敏：本手册不写真实域名/IP/口令（用 `your-domain.com` / `<HK_IP>` 占位）。Caddyfile 用 `{$SLG_*}`
> env 注入，仓库里本就无明文域名。

---

## Part A — Caddy basic_auth（P0-1，收益最大、当天可上）

**目标**：把认证上移到网络层。未认证者连前端 JS bundle 都拿不到 → 「bundle 含 API_KEY」的整条链从源头屏蔽。零应用改动，只改 `Caddyfile` + `docker-compose.prod.yml` + `.env`。

### ⚠️ 上线前必读
1. **钉钉 webview 对 HTTP Basic Auth 弹窗的兼容性是最大未知**——**必须先真机验证**。若钉钉内打不出输入框 / 输入后仍 401，走「降级方案」（见文末），别硬上。
2. Caddy env 未设 → basic_auth 块变空 → **可能锁死全站或启动失败**。故**先设 env、`caddy validate` 通过、再 reload**，并保留回滚 tag。
3. `/api/health` 探针豁免鉴权（容器间探活直连 `backend:8000` 不过 Caddy，此处豁免仅防外部 LB/监控打它被 401）。

### 步骤

**1) 生成 bcrypt 口令哈希**（在服务器，用 Caddy 容器自带命令）：
```bash
ssh hk-prod
cd /opt/slg-research-dashboard
docker exec slg_caddy caddy hash-password --plaintext '你想要的口令'
# 输出形如 $2a$14$xxxx....（bcrypt）。复制备用；口令本身别写进任何文件。
```

**2) 在根 `.env` 追加两个变量**（哈希里有 `$`，compose 读 `.env` 会把 `$` 当变量展开 → **每个 `$` 写成 `$$` 转义**）：
```dotenv
SLG_BASIC_AUTH_USER=admin
SLG_BASIC_AUTH_HASH=$$2a$$14$$xxxx....   # 把上一步哈希里每个 $ 改成 $$
```

**3) `Caddyfile` 改动**（在 `header { ... }` 块之后、`handle /api/*` 之前插入）：
```diff
 	header {
 		Strict-Transport-Security "max-age=31536000; includeSubDomains"
 		X-Content-Type-Options nosniff
 		Referrer-Policy strict-origin-when-cross-origin
 		X-Frame-Options DENY
 		-Server
 	}
 
+	# 网络层认证：未认证连前端 bundle 都拿不到（P0-1）。/api/health 豁免供探活。
+	@needs_auth not path /api/health
+	basic_auth @needs_auth {
+		{$SLG_BASIC_AUTH_USER} {$SLG_BASIC_AUTH_HASH}
+	}
+
 	# API → backend container（FastAPI 路由挂在 /api/*，所以保留前缀，用 handle 而非 handle_path）
 	handle /api/* {
```

**4) `docker-compose.prod.yml` 的 `caddy.environment` 追加**：
```diff
     environment:
       - SLG_DOMAIN=${SLG_DOMAIN:-localhost}
       - SLG_TLS_EMAIL=${SLG_TLS_EMAIL:-internal}
       - SLG_HTTPS_PORT=${SLG_HTTPS_PORT:-443}
+      - SLG_BASIC_AUTH_USER=${SLG_BASIC_AUTH_USER}
+      - SLG_BASIC_AUTH_HASH=${SLG_BASIC_AUTH_HASH}
```

**5) 校验 + 生效**（先校验，别直接重启）：
```bash
# 打回滚锚点
git pull --ff-only && git tag rollback-$(date +%Y%m%d-%H%M)
# 拉起（compose 会把新 env 注入 caddy 容器）
docker compose -f docker-compose.prod.yml --env-file .env up -d caddy
# 校验配置语法（basic_auth 展开是否正确）
docker exec slg_caddy caddy validate --config /etc/caddy/Caddyfile
# 若 validate 过但想热加载而非重启：
docker exec slg_caddy caddy reload --config /etc/caddy/Caddyfile
```

### 验证（缺一不可）
```bash
# 无凭据 → 401；带凭据 → 200；health 豁免 → 200
curl -sk -o /dev/null -w "%{http_code}\n" https://your-domain.com/            # 期望 401
curl -sk -u admin:'口令' -o /dev/null -w "%{http_code}\n" https://your-domain.com/   # 期望 200
curl -sk -o /dev/null -w "%{http_code}\n" https://your-domain.com/api/health  # 期望 200
```
- ✅ **钉钉真机**：手机钉钉点看板链接 → 应弹账号密码框 → 输入后正常进站 + 素材视频能播（`<video>` 请求会自动带上已认证的 Authorization 头）。**这一步过不了就别依赖 basic_auth**。

### 回滚
```bash
git checkout <rollback-tag> -- Caddyfile docker-compose.prod.yml
docker compose -f docker-compose.prod.yml --env-file .env up -d caddy
# 或热恢复：删 Caddyfile 里 basic_auth 块 + reload
```

### 降级方案（钉钉 webview 打不出 basic auth 框时）
- **P0-1b**：Caddy 前置一个「签名 cookie 门页」——`/login` 表单收口令 → set 一个 Caddy 签名 cookie → 其余路径校验该 cookie。webview 对表单+cookie 支持比 basic-auth 弹窗好。实现比 basic_auth 略重（需 `forward_auth` 或小中间件）。
- 或**并入 Part B 的 Cloudflare Access**（更专业的门，见下）。

---

## Part B — 真域名 + Cloudflare 代理（P1-2，一笔钱治多病）

**目标**：①隐藏源站 IP（不再从证书 CT 日志泄露）②免费 WAF/边缘限流 ③可选 Cloudflare Access 替代 Part A ④**顺带改善国内手机可达性**（[[project_dashboard_mobile_reachability]] 备选 C）。

**与 ST 约束不冲突**：ST 只关**服务器出口**（HK 出口访问 ST，不动）；Cloudflare 只挡**入口**，正交。

### ⚠️ 上线前必读
- **Cloudflare 免费版对大文件/视频流有限制**：单次请求体上传上限 **100MB**（免费版）；素材上传/播放若超需注意。`/api/materials/*/file` 的 **Range 视频流**必须实测不被 CF 破坏。
- 橙云代理后，源站看到的都是 CF 的 IP → 应用层 `X-Forwarded-For` 由 CF 给（Caddy 已透传）。
- ACME：走 CF 代理后，源站 Let's Encrypt HTTP-01 挑战会被 CF 拦。改用 **CF「完全（严格）」TLS 模式** + 源站证书（Caddy 仍可签，或用 CF Origin Cert）。

### 步骤
1. **注册域名**（~$10/年，任意注册商）。
2. **接入 Cloudflare**：加站点 → 改域名 NS 到 CF 指派的 NS。
3. **DNS 记录**：`A` 记录 `@`（或子域）→ `<HK_IP>`，**橙云开启**（Proxied）。
4. **TLS 模式**：SSL/TLS → 概览 → **Full (strict)**。源站保持 Caddy 自动 HTTPS（或换 CF Origin Certificate 装到 Caddy）。
5. **`.env` 改 `SLG_DOMAIN`** 为新域名 → `up -d`（Caddy 重签证书、CORS 自动跟随，见 compose `CORS_ORIGINS` 默认拼接）。
6. **（可选，最彻底）源站防绕过**：HK 防火墙只放行 [Cloudflare IP 段](https://www.cloudflare.com/ips/) 到 443 → 攻击者拿到 IP 也打不进，必须过 CF。
7. **（可选）Cloudflare Access**：Zero Trust → Access → 加应用保护看板，用邮箱 OTP/身份登录，替代 Part A 的 basic_auth（webview 兼容性通常更好）。

### 验证
```bash
# 解析应指向 CF（不是 HK 源站 IP）
dig +short your-domain.com          # 期望 CF 的 IP 段
# 站点经 CF 可达
curl -sI https://your-domain.com/ | grep -i "server: cloudflare"
# 素材视频 Range 流未被破坏（带 Part A 凭据）
curl -sk -u admin:'口令' -H "Range: bytes=0-1023" \
  "https://your-domain.com/api/materials/<id>/file?token=<签名>" -o /dev/null -w "%{http_code}\n"   # 期望 206
```
- ✅ 新证书（`crt.sh` 查 your-domain.com）只暴露 CF，不再暴露源站 IP。
- ✅ 钉钉真机 webview 实测可达 + 素材可播。

### 回滚
- CF DNS 关橙云（改「仅 DNS / DNS only」）→ 直连源站；或 `.env` 把 `SLG_DOMAIN` 改回旧 nip.io + `up -d`。保留旧 Caddy 配置一段时间。

---

## 建议顺序
Part A（当天，验证钉钉真机）→ 稳定后 Part B（域名注册有等待期，可先启动注册并行推进）。两者可叠加（CF 在前、basic_auth/Access 在后），也可用 CF Access 直接替代 Part A。
