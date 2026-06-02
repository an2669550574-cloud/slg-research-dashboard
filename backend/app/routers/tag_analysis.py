"""AI 标签分析对话（P6）：对当前筛选范围的素材标签 + 已有 AI 分析内容做对话式
分析。一键报告 + 自由追问，会话落库可回查、可导出 md/csv。走公司 LLM 网关，
零 Sensor Tower 配额；与素材视频分析 / 创意迁移共享 LLM_DAILY_BUDGET_USD 日预算护栏。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.config import settings
from app.models.tag_analysis import TagAnalysisSession, TagAnalysisMessage
from app.schemas import (
    TagAnalysisRequest, TagAnalysisSessionOut, TagAnalysisMessageOut,
    TagAnalysisSessionListItem,
)
from app.services import tag_analysis, video_analyze

router = APIRouter(prefix="/api/tags/analysis", tags=["tag-analysis"])


async def _session_out(db: AsyncSession, session: TagAnalysisSession) -> TagAnalysisSessionOut:
    msgs = await tag_analysis.load_messages(db, session.id)
    out = TagAnalysisSessionOut.model_validate(session)
    out.messages = [TagAnalysisMessageOut.model_validate(m) for m in msgs]
    return out


@router.post("", response_model=TagAnalysisSessionOut)
async def run_analysis(req: TagAnalysisRequest, db: AsyncSession = Depends(get_db)):
    """跑一轮分析：新建会话（session_id 空）或在既有会话追问。

    护栏：
    - 范围内素材数 0 或 > 50 → 400（先缩小筛选）
    - 模型须在白名单（sonnet/opus）→ 400
    - 日 LLM 预算超 LLM_DAILY_BUDGET_USD → 429
    """
    spent = await video_analyze.today_cost_usd(db)
    if spent >= settings.LLM_DAILY_BUDGET_USD:
        raise HTTPException(
            status_code=429,
            detail=f"今日 LLM 预算已用尽（${spent:.2f} / ${settings.LLM_DAILY_BUDGET_USD:.2f}），明日重试",
        )
    try:
        session = await tag_analysis.run_turn(
            db,
            session_id=req.session_id,
            mode=req.mode,
            message=req.message,
            model=req.model,
            app_id=req.app_id,
            material_type=req.material_type,
            tag_options=req.tag_options,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM 网关调用失败：{e}")
    return await _session_out(db, session)


@router.get("", response_model=list[TagAnalysisSessionListItem])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """会话列表（按更新时间倒序，带消息条数，轻量不含正文）。"""
    counts = dict((await db.execute(
        select(TagAnalysisMessage.session_id, func.count())
        .group_by(TagAnalysisMessage.session_id)
    )).all())
    rows = (await db.execute(
        select(TagAnalysisSession).order_by(TagAnalysisSession.updated_at.desc(), TagAnalysisSession.id.desc())
    )).scalars().all()
    out = []
    for s in rows:
        item = TagAnalysisSessionListItem.model_validate(s)
        item.message_count = counts.get(s.id, 0)
        out.append(item)
    return out


@router.get("/{session_id}", response_model=TagAnalysisSessionOut)
async def get_session(session_id: int, db: AsyncSession = Depends(get_db)):
    try:
        session = await tag_analysis.get_session(db, session_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return await _session_out(db, session)


@router.delete("/{session_id}")
async def delete_session(session_id: int, db: AsyncSession = Depends(get_db)):
    try:
        await tag_analysis.delete_session(db, session_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "已删除", "id": session_id}


@router.get("/{session_id}/export.md")
async def export_md(session_id: int, db: AsyncSession = Depends(get_db)):
    """整段会话导出 markdown。文件名用 ASCII（规避 CJK Content-Disposition 500）。"""
    try:
        text = await tag_analysis.export_markdown(db, session_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(
        content=text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="tag-analysis-{session_id}.md"'},
    )


@router.get("/{session_id}/export.csv")
async def export_csv(session_id: int, db: AsyncSession = Depends(get_db)):
    """标签分布数据导出 CSV（按会话范围实时重算）。"""
    try:
        text = await tag_analysis.export_csv(db, session_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="tag-analysis-{session_id}.csv"'},
    )
