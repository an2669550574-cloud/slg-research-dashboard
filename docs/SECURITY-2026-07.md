# 安全加固计划（2026-07-05 第一性原理审查）

> 一次「安全视角 + 第一性原理」的审查产出。**这是路线图 / 判案笔记，不是权威 runbook**——机制怎么工作看 [`ARCHITECTURE.md`](ARCHITECTURE.md)。已实现项在「状态跟踪」勾掉，其余是 backlog。
>
> 触发：用户提出两点担忧——①站点用免费 nip.io 域名（IP 型）；②AI 解析模块烧 LLM token，滥用则成本不可控。审查从这两点出发，追到共同的根因。
>
> **脱敏纪律**：本文件进 git，**不写任何** IP / 域名 / 容器名 / key 值 / webhook（遵守 [`CLAUDE.md`](../CLAUDE.md) 硬规则）——只写机制与路径，具体值一律「见根 `.env` / `backend/.env` / 运维私有渠道」。
>
> 方法：先摸清「谁能进来 → 进来能烧什么 → 现有防线拦不拦得住」的完整链路（代码 grounding），再按资产价值排序，最后按成本/收益排 P0→P2。

---

## 一、第一性原理：真实威胁模型

### 要保护的资产（按被滥用的代价排序）

| # | 资产 | 滥用后果 | 硬/软 |
|---|---|---|---|
| 1 | **公司 LLM 网关额度** | 烧钱（网关 $50/天/人，见 [[reference_taishi_gateway]]），且可能连累公司账号 | 用户头号担忧 |
| 2 | **Sensor Tower 配额** | 公司池 3000/月，`refresh` 类端点每次直接消耗；配额是全项目最硬约束 | 硬 |
| 3 | **调研数据 / 上传素材** | 竞品情报 + 买量素材泄露，商业尴尬 | 软 |
| 4 | **服务器本体** | 被打穿可横向移动 | 硬 |

### 攻击者怎么进来（这是审查的核心发现）

用户以为「免费域名」和「LLM 成本」是两个独立问题，**其实是同一个洞的两面**：

1. **站点可被发现，不靠「没人知道地址」**。当前用 nip.io 免费域名 + Let's Encrypt 证书。**签证书就会进 [Certificate Transparency 公开日志](https://crt.sh)**——任何人都能枚举到这个子域名，而 nip.io 域名本身就是 IP 的明文编码（`<ip>.nip.io`），域名即源站 IP。「地址隐蔽」这条防线**根本不成立**。
2. **发现之后，那把「钥匙」等于没锁**。API_KEY 在前端构建期被编进 JS bundle（[`Dockerfile:5`](../frontend/Dockerfile) `ARG VITE_API_KEY` → [`api.ts:89`](../frontend/src/lib/api.ts) 注入请求头）。任何人打开首页、F12 看 bundle 就能拿到 key。它防得住「没打开过页面的爬虫」，**防不住人**。
3. **拿到 key，AI 端点随便刷**。素材分析（[`materials.py:217` `/analyze`](../backend/app/routers/materials.py)）、创意迁移、标签分析都只有一道 `require_api_key`。key 既已泄露在 bundle，成本护栏就只剩下 `LLM_DAILY_BUDGET_USD`（见下）。

**一句话**：CT 日志让站点**可被发现** → bundle 里的 key 让发现者**能进来** → AI 端点让进来的人**能烧钱**。三者串成一条链，用户担心的两件事都挂在这条链上。免费域名的真正问题不在「免费」，在于 nip.io 把 DNS 钉死指向裸源站 IP，**没法把入口挪到 CDN / 反代后面**，源站永久暴露。

### 现有防线的真实强度（已代码核实）

