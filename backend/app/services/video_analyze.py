"""素材视频 LLM 分析：ffmpeg 抽关键帧 + 太石网关视觉模型解读。

调用路径（由 routers/materials.py 的 BackgroundTasks 触发）：

    analyze_material(material_id) →
        extract_frames(file_path)                      # ffmpeg N 帧
        → build messages（system + 用户消息含 N 张帧图）
        → AsyncOpenAI.chat.completions.create(...)     # 走太石网关
        → parse_response → 写回 DB

设计取舍：
- **均匀采样**而非场景切换检测。SLG 广告 15~60 秒、节奏紧凑；ffmpeg scenedetect
  对短视频反而漏首尾；均匀采样 10 帧已经够 sonnet 拼出完整剧情。
- **一次性多图调用**而非分帧多次调用。Anthropic vision 单次能吃 ≤20 图，
  10 帧远在限内；多次调用 = 多份重复的 system prompt = 浪费成本和 token。
- **强制 JSON 输出**。不依赖模型自觉，prompt 里写明 schema 并要求 only JSON；
  解析失败兜底成 status=failed 而非崩溃。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.material import Material
from app.services import llm_gateway, media

logger = logging.getLogger(__name__)


_FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
_FFPROBE = shutil.which("ffprobe") or "ffprobe"

# 抽帧 / 联系单 文件落盘的子目录（在 MEDIA_ROOT 下）。
# 文件名走 deterministic：frame_NN.jpg / contact_sheet.jpg；DB 不存路径。
ANALYSIS_SUBDIR = "analysis"


def analysis_dir(material_id: int) -> Path:
    """data/materials/analysis/{material_id}/ 绝对路径。caller 自行 mkdir。"""
    root = Path(settings.MEDIA_ROOT)
    return root / ANALYSIS_SUBDIR / str(material_id)


def frame_path(material_id: int, n: int) -> Path:
    return analysis_dir(material_id) / f"frame_{n:02d}.jpg"


def contact_sheet_path(material_id: int) -> Path:
    return analysis_dir(material_id) / "contact_sheet.jpg"


@dataclass
class FrameSample:
    timestamp_sec: float
    jpeg_bytes: bytes


# ────────────────────────────────────────────────────────────────────────
# ffmpeg / ffprobe
# ────────────────────────────────────────────────────────────────────────

def _probe_duration(path: Path) -> float:
    """秒。失败则返回 0；调用方据此决定走错误路径还是用兜底单帧。"""
    try:
        out = subprocess.run(
            [_FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip() or 0)
    except Exception as e:
        logger.warning("ffprobe duration failed for %s: %s", path, e)
        return 0.0


def _extract_one_frame(path: Path, ts_sec: float, max_dim: int) -> Optional[bytes]:
    """-ss 放 -i 前：快速 seek（关键帧定位，毫秒级，不解码到 ts）。
    缩放滤镜保证最长边 ≤ max_dim，保持宽高比。"""
    cmd = [
        _FFMPEG, "-loglevel", "error",
        "-ss", f"{ts_sec:.3f}", "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale='min({max_dim},iw)':'min({max_dim},ih)':"
               f"force_original_aspect_ratio=decrease",
        "-q:v", "5",  # JPEG 质量 (2 最好, 31 最差) — 5 是兼顾体积与清晰度
        "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30)
        if out.returncode != 0 or not out.stdout:
            logger.warning("ffmpeg frame extract failed ts=%.2f rc=%d stderr=%s",
                           ts_sec, out.returncode, out.stderr[:200].decode("utf-8", "ignore"))
            return None
        return out.stdout
    except Exception as e:
        logger.warning("ffmpeg subprocess error: %s", e)
        return None


def build_contact_sheet(frames: list[FrameSample], dest: Path,
                        cols: int = 5, cell_max: int = 360,
                        gap: int = 4, bg: tuple = (10, 14, 22)) -> None:
    """把 N 张帧拼成 cols 列网格 JPG。

    单元格统一缩到 cell_max 最长边，保持宽高比；放在固定大小（max(w),max(h)）
    的格子里居中——避免不同纵横比的帧让网格忽宽忽窄。bg=深色（与"情报终端"
    设计系统 bg-base 接近），不喧宾夺主。
    """
    if not frames:
        return
    # 预解码 + 缩放
    thumbs = []
    for f in frames:
        im = Image.open(io.BytesIO(f.jpeg_bytes)).convert("RGB")
        im.thumbnail((cell_max, cell_max), Image.LANCZOS)
        thumbs.append(im)
    cell_w = max(im.width for im in thumbs)
    cell_h = max(im.height for im in thumbs)
    rows = (len(thumbs) + cols - 1) // cols
    sheet_w = cols * cell_w + (cols + 1) * gap
    sheet_h = rows * cell_h + (rows + 1) * gap
    sheet = Image.new("RGB", (sheet_w, sheet_h), bg)
    for i, im in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = gap + c * (cell_w + gap) + (cell_w - im.width) // 2
        y = gap + r * (cell_h + gap) + (cell_h - im.height) // 2
        sheet.paste(im, (x, y))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest, format="JPEG", quality=82, optimize=True)


def save_frames_to_disk(frames: list[FrameSample], material_id: int) -> None:
    """落盘每帧到 frame_NN.jpg。前端按帧索引访问，DB 不存路径只存 ts。"""
    d = analysis_dir(material_id)
    d.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        with open(frame_path(material_id, i), "wb") as fp:
            fp.write(f.jpeg_bytes)


def clear_analysis_artifacts(material_id: int) -> None:
    """重新分析时清理旧 artifacts，避免遗留旧帧。"""
    d = analysis_dir(material_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def extract_frames(file_path: Path, n_frames: int, max_dim: int) -> list[FrameSample]:
    """从 [0.5%, 99.5%] 之间均匀采样 n_frames 张。

    避开首末 0.5%：前 0~50ms 经常是黑场/淡入，末尾几十毫秒可能 fade-out
    或片源被截断；采到这种帧浪费一个 token 槽。
    """
    duration = _probe_duration(file_path)
    if duration <= 0:
        # 兜底：抽 1 帧（位置 0.5s）。短片或 ffprobe 失败时让模型至少能看一眼。
        frame = _extract_one_frame(file_path, 0.5, max_dim)
        return [FrameSample(0.5, frame)] if frame else []

    n = max(1, n_frames)
    if n == 1:
        timestamps = [duration / 2]
    else:
        start = duration * 0.005
        end = duration * 0.995
        step = (end - start) / (n - 1)
        timestamps = [start + i * step for i in range(n)]

    frames: list[FrameSample] = []
    for ts in timestamps:
        b = _extract_one_frame(file_path, ts, max_dim)
        if b:
            frames.append(FrameSample(round(ts, 2), b))
    return frames


# ────────────────────────────────────────────────────────────────────────
# Prompt 构造 + LLM 调用
# ────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是一名资深手机游戏买量素材分析师，专长 SLG（策略类）品类。
我会给你一条广告素材的 N 张等间距采样关键帧，并附每帧的时间戳（秒）。
你的任务是输出**严格的 JSON**，字段如下（中文回答；不要 markdown、不要任何解释文字）：

{
  "brief": "一段中文总结（80~150 字）：素材主题、创意手法、目标人群、情感基调、整体节奏",
  "tags": ["3~8 个中文短标签，覆盖：题材/玩法/创意手法/卖点/受众，例：末日生存、城建、UGC 风、女性向"],
  "scenes": [
    {"ts": 0.5, "description": "<该帧在叙事里的作用：开场吸睛/冲突铺垫/产品展示/CTA 等，附场景内容>"}
    // 每张关键帧一条；按时间升序
  ],
  "hooks": [
    {"ts": 1.2, "kind": "卸负", "note": "<具体说明：表演弱者被欺压，制造情绪卸负>"},
    {"ts": 12.0, "kind": "反转", "note": "..."},
    {"ts": 24.5, "kind": "CTA", "note": "..."}
    // 0~6 条；只在能识别到明显买量钩子时给。kind 取值：卸负/反转/CTA/价值主张/情绪高潮/对比/痛点
  ]
}

输出原则：
- 只输出上述 JSON 对象本体，前后不带 ```、不带任何前后缀文字。
- 必须是可被 JSON.parse 解析的合法 JSON：字符串值内若要引用素材文案，一律用中文引号「」，
  **绝不用英文双引号 "**（会截断字符串、破坏 JSON）；万不得已用英文引号务必转义为 \\"。
- ts 用秒（浮点，精度到 0.1s 即可），与给你的帧时间戳一致或就近。
- 不确定的字段宁可省略也不要编造；scenes 必须覆盖给到的每一帧。
- 标签简短（≤ 6 字），不要把整段描述塞进 tag。
"""


