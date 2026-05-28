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
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal, utcnow_naive
from app.models.material import Material
from app.services import llm_gateway, media

logger = logging.getLogger(__name__)


_FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
_FFPROBE = shutil.which("ffprobe") or "ffprobe"


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
    """模型应吐纯 JSON；但偶尔会带 ```json``` 围栏或前缀解释——剥掉再 parse。
    解析失败抛 ValueError，由 analyze_material 转 status=failed。"""
    text = text.strip()
    if text.startswith("```"):
        # 剥 ```json ... ``` / ``` ... ```
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        # 去掉末尾可能残留的 ```
        text = text.strip().rstrip("`").strip()
    # 兜底：找第一个 { 到最后一个 } 之间
    if not text.startswith("{"):
        s = text.find("{")
        e = text.rfind("}")
        if s >= 0 and e > s:
            text = text[s : e + 1]
    return json.loads(text)


@dataclass
class AnalysisResult:
    brief: str
    tags: list[str]
    scenes: list[dict]
    hooks: list[dict]
    cost_usd: float
    model: str


async def _call_llm(messages: list[dict]) -> tuple[dict, float, str]:
    """返回 (parsed_json, cost_usd, model_id)。"""
    client = llm_gateway.get_client()
    model = settings.TAISHI_VISION_MODEL
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
    """当 UTC 日已分析成本合计。给端点护栏用。"""
    today_start = datetime(date.today().year, date.today().month, date.today().day)
    stmt = (
        select(func.coalesce(func.sum(Material.analysis_cost_usd), 0.0))
        .where(Material.analyzed_at >= today_start)
    )
    return float((await db.execute(stmt)).scalar_one() or 0.0)


async def analyze_material(material_id: int) -> None:
    """后台任务入口。整段独立开 session（BackgroundTasks 不传 request scope db）。

    状态机：调用方应已把 analysis_status 置 running，本函数负责走完
    done / failed 终态并写入字段。
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

            parsed, cost, model = await _call_llm(_build_messages(frames, m.title))
            m.analysis_status = "done"
            m.analysis_brief = str(parsed.get("brief", "")).strip() or None
            m.analysis_tags = _norm_tags(parsed.get("tags"))
            m.analysis_scenes = _norm_scenes(parsed.get("scenes"))
            m.analysis_hooks = _norm_hooks(parsed.get("hooks"))
            m.analyzed_at = utcnow_naive()
            m.analysis_model = model
            m.analysis_cost_usd = cost
            m.analysis_error = None
            await db.commit()
            logger.info("Analyzed material %s in %.2f$ via %s", material_id, cost, model)
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