| 防线 | 现状（文件锚点） | 真实强度 |
|---|---|---|
| 「地址没人知道」 | nip.io + Let's Encrypt | ❌ **不成立**：CT 日志可枚举，域名即 IP |
| API Key 鉴权 | 单把静态 key，[`security.py:24`](../backend/app/security.py) | ❌ **对能打开页面的人形同虚设**：key 编进 bundle |
| 鉴权兜底逻辑 | [`security.py:31`](../backend/app/security.py) `API_KEY` 未配 → 直接放行 | ⚠️ **fail-open**：为兼容本地开发。prod 已配 key 故当前不触发，但**配置漂移 / 换机漏配即全站裸奔**，无启动护栏 |
| 全局限流 | [`rate_limit.py:35`](../backend/app/rate_limit.py) `RATE_LIMIT_DEFAULT` 未设 → **整体禁用** | ⚠️ prod 是否设了**待核实**；只有 refresh 的 30s cooldown（[`rate_limit.py:66`](../backend/app/rate_limit.py)）常开 |
| LLM 花费护栏 | `LLM_DAILY_BUDGET_USD=20`（[`config.py:320`](../backend/app/config.py)），analyze / adapt / tag_analysis 共享 | ⚠️ **只有日封顶、无月封顶**：最坏 ~$600/月；且触顶是**静默 429**，无告警——被刷了你不会知道 |
| 素材文件流鉴权 | file_router **已有 HMAC 令牌**（[`materials.py:600`](../backend/app/routers/materials.py)、[`media.py:116` `sign`](../backend/app/services/media.py)，TTL 6h [`config.py:302`](../backend/app/config.py)） | ⚠️ **机制对、密钥复用错**：`_secret()` 直接返回 `API_KEY`（[`media.py:113`](../backend/app/services/media.py)）→ 签名密钥 = bundle 里那把泄露的 key → **拿到 bundle 就能自己伪造任意 `material_id` 的合法媒体 token**。HMAC 的真实强度被拉低到和 API_KEY 一样弱 |
| 管理员删除口令 | `ADMIN_DELETE_PASSWORD`（[`security.py:6`](../backend/app/security.py)）；同 fail-open 语义 | ⚠️ prod 是否配了**待核实**；未配则删标签无口令 |

### 审查纠错记录（智识诚实）

初次口头审查曾判「素材 file_router 无鉴权、material_id 可枚举拖走」——**错**。核实 [`main.py:76`](../backend/app/main.py) 注释 + [`materials.py:613`](../backend/app/routers/materials.py) 后确认：file_router 已有 HMAC 令牌鉴权。真正的问题不是「没锁」，而是「**锁和大门用了同一把会泄露的钥匙**」。故 P1-3 不是「实现签名 URL」（已实现），而是「给媒体签名拆出独立密钥」。这条纠错也说明 **P1-1（key 移出 bundle）不只是防 API 滥用，会连带修复媒体签名强度**。

---

## 二、优化方案（P0 → P2，每项独立可验收、可回滚）

> 排序原则：先用**最小改动**把门真正关上（P0，多为配置 / 网络层），再拆结构性隐患（P1，动应用代码），纵深留 P2。全部不与 ST 省配额约束冲突（安全防的是**入口**，ST 约束在**出口**，HK 服务器不动）。

### P0 — 先把门真正关上（不动或极小动应用代码）

#### P0-1 Caddy 层加一道真实认证 ⭐ 收益最大、当天可上
把信任边界从「应用层假钥匙」上移到**网络层**：未认证者连 JS bundle 都拿不到 → 「bundle 含 key」的整条链从源头被屏蔽。
- **做法**：首选 Caddy `basic_auth`（bcrypt 口令，只改 Caddyfile + `caddy reload`，**零应用改动**）。
- **关键未知**：**钉钉 webview 对 HTTP Basic Auth 弹窗的兼容性必须真机验证**（手机端可达性本就脆，见 [[project_dashboard_mobile_reachability]]）。若弹窗在 webview 里不可用 → 降级 **P0-1b**：Caddy 前置一个签名 cookie 门页（输入口令 → set 签名 cookie → 放行），或直接并入 P1-2 的 Cloudflare Access。
- **验收**：无凭据 `curl` 首页 / `/api/*` 均 401；有凭据正常；钉钉真机能过。
- **回滚**：Caddyfile 去掉 `basic_auth` 段 + reload，秒级。

#### P0-2 fail-open 改 fail-closed
- **做法**：`USE_MOCK_DATA=false`（即 prod 模式）且 `API_KEY` 未配置时，**启动即 refuse**（或至少 ERROR 级大声告警 + `/api/health` 标记降级）。位置 [`security.py:31`](../backend/app/security.py) 附近或启动钩子。同步给 `ADMIN_DELETE_PASSWORD` 加同款语义。
- **验收**：单测——非 mock 模式无 key 启动失败；mock 模式不受影响（本地开发照常）。
- **回滚**：纯代码，revert 即可。