def _build_messages(frames: list[FrameSample], material_title: str) -> list[dict]:
    """构造 OpenAI 兼容的多模态消息。

    Anthropic vision 通过太石 OpenAI 兼容入口走时，图片必须用 OpenAI 风格的
    `image_url` + `data:image/jpeg;base64,...`。网关在内部转换为 Anthropic
    格式（这是手册"OpenAI completions 格式"的承诺）。
    """
    user_content: list[dict] = [
        {"type": "text",
         "text": f"素材标题：{material_title or '(未命名)'}\n以下为 {len(frames)} 张关键帧（按时间升序）："},
    ]
    for f in frames:
        user_content.append({"type": "text", "text": f"[ts={f.timestamp_sec}s]"})
        b64 = base64.b64encode(f.jpeg_bytes).decode("ascii")
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    user_content.append({
        "type": "text",
        "text": "请按 system 指令输出 JSON。",
    })
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _parse_response(text: str) -> dict:
    """模型应吐纯 JSON；但偶尔带 ```json``` 围栏 / 前缀解释 / 字符串内未转义引号——
    委托 llm_gateway.parse_llm_json 稳健解析（含游离引号兜底修复）。
    解析失败抛 json.JSONDecodeError（ValueError 子类），由 analyze_material 转 failed。"""
    return llm_gateway.parse_llm_json(text)


