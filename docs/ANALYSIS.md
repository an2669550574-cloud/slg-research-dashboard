# 素材 AI 分析 · Runbook

> 买量素材（上传的短视频）交给**太石 LLM 网关的视觉模型**解读，产出中文「总览 / 标签 / 分镜 / 买量钩子」，供竞品创意调研。**全链零 Sensor Tower 配额**（ffmpeg 抽帧 + 太石网关，不碰 ST）。
>
> 本文是运维/接手 runbook：怎么工作 · 怎么用 · 怎么排障 · 配置项。改相关代码前先读这里 + `docs/ARCHITECTURE.md`。
>
> 代码主入口：[`backend/app/services/video_analyze.py`](../backend/app/services/video_analyze.py) · 端点 [`backend/app/routers/materials.py`](../backend/app/routers/materials.py) · 网关封装 [`backend/app/services/llm_gateway.py`](../backend/app/services/llm_gateway.py)。

---

## 1. 三个共用网关的 AI 能力

素材侧有三个独立能力，**都走太石网关（`llm_gateway.get_client`，OpenAI 兼容）+ 共享 `parse_llm_json` 容错解析 + 共享日/月预算护栏**，但用途不同：

| 能力 | service | 输入 → 输出 | 触发 |
|---|---|---|---|
| **素材分析**（本文主体） | `video_analyze.py` | 一支上传视频 → 总览/标签/分镜/钩子 | `POST /materials/{id}/analyze` |
| **创意迁移**（sibling，§7） | `creative_adapt.py` | 参考素材 + 我方产品 → 创意方向 → 分镜脚本 | `POST /materials/{id}/adapt/*` |
| **产品画像**（sibling，§7） | `product_analyze.py` | 我方产品的素材 → 反推产品 brief | `POST /products/{id}/analyze` |

> **网关铁律**：必须走公司统一 LLM 网关（`relay.tuyoo.com/v1`），**不直连 Anthropic/OpenAI**。key/地址在 `backend/.env`（模板见 `backend/.env.example`），不进 git。

---

## 2. 素材分析端到端流程

后台任务 `analyze_material(material_id, model=None)`（`video_analyze.py:375`）。端点收到请求后立刻把 `analysis_status='running'` 返回，真正的分析在 `BackgroundTasks` 里异步跑，前端轮询 `GET /materials/{id}` 拉终态。

```
POST /materials/{id}/analyze  ── 护栏（§4）→ status=running → 返回
        │  (BackgroundTasks)
        ▼
extract_frames(file)          ── ffmpeg 均匀抽 MATERIAL_ANALYZE_FRAMES(10) 帧，
                                 每帧降采样最长边 ≤ MATERIAL_ANALYZE_FRAME_MAX_DIM(1280)
        ▼
_build_messages(frames)       ── system prompt + N 张帧图(base64 data URI，一次多图)
        ▼
_call_llm(messages, model)    ── 太石网关 chat.completions；model=None → TAISHI_VISION_MODEL(sonnet)
        ▼
parse_llm_json(text)          ── 容错解析出 JSON（§6 坑一）
        ▼
写盘 + 写 DB                   ── clear_artifacts → save_frames → build_contact_sheet → 写字段 → status=done
```

**顺序取舍（勿改）**：LLM 调用**在前**、落盘**在后**（`analyze_material` 里 `_call_llm` 先跑，成功了才 `save_frames_to_disk`/`build_contact_sheet`）——调用失败就不浪费磁盘。重新分析时 `clear_analysis_artifacts` 先清旧帧，避免帧数变化残留。

**抽帧设计**：均匀采样而非场景切换检测（SLG 广告 15~60s、节奏紧凑，scenedetect 对短视频反漏首尾）；一次性多图单次调用（Claude vision 单次 ≤20 图，10 帧远在限内，避免多次重复 system prompt 浪费 token）。

**模型输出 JSON schema**（system prompt 里约定，`video_analyze.py:197`）：

| 字段 | 内容 |
|---|---|
| `brief` | 一段中文总结（80~150 字）：主题/创意手法/目标人群/情感基调/节奏 |
| `tags` | 3~8 个中文短标签（题材/玩法/创意手法/卖点/受众） |
| `scenes` | 每帧一条 `{ts, description}`（该帧在叙事里的作用） |
| `hooks` | 0~6 条 `{ts, kind, note}`；`kind` ∈ 卸负/反转/CTA/价值主张/情绪高潮/对比/痛点 |