#### P0-3 LLM 月度封顶 + 触顶告警（直击用户「成本不可控」担忧）
- **做法**：现有日预算旁加 `LLM_MONTHLY_BUDGET_USD`（水位**先查 prod 近 30 天真实日均**再定，别盲设），与 `today_cost_usd` 同源加一个 `month_cost_usd`。**日 / 月触顶时推钉钉维护者群一条告警**（现状是静默 429，被刷你根本不知道）。复用 release_alerts 的维护者 webhook（仅维护者群，别进领导群）。
- **验收**：模拟触顶 → 收到告警；正常用量不误报；月封顶生效。
- **回滚**：纯加配置 + 一处检查，可关开关降级。

#### P0-4 确认并配置全局限流
- **做法**：上 prod 查 `RATE_LIMIT_DEFAULT` 是否已设；未设则配（如 `120/minute`，slowapi 已就位，[`rate_limit.py`](../backend/app/rate_limit.py) 纯配置生效）。对 AI 端点可另加更严的 per-key 限制。
- **验收**：超阈值 429；正常人工使用不误伤。
- **回滚**：`.env` 清空 `RATE_LIMIT_DEFAULT` + 重启（注意 `RATE_LIMIT_*` 改后须 `compose --env-file .env up -d backend` 重读，restart 不生效）。

### P1 — 拆掉结构性隐患（动应用代码）

#### P1-1 API_KEY 移出前端 bundle
- **做法**：前端改**运行时输入**——首次 401 跳一次性口令页 → 存 localStorage → 后续请求带 `X-API-Key` 头。删 [`Dockerfile:5`](../frontend/Dockerfile) 的 `ARG VITE_API_KEY` 与 [`api.ts:89`](../frontend/src/lib/api.ts) 的构建期注入。
- **双重收益**：①bundle 不再含密钥（配合 P0-1，纵深防御）；②**换 key 不用重建前端镜像**（现状轮换一次 key 要重构建 + 重部署整个前端）。
- **注意**：此项与 P0-1（Caddy 认证）有部分功能重叠——**若 P0-1 已上且够用，P1-1 可降级为「纯粹让 key 可轮换」而非主防线**。二选一还是叠加，看 P0-1 落地后的实际体感。
- **验收**：构建产物 `rg` 不到 key；换 key 只改 `.env` + 重启 backend，前端无需重建。
- **回滚**：纯前端 + Dockerfile，revert。

#### P1-2 买真域名 + Cloudflare 代理（直击用户「免费域名」担忧，一笔钱治多病）
- **做法**：~$10/年买域名，DNS 走 Cloudflare 橙云代理回源到 HK。
- **四合一收益**：①**隐藏源站真实 IP**（CT 日志今后只见 Cloudflare，源站不再从证书泄露）；②免费 WAF / 边缘限流；③可选 **Cloudflare Access** 替代 P0-1 的认证层（更专业的门）；④**顺带改善国内手机可达性**——正是 [[project_dashboard_mobile_reachability]] 里记的「备选 C：CDN」，一笔钱同时治「域名不专业」和「钉钉 webview 白屏」两个老问题。
- **不冲突性**：ST 约束只管**服务器出口**（HK 出口访问 ST，不动）；Cloudflare 只挡**入口**。两者正交。
- **注意**：Cloudflare 免费版对**大文件 / 视频流**（素材播放）有回源与缓存策略需确认；`/api/materials/*/file` 的 Range 流式需实测不被 CF 破坏。
- **验收**：直连旧 nip.io 地址失效或仅 CF 回源可达；`crt.sh` 新证书不暴露源站 IP；钉钉真机 webview 实测可达。
- **回滚**：DNS 切回 nip.io 直连（保留旧 Caddy 配置一段时间）。

#### P1-3 媒体签名密钥与 API_KEY 解耦
- **做法**：新增 `MEDIA_SIGNING_SECRET`（独立随机值，**不进前端 bundle**），[`media.py:113` `_secret()`](../backend/app/services/media.py) 改读它，缺失时**才** fallback 到 `API_KEY`（平滑迁移，避免旧链接立即失效）。
- **依赖关系**：此项与 P1-1 是**同一根因的两面**——只要 API_KEY 还在 bundle 里且被复用为媒体密钥，媒体 token 就可伪造。若 P1-1 已让 key 离开 bundle，则媒体密钥即使仍复用 API_KEY 也不再从 bundle 泄露；但**拆出独立密钥仍是更干净的最小权限**（一把钥匙一个用途）。
- **验收**：伪造 token（用 bundle 里能拿到的旧 API_KEY 签）在配置新密钥后失效；正常页面图片 / 视频播放不受影响；TTL 仍 6h。
- **回滚**：`.env` 删 `MEDIA_SIGNING_SECRET` → 自动 fallback 回 API_KEY，旧行为恢复。