@dataclass
class AnalysisResult:
    brief: str
    tags: list[str]
    scenes: list[dict]
    hooks: list[dict]
    cost_usd: float
    model: str


# 素材分析允许选的模型白名单（与创意迁移 creative_adapt.ALLOWED_ADAPT_MODELS 对齐：
# 都带视觉/强归纳的 Claude；默认 sonnet 省钱，用户可在前端升 opus 拿更细的解读）。
# 端点校验：传白名单外的值一律 400，防误调贵模型/不存在模型。None → settings.TAISHI_VISION_MODEL。
ALLOWED_ANALYZE_MODELS = ("claude-sonnet-4.5", "claude-opus-4.7")


async def _call_llm(messages: list[dict], model: Optional[str] = None) -> tuple[dict, float, str]:
    """返回 (parsed_json, cost_usd, model_id)。model=None → settings.TAISHI_VISION_MODEL。"""
    client = llm_gateway.get_client()
    model = model or settings.TAISHI_VISION_MODEL
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=2000,
        temperature=0.3,
    )
    text = resp.choices[0].message.content or ""
    usage = llm_gateway.usage_to_dict(getattr(resp, "usage", None))
    cost = llm_gateway.estimate_cost(model, usage).total_usd
    parsed = _parse_response(text)
    return parsed, cost, model


# ────────────────────────────────────────────────────────────────────────
# 预算护栏 + DB 写入
# ────────────────────────────────────────────────────────────────────────

