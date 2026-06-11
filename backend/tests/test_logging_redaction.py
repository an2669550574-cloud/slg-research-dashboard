"""日志脱敏：ST auth_token 等密钥绝不落盘（docker logs / Sentry 同源）。

背景：httpx 在 INFO 级打印完整请求 URL，而 Sensor Tower 鉴权是 auth_token
查询参数 → API key 曾明文进生产容器日志。三道防线分别验证：
1. httpx/httpcore logger 压到 WARNING（源头）；
2. SensitiveQueryFilter 改写 record（兜底，含 %-args 路径）；
3. JsonFormatter 对异常链文本脱敏（httpx 异常 str 自带 URL）。
"""
import io
import json
import logging

from app.logging_setup import (
    JsonFormatter,
    SensitiveQueryFilter,
    configure_logging,
)

SECRET_URL = "https://api.sensortower.com/v1/ios/featured/impacts?app_id=123&auth_token=ST0_supersecret&start_date=2025-06-11"


def _make_capture_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    """独立 logger + StringIO handler，复刻 configure_logging 的格式/过滤栈。"""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SensitiveQueryFilter())
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def test_configure_logging_silences_httpx_request_lines():
    configure_logging("INFO")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_configure_logging_survives_debug_level():
    """LOG_LEVEL=DEBUG 排障时 httpx/httpcore 也不会把 URL 漏出来。"""
    configure_logging("DEBUG")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.DEBUG)
    configure_logging("INFO")  # 还原，免得影响别的用例


def test_filter_redacts_plain_message():
    logger, stream = _make_capture_logger("test.redact.plain")
    logger.info("HTTP Request: GET %s \"HTTP/1.1 200 OK\"" % SECRET_URL)
    line = json.loads(stream.getvalue())
    assert "ST0_supersecret" not in stream.getvalue()
    assert "auth_token=***" in line["msg"]
    # 非敏感参数原样保留，定位问题不受影响
    assert "app_id=123" in line["msg"]
    assert "start_date=2025-06-11" in line["msg"]


def test_filter_redacts_percent_args_path():
    """sensor_tower 的 logger.error("... %s", e) 走 args 合并路径。"""
    logger, stream = _make_capture_logger("test.redact.args")
    err = ValueError(f"Server error '500' for url '{SECRET_URL}'")
    logger.error("Sensor Tower fetch failed (%s), falling back: %s", "featured:ios:123", err)
    out = stream.getvalue()
    assert "ST0_supersecret" not in out
    assert "auth_token=***" in json.loads(out)["msg"]
    assert "featured:ios:123" in json.loads(out)["msg"]


def test_formatter_redacts_exception_chain():
    logger, stream = _make_capture_logger("test.redact.exc")
    try:
        raise RuntimeError(f"timeout while requesting {SECRET_URL}")
    except RuntimeError:
        logger.error("fetch blew up", exc_info=True)
    out = stream.getvalue()
    assert "ST0_supersecret" not in out
    assert "auth_token=***" in json.loads(out)["exc"]


def test_redaction_covers_other_sensitive_param_names():
    logger, stream = _make_capture_logger("test.redact.names")
    logger.info("retry GET /x?api_key=abc123&password=hunter2&page=3")
    line = json.loads(stream.getvalue())
    assert "abc123" not in line["msg"] and "hunter2" not in line["msg"]
    assert "api_key=***" in line["msg"] and "password=***" in line["msg"]
    assert "page=3" in line["msg"]


def test_clean_messages_pass_through_untouched():
    """无敏感参数的日志一个字符都不动（args 不被吞、JSON 结构不变）。"""
    logger, stream = _make_capture_logger("test.redact.clean")
    logger.info("synced %d apps for %s", 21, "publisher:lilith")
    line = json.loads(stream.getvalue())
    assert line["msg"] == "synced 21 apps for publisher:lilith"
    assert line["level"] == "INFO"
    assert "request_id" in line