写回 `Material` 表字段（`backend/app/models/material.py:28-45`）：`analysis_status`（pending/running/done/failed）· `analysis_brief` · `analysis_tags` · `analysis_scenes` · `analysis_hooks` · `analysis_frames`（`[{ts}]`）· `analysis_has_contact_sheet` · `analyzed_at` · `analysis_model`（实际用的模型）· `analysis_cost_usd` · `analysis_error`。

---

## 3. 触发与操作

### 端点（`materials.py`）

| 端点 | 作用 |
|---|---|
| `POST /materials/{id}/analyze` | 触发分析。可选 body `{"model": "..."}`（§5）；空 body = 默认模型。立即返回 `running` |
| `POST /materials/{id}/adopt-tags` | 把 `analysis_tags` 去重合并进人工 `tags`（`analysis_tags` 原样保留） |
| `GET /materials/{id}` | 轮询分析状态（前端运行中每 3s 拉一次直到终态） |
| `GET /materials/{id}/frame/{n}` · `/contact-sheet` · `/file` | 取抽帧/联系单/原视频。**走 HMAC 短时令牌**（`MEDIA_SIGNING_SECRET`，TTL 6h），`<video>/<img>` src 带不了 API-Key 头故用令牌 |

### 前端

`MaterialAnalysisContent.tsx`（抽屉 `MaterialAnalysisDrawer` + 详情页 `MaterialAnalysisDetail` 共用），三条入口：素材库卡片「AI 分析」按钮 / `/materials/:id/analysis` 详情页 / `AI 解析`(`/materials/analysis`) 汇总页。分析面板四态：**pending**（尚未分析）/ **running**（正在分析，20~60s）/ **done**（总览+标签+关键帧+分镜+钩子+创意迁移区块）/ **failed**（错误原因 + 重试）。pending/done/failed 三态都有**模型下拉**（§5）。

---

## 4. 触发护栏（`analyze_material_endpoint`，materials.py:222）

按序：
1. 素材存在 → 否则 **404**
2. `source=='upload'` 且有 `file_path` → 外链素材 **400**（拿不到原文件抽帧）
3. `material_type=='video'` → 非视频 **400**
4. 未在分析中（`status!='running'`）→ 否则 **409**（防重入；done/failed 都可重分析）
5. `model` 若指定须在白名单 → 否则 **400**（放在资源检查之后，故不存在的素材优先 404）
6. `assert_llm_budget` → 日/月预算超限 **429**（§5）

---

## 5. 模型选择 · 成本 · 预算护栏

### 选模型（#203/#204）

素材分析可选模型，白名单 `ALLOWED_ANALYZE_MODELS`（`video_analyze.py`，与创意迁移 `ALLOWED_ADAPT_MODELS` / 标签分析 `ALLOWED_MODELS` 对齐；2026-07-14 升级 4.6/4.8，旧 4.5/4.7 留白名单兼容旧前端 bundle）：

| 模型 | 单次成本(粗估) | 用途 |
|---|---|---|
| `claude-sonnet-4.6`（默认，`TAISHI_VISION_MODEL`） | ~$0.04（4.5 实测 $0.0438 同档价） | 日常，省钱 |
| `claude-opus-4.8` | ~$0.07（≈ sonnet × 5/3） | 想要更细解读时升 |

端点接可选 body `{model}`，白名单校验（坏 model → 400）；不传 → `None` → 回落 `TAISHI_VISION_MODEL`（空 body 向后兼容）。前端下拉默认 sonnet。

**temperature 兼容（2026-07-14 prod 实锤）**：新一代 Claude（opus-4.5+/sonnet-5+）经网关 Bedrock 后端对 `temperature` 参数硬报 400「temperature is deprecated for this model」——所有 LLM 调用已收口到 `llm_gateway.chat_completion()`，撞到该 400 自动剥掉 temperature 重试一次；新增调用**别直接拿 `get_client()` 调 `chat.completions.create`**。

### 预算护栏（`assert_llm_budget`，#194）

**7 个 AI 端点共享**日/月预算（素材分析 / 创意迁移×3 / 产品画像 / 标签分析）：