async def today_cost_usd(db: AsyncSession) -> float:
    """当日 LLM 已花费合计（素材分析 + 创意迁移 + 标签分析三端点汇总）。

    历史坑：本函数曾只统计 materials.analysis_cost_usd，创意迁移 / 标签分析的花费
    记在各自表里、不进闸门——「三端点共享日预算」在记账层是漏的（只算三分之一）。
    现委托 llm_budget 汇总三表修正。保留本函数名作为 6 处 router 预算闸门的统一入口
    （测试亦 monkeypatch 此名，勿让 router 改调 llm_budget 否则 patch 失效）。
    """
    from app.services import llm_budget

    return await llm_budget.day_cost_usd(db)


# 触顶告警去重：当天(day)/当月(month)每档最多推一次维护者群。单进程内存态——
# backend 单容器足够；重启丢失顶多多发一条，无害。
_budget_alert_marks: set[str] = set()


async def _alert_budget_hit(scope: str, spent: float, cap: float) -> None:
    """LLM 预算触顶 → 推维护者群，当天(day)/当月(month)每档一次。

    未配 webhook / 发送失败均不阻断闸门（告警是旁路，429 该照常抛）。
    """
    today = date.today()
    key = (
        f"month:{today.year}-{today.month:02d}" if scope == "month"
        else f"day:{today.isoformat()}"
    )
    if key in _budget_alert_marks:
        return
    label = "本月" if scope == "month" else "今日"
    title = f"⚠️ LLM {label}预算触顶"
    text = (
        f"### ⚠️ LLM {label}预算触顶\n\n"
        f"{label}已花费 **${spent:.2f}** / 上限 ${cap:.2f}，后续 AI 端点（素材分析 / "
        f"创意迁移 / 标签分析）请求将被拒（429）。\n\n"
        f"> 若非预期用量，排查是否有异常调用在刷这些端点。"
    )
    try:
        from app.services import dingtalk

        if await dingtalk.send_markdown(title, text, target="maintainer"):
            _budget_alert_marks.add(key)  # 仅发送成功才去重，失败下轮可重试
    except Exception:
        logger.warning("LLM 预算触顶告警发送失败", exc_info=True)


async def assert_llm_budget(db: AsyncSession) -> None:
    """AI 端点统一预算闸门：日 / 月任一超限 → 触顶告警（当天每档一次）+ 429。

    7 处可触发 LLM 的端点（素材分析 / 创意迁移×3 / 产品画像 / 标签分析）共用此闸门。
    daily 走 today_cost_usd（三端点汇总，且保留为测试 monkeypatch 入口）；monthly 走
    llm_budget.month_cost_usd。LLM_MONTHLY_BUDGET_USD=0 则不启用月度门。
    """
    from fastapi import HTTPException

    from app.services import llm_budget

    month_cap = settings.LLM_MONTHLY_BUDGET_USD
    if month_cap:
        month = await llm_budget.month_cost_usd(db)
        if month >= month_cap:
            await _alert_budget_hit("month", month, month_cap)
            raise HTTPException(
                status_code=429,
                detail=f"本月 LLM 预算已用尽（${month:.2f} / ${month_cap:.2f}），下月重试",
            )
    day = await today_cost_usd(db)
    day_cap = settings.LLM_DAILY_BUDGET_USD
    if day >= day_cap:
        await _alert_budget_hit("day", day, day_cap)
        raise HTTPException(
            status_code=429,
            detail=f"今日 LLM 预算已用尽（${day:.2f} / ${day_cap:.2f}），明日重试",
        )