### P2 — 纵深（视 P0/P1 落地后剩余风险再定，别提前过度工程）

- **贵端点审计日志**：analyze / adapt / refresh 记录 key 指纹 + IP + 花费，异常事后有据可查。
- **静态单 key → 按人 key**：**仅当**领导群真开始高频用看板才值得（当前受众 = 维护者 + 领导小群，杀鸡用牛刀，缓）。
- **CSP / 安全响应头**：Caddy 层统一加 `Content-Security-Policy` / `X-Frame-Options` 等（注意别打断钉钉 webview iframe 嵌入）。

---

## 三、明确不做（防过度工程：受众小、攻击面已被 P0/P1 收敛）

| 不做 | 理由 |
|---|---|
| ❌ 完整用户体系 / OAuth / RBAC | 受众 = 维护者 + 领导小群，重登录体系是杀鸡用牛刀；P0-1 + P1-2 已把攻击面收敛到不值得 |
| ❌ 迁服务器 / 改 ST 出口 | ST 境外访问约束钉死 HK（见 [[project_server_region]]），安全改动只碰**入口**，绝不动出口 |
| ❌ 自建 fail2ban / WAF 全家桶 | Cloudflare 免费版 WAF 已覆盖；P0-1 + P1-2 之后攻击面不值得再堆 |
| ❌ 给 file_router「加鉴权」 | **已有** HMAC（本文件核实纠错）；要做的是 P1-3 换密钥，不是重造 |

---

## 四、执行前需上 prod 核实的清单（opus 会话第一步，先摸真实水位再动手）

1. **`RATE_LIMIT_DEFAULT` / `ADMIN_DELETE_PASSWORD` 是否已配置**（决定 P0-2 / P0-4 是「补配」还是「加护栏」）。
2. **近 30 天 LLM 真实日均花费**（定 P0-3 月度封顶水位，别盲设——查 `video_analyze.today_cost_usd` 的历史累计 / `Material.analysis_cost_usd` 列）。
3. **钉钉 webview 对 HTTP Basic Auth 弹窗的真机兼容性**（决定 P0-1 走 basic_auth 还是降级 P0-1b 门页 / CF Access）。
4. **当前 Caddyfile 结构**（在服务器 `/opt/slg-research-dashboard`，确认反代 / TLS 段落，见 [[reference_hk_deploy]]）。
5. **Cloudflare 免费版对 `/api/materials/*/file` Range 视频流的回源行为**（P1-2 上线前实测，别让素材播放坏掉）。

---

## 五、推荐执行顺序

```
P0-1（Caddy 认证，当天，收益最大）
  └→ P0-2 + P0-3 + P0-4（一个小 backend PR：fail-closed + LLM 月封顶告警 + 限流）
       └→ P1-2（买域名 + CF，有注册/DNS 等待期，尽早启动并行推进）
            └→ P1-1（key 移出 bundle，看 P0-1 体感决定是否降级为纯轮换）
                 └→ P1-3（媒体签名解耦，跟 P1-1 一起做最省事）
                      └→ P2（视剩余风险，多半只做审计日志）
```

**每步一个 feature 分支 → PR → squash**（[[feedback_git_pr_workflow]]）；部署前照例打 `rollback-<date>-<time>` tag + DB 兜底（见 checkpoint「回滚」段）。P0 多为配置 / Caddy 层，多数**零迁移纯代码或纯运维**，回滚成本极低。

---

## 六、状态跟踪（2026-07-05 执行后更新）

> ⚠️ **地面真相修正**：上 prod 核实 + 读码后，本计划初稿有多处「把已实现当待做」的误判，
> 已在下表订正。核实结论见 §七。