- 日 `LLM_DAILY_BUDGET_USD=5` · 月 `LLM_MONTHLY_BUDGET_USD=30`（0=不启用月度门）。超限 **429** + 触顶告警推维护者群（当天/当月每档去重一次）。
- **记账修全 3 表（#194 坑）**：`today_cost_usd` 委托 `services/llm_budget.day_cost_usd` 汇总 **materials.analysis_cost_usd + creative_adaptations.cost_usd + .script_cost_usd + tag_analysis_messages.cost_usd**（3 表 4 列）——早期只 sum materials 一张，另两端点花费漏记、闸门只算 1/3。改 router 别绕过 `today_cost_usd`（测试 monkeypatch 此名）。
- **产品画像不回写**：它调 `assert_llm_budget` 受前置拦截（预算耗尽当天拒新请求），但**自身花费不写任何预算表、不计入聚合**（低频小额，故意）。
- 太石账号本身 $50/天/人硬上限，本软护栏须低于此。正常人工用量极低（prod 历史 ≈ $0），设紧是为异常刷量早触顶。

---

## 6. 失败态排障 · 关键坑

失败素材在面板显示「分析失败」+ `analysis_error`。对照排查：

| `analysis_error` / 症状 | 成因 | 处置 |
|---|---|---|
| `模型返回非 JSON：Expecting ...` | 模型输出连兜底修复都解析不了（罕见；常见的内嵌引号已被 `parse_llm_json` 修好） | 点「重试」（LLM 非确定，重跑多半过）；反复失败看 `docker logs` 里 `LLM JSON parse failed` 的 `FIRST 500` 原文 |
| `源文件丢失` | `MEDIA_ROOT` 下原视频不在（迁移/恢复漏带 `data/materials/`） | 从备份补回素材文件，见 `docs/BACKUP.md` / `docs/MIGRATION.md` |
| `ffmpeg 抽帧失败（视频可能损坏）` | 视频损坏 / 非视频 / 容器无 ffmpeg | 校验源文件可播；确认容器内 `ffmpeg`/`ffprobe` 在 PATH |
| `仅上传素材可分析（外链不支持）` | 素材是外链（无本地文件抽帧） | 下载后作为 upload 素材重传 |
| `分析失败：{类型}: ...` | 网关超时 / 网络 / 其它异常 | 看 `docker logs slg_backend` 具体异常；网关侧确认 `TAISHI_API_KEY` 有效、未触账号硬上限 |
| 端点直接 **429** | 日/月 LLM 预算触顶（§5） | 等次日/次月，或临时调高 `LLM_DAILY_BUDGET_USD` 后 `compose up -d backend` 重读；先排查是否异常刷量 |
| 端点 **409** | 同素材正在分析中 | 等当前分析走完终态再触发 |

**坑一 · 模型返回非 JSON（#202）**：视觉模型在中文 `brief` 里内嵌**未转义的英文双引号**（引用素材文案，如「素材用"庇护所"对比…」），裸 `json.loads` 在字符串中途断裂 → 「Expecting ',' delimiter」→ 素材标 failed。解析统一走 **`llm_gateway.parse_llm_json`**：`json.loads(strict=False)` 容忍裸控制字符 + 首次失败后 `_escape_stray_quotes` 兜底修复游离引号再试，仍失败抛回原始 `JSONDecodeError`（caller 兜成 failed）。合法 JSON 永不进修复分支。**三端点（video/creative/product）共用此函数——别在别处再手写裸 `json.loads`**。

**坑二 · CJK 验证铁律**：素材/上传/文件流相关改动，验证**必须用中文测试数据**（纯 ASCII 夹具漏过中文名导致 `Content-Disposition` 500 的真 bug；坏 JSON 复现样本也是中文摘要内嵌引号）。项目根 `CLAUDE.md` 校验节列为硬规则。

**坑三 · React hooks 顺序**：分析面板（抽屉/弹层）所有 hooks 必须在任何 early return **之前**，否则 prop 切换 hook 数变化崩页，tsc/vitest 抓不到。项目根 `CLAUDE.md` 校验节列为硬规则。

**坑四 · 落盘不进 DB**：抽帧/联系单落 `MEDIA_ROOT/analysis/{id}/`（`frame_NN.jpg` / `contact_sheet.jpg`，文件名 deterministic，**DB 不存路径**）。换机迁移/恢复必须带上整个 `data/materials/`，见 `docs/MIGRATION.md` + `docs/BACKUP.md`。