async def analyze_material(material_id: int, model: Optional[str] = None) -> None:
    """后台任务入口。整段独立开 session（BackgroundTasks 不传 request scope db）。

    状态机：调用方应已把 analysis_status 置 running，本函数负责走完
    done / failed 终态并写入字段。model=None → settings.TAISHI_VISION_MODEL
    （白名单校验在端点侧做，后台任务信任传入值）。
    """
    async with AsyncSessionLocal() as db:
        m = (await db.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
        if not m:
            logger.warning("analyze_material: material %s not found", material_id)
            return
        if m.source != "upload" or not m.file_path:
            await _mark_failed(db, m, "仅上传素材可分析（外链不支持）")
            return
        path = media.resolve(m.file_path)
        if not path.is_file():
            await _mark_failed(db, m, "源文件丢失")
            return

        try:
            frames = await asyncio.to_thread(
                extract_frames, path,
                settings.MATERIAL_ANALYZE_FRAMES,
                settings.MATERIAL_ANALYZE_FRAME_MAX_DIM,
            )
            if not frames:
                await _mark_failed(db, m, "ffmpeg 抽帧失败（视频可能损坏）")
                return

            # 持久化帧 + 联系单：用户后续在抽屉里看图用。重新分析时先清旧的，
            # 避免帧数变化（比如改了 MATERIAL_ANALYZE_FRAMES）导致 frame_10
            # 残留。LLM 调用在前，IO 在后；调用失败就不浪费磁盘。
            parsed, cost, used_model = await _call_llm(_build_messages(frames, m.title), model)
            await asyncio.to_thread(clear_analysis_artifacts, material_id)
            await asyncio.to_thread(save_frames_to_disk, frames, material_id)
            await asyncio.to_thread(
                build_contact_sheet, frames, contact_sheet_path(material_id),
            )

            m.analysis_status = "done"
            m.analysis_brief = str(parsed.get("brief", "")).strip() or None
            m.analysis_tags = _norm_tags(parsed.get("tags"))
            m.analysis_scenes = _norm_scenes(parsed.get("scenes"))
            m.analysis_hooks = _norm_hooks(parsed.get("hooks"))
            # 帧元信息：[{ts}, ...]。文件名走 deterministic（frame_NN.jpg）
            # 由 frame_path(id, i) 给出，i 是数组下标。
            m.analysis_frames = [{"ts": f.timestamp_sec} for f in frames]
            m.analysis_has_contact_sheet = True
            m.analyzed_at = utcnow_naive()
            m.analysis_model = used_model
            m.analysis_cost_usd = cost
            m.analysis_error = None
            await db.commit()
            logger.info("Analyzed material %s in %.2f$ via %s", material_id, cost, used_model)
        except json.JSONDecodeError as e:
            await _mark_failed(db, m, f"模型返回非 JSON：{str(e)[:200]}")
        except Exception as e:
            logger.exception("analyze_material %s failed", material_id)
            await _mark_failed(db, m, f"分析失败：{type(e).__name__}: {str(e)[:200]}")


async def _mark_failed(db: AsyncSession, m: Material, reason: str) -> None:
    m.analysis_status = "failed"
    m.analysis_error = reason[:500]
    m.analyzed_at = utcnow_naive()
    await db.commit()


def _norm_tags(raw) -> Optional[list[str]]:
    if not isinstance(raw, list):
        return None
    return [str(t).strip() for t in raw if isinstance(t, (str, int)) and str(t).strip()][:8]


def _norm_scenes(raw) -> Optional[list[dict]]:
    if not isinstance(raw, list):
        return None
    out = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        try:
            ts = float(it.get("ts", 0))
        except (TypeError, ValueError):
            continue
        desc = str(it.get("description", "")).strip()
        if desc:
            out.append({"ts": round(ts, 2), "description": desc})
    return out or None


def _norm_hooks(raw) -> Optional[list[dict]]:
    if not isinstance(raw, list):
        return None
    out = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        try:
            ts = float(it.get("ts", 0))
        except (TypeError, ValueError):
            continue
        kind = str(it.get("kind", "")).strip()
        note = str(it.get("note", "")).strip()
        if kind and note:
            out.append({"ts": round(ts, 2), "kind": kind, "note": note})
    return out or None