| 项 | 状态 | 备注 |
|---|---|---|
| P0-1 Caddy 认证 | ⬜ **待做（需你）** | 依赖钉钉 webview 真机验证（§七 #3）——我做不了 |
| P0-2 fail-closed | ✅ **已发现早已实现** | `main.py:24-31` 启动即对 prod 无 API_KEY 抛 `RuntimeError` 拒起；security.py 的 per-request skip 仅 mock 模式可达。**剩 ADMIN_DELETE 无启动闸**（低优先，在 API_KEY 墙后） |
| P0-3 LLM 成本护栏 | ✅ **本次落地**（`feat/llm-budget-hardening`） | 拆记账漏洞修复 + 月封顶 + 触顶告警 + 日预算调低。见 §八 |
| P0-4 全局限流 | ✅ **已发现早已配** | prod `RATE_LIMIT_DEFAULT=120/minute` + config `RATE_LIMIT_AI_SYNC=10/hour`。剩「AI 端点单独更严限流」但已被 P0-3 预算闸门覆盖，降级为可选 |
| P1-1 key 移出 bundle | ⬜ **待做（需决策）** | 与 P0-1 重叠；P0-1 上后可降级为纯轮换 |
| P1-2 真域名 + Cloudflare | ⬜ **待做（需你）** | 需你注册域名 + 配 DNS——我做不了 |
| P1-3 媒体签名解耦 | ✅ **本次落地**（`feat/media-signing-decouple`） | `MEDIA_SIGNING_SECRET` 独立密钥，见 §八 |
| P2 审计日志 / 按人 key / CSP | ⬜ 缓 | 视 P0/P1 后剩余风险；Caddy 已有 Security header 段，CSP 可增量 |

---

## 七、Prod 核实结论（2026-07-05，只读）

| 核实项 | 结果 |
|---|---|
| 全局限流 | ✅ `RATE_LIMIT_DEFAULT=120/minute` 已配（P0-4 已完成） |
| 删除口令 | ❌ `ADMIN_DELETE_PASSWORD` 未配（删标签无独立口令，但在 API_KEY 墙后，低危） |
| LLM 日均花费 | **三个可触发端点历史花费全为 $0**（素材2条/标签0/创意迁移0）→ 你的担忧是**前瞻性防滥用**，非「正在流血」 |
| 成本记账 | ⚠️ **发现真隐患**：成本分散 3 张表，闸门只 sum 了 materials 一张 → 「三端点共享日预算」在记账层是漏的（已修，见 §八） |
| fail-open | ✅ **误判订正**：`main.py:24-31` 早已 fail-closed（prod 无 key 拒起） |
| Caddyfile | 44 行、已有 Security header 段、`handle /api/*`→backend / `handle`→frontend，加 basic_auth 容易 |

---

## 八、本次已落地（2026-07-05，三分支未推）

- **`feat/llm-budget-hardening`**（2 commits，pytest 622✓）：①修记账漏洞——新增 `services/llm_budget.py` 汇总三表四列，`today_cost_usd` 委托之，7 处闸门零改动自动受益；②月封顶 `LLM_MONTHLY_BUDGET_USD=30` + 触顶推维护者群告警（当天每档一次、内存去重）+ 日预算 `20→5`（历史用量≈0，设紧更早触顶）；③conftest 清空 DINGTALK webhook env（顺带修「本地真 webhook 致 dingtalk 用例偶发误发」）。
- **`feat/media-signing-decouple`**（1 commit，pytest ✓）：`MEDIA_SIGNING_SECRET` 独立密钥，`media._secret()` 优先读它、未配回退 API_KEY。后端签 URL、前端不碰密钥 → **零前端改动**；旧链接 TTL 6h 内自动重签。
- **`docs/security-hardening-plan`**：本文件。
- **部署注意**：三分支均**零迁移纯代码**，回滚走纯代码。`LLM_*` / `MEDIA_SIGNING_SECRET` 改后须 `compose --env-file .env up -d backend` 重读。prod 若配 `MEDIA_SIGNING_SECRET`，开着页面的用户媒体 URL 会在下次列表刷新（≤6h）自动重签，无需干预。
- **待你决策/操作**：P0-1 Caddy basic_auth（需钉钉真机验证）、P1-2 买域名 + Cloudflare（需注册/DNS）、P1-1 前端 key 运行时化（看 P0-1 体感）。**P0-1 + P1-2 的可套用 diff + step-by-step + 真机验证 + 回滚见配套手册 [`SECURITY-CADDY-DOMAIN.md`](SECURITY-CADDY-DOMAIN.md)**。