---

## 7. Sibling 能力（同网关 + 同 parse_llm_json）

### 创意迁移（`creative_adapt.py`）
参考买量素材 + 我方产品 → **两段式**：先出 3-5 个创意方向（`generate_directions`）→ **人先挑方向** → 再基于选中方向写分镜脚本（`generate_script`）。另有跨素材统一方向（`generate_unified_directions`，勾 2-15 支 done 素材归纳共性）。端点 `POST /materials/{id}/adapt/directions` · `/adapt/script` · `/adapt/unified-directions`（`?estimate_only=true` 干跑只估成本）。方法论核心：两段式人筛（方向→脚本）+ 借结构不抄壳 + 五条硬约束（禁宏大叙事开场 / 禁 CG 宣传片 / 一镜一事 / 0-1.5s 单动作 / 反馈单镜头），调创意 prompt 前先对齐。

### 产品画像（`product_analyze.py`）
我方产品的素材（视频抽帧 + 商店描述）→ AI 反推产品 `brief/theme/gameplay/selling_points/audience/differentiation`，回填「我方产品」表单。端点 `POST /products/{id}/analyze`。复用 `video_analyze.extract_frames` 抽帧；**不写 materials 表、花费不回写不计入预算聚合**（低频小额），但仍受 `assert_llm_budget` 前置拦截。

---

## 8. 配置项（`backend/.env`，见 `backend/.env.example`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `TAISHI_API_KEY` | — | 太石网关 key；空则所有 AI 端点 no-op 降级 |
| `TAISHI_BASE_URL` | `https://relay.tuyoo.com/v1` | 网关地址（OpenAI 兼容） |
| `TAISHI_VISION_MODEL` | `claude-sonnet-4.6` | 素材/产品分析默认视觉模型 |
| `TAISHI_TIMEOUT_SECONDS` | `120` | 单次调用超时（多图 + 长 prompt 宽点） |
| `LLM_DAILY_BUDGET_USD` | `5` | 7 端点共享日预算，超限 429 |
| `LLM_MONTHLY_BUDGET_USD` | `30` | 月预算二道保险，0=关 |
| `MATERIAL_ANALYZE_FRAMES` | `10` | 单视频抽多少帧（8~12 是 sonnet 甜区） |
| `MATERIAL_ANALYZE_FRAME_MAX_DIM` | `1280` | 每帧降采样最长边（Claude vision ≤1568 才不被自动缩） |
| `MEDIA_ROOT` | `./data/materials` | 素材 + 抽帧/联系单落盘根 |
| `MEDIA_SIGNING_SECRET` | — | 帧/文件流 HMAC 令牌密钥（与 API_KEY 解耦，#195）；未设回退 API_KEY |

> 改 `LLM_*` / `TAISHI_*` / `MATERIAL_ANALYZE_*` / `MEDIA_SIGNING_SECRET` 后须 `docker compose --env-file .env up -d backend` 重读（restart 不生效）。

---

## 9. 代码地图

| 关注点 | 位置 |
|---|---|
| 抽帧 / 联系单 / 落盘 | `video_analyze.py`：`extract_frames` · `build_contact_sheet` · `save_frames_to_disk` · `clear_analysis_artifacts` |
| system prompt / 消息构造 | `video_analyze.py:197` `_SYSTEM_PROMPT` · `_build_messages` |
| LLM 调用 / 解析 / 归一化 | `_call_llm` · `_parse_response`→`llm_gateway.parse_llm_json` · `_norm_tags/_norm_scenes/_norm_hooks` |
| 预算护栏 / 记账 | `assert_llm_budget` · `today_cost_usd`→`services/llm_budget.py` · `_alert_budget_hit` |
| 端点 | `routers/materials.py`（analyze/adopt-tags/adapt/file/frame/contact-sheet）· `routers/product.py`（产品画像） |
| 前端 | `components/MaterialAnalysisContent.tsx`（含 `ModelSelect` + 创意迁移 `AdaptBlock`）· `MaterialAnalysisDrawer.tsx` · `pages/MaterialAnalysisDetail.tsx` · `pages/MaterialAnalysis.tsx` |
| 数据模型 | `models/material.py`（`analysis_*` 字段）· `CreativeAdaptation`（迁移历史存档） |
