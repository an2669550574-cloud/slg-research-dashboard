FROM python:3.11-slim
# 容器内 stdout 默认块缓冲，日志可能延迟/丢失；强制无缓冲。
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# ffmpeg：素材视频关键帧抽取（services/video_analyze.py）。
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# 单 worker 是刻意的：scheduler 是同进程 AsyncIOScheduler（app/scheduler.py），
# 多 worker 会各起一个 scheduler → ST 同步 job 双跑（击穿配额）+ 领导群重复推送。
# 需要横向扩展就拆独立 scheduler 容器，不要在这里加 --workers。
# 运行时另有 flock 排它锁兜底（_acquire_scheduler_lock），但别依赖它绕过本约束。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
