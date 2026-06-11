"""标准库 logging 包装：JSON 格式 + 请求 ID 上下文 + 中间件。

不引入额外依赖（不用 structlog），通过 contextvars 跨协程传递 request id。
"""
import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# URL 查询参数里的密钥值（如 Sensor Tower 的 auth_token）不能落盘。
_SENSITIVE_QUERY_RE = re.compile(
    r"\b(auth_token|api_key|apikey|access_token|token|secret|password)=[^&\s\"']+",
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    return _SENSITIVE_QUERY_RE.sub(r"\1=***", text)


class SensitiveQueryFilter(logging.Filter):
    """兜底脱敏：日志行里出现敏感查询参数时把值替换成 ***。

    第一道防线是把 httpx/httpcore 压到 WARNING（它们在 INFO/DEBUG 级会打完整
    请求 URL）；这里兜的是异常文本带 URL 的情况——httpx 异常的 str 含完整
    URL，`logger.error("... %s", e)` 会把 auth_token 原样写进日志与 Sentry。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        redacted = _redact(msg)
        if redacted != msg:
            # 直接改写 record：后续 handler（含 Sentry 的）拿到的也是脱敏版
            record.msg = redacted
            record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            # 异常链文本同样可能携带含密钥的 URL（httpx 异常自带请求 URL）
            payload["exc"] = _redact(self.formatException(record.exc_info))
        # logger.info("...", extra={"app_id": "..."}) 中的 extra 字段会出现在 record.__dict__
        for k, v in record.__dict__.items():
            if k not in payload and k not in (
                "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "created", "msecs", "relativeCreated", "thread", "threadName",
                "processName", "process", "message", "asctime", "taskName",
            ):
                payload[k] = v
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """幂等：每次清掉 handler 重建，避免 reload 时重复挂载。"""
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SensitiveQueryFilter())
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn 自带的 access log 太吵，让 RequestLoggingMiddleware 接管
    logging.getLogger("uvicorn.access").disabled = True
    # httpx 在 INFO 级、httpcore 在 DEBUG 级会打印完整请求 URL——Sensor Tower
    # 鉴权走 auth_token 查询参数，等于把 API key 明文写进容器日志。压到
    # WARNING 在源头掐掉（LOG_LEVEL=DEBUG 排障时也不会漏）。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 request_id（或采用客户端传入的 X-Request-ID），写入响应头。

    每个请求结束时打一行 access log，包含 method/path/status/latency_ms。
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            logging.getLogger("app.request").exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "latency_ms": latency_ms,
                },
            )
            request_id_var.reset(token)
            raise

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logging.getLogger("app.request").info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "latency_ms": latency_ms,
            },
        )
        response.headers["X-Request-ID"] = rid
        request_id_var.reset(token)
        return response
