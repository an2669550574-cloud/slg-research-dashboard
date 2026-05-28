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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
