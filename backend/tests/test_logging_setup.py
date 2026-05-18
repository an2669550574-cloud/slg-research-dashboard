"""日志配置回归。

高危静默故障：alembic/env.py 的 fileConfig(disable_existing_loggers=True)
在 init_db() 进程内跑迁移时，会清掉应用的 JSON root handler **和** Sentry
LoggingIntegration handler，导致生产日志与告警双双静默。这里锁住：
① configure_logging 的形状/幂等；② env.py 的「进程内跳过 fileConfig」守卫。

conftest 每个 test 重载 app.* —— app.* import 放函数内。
"""
import logging
import pathlib


def _root_stream_json_handlers():
    from app.logging_setup import JsonFormatter
    root = logging.getLogger()
    return [h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonFormatter)]


def test_configure_logging_installs_single_json_handler():
    from app.logging_setup import configure_logging
    configure_logging("INFO")
    assert len(_root_stream_json_handlers()) == 1
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_idempotent():
    from app.logging_setup import configure_logging
    configure_logging("INFO")
    configure_logging("INFO")
    configure_logging("INFO")
    # 反复调用不叠加 handler（reload/lifespan 重配时不重复挂载）
    assert len(_root_stream_json_handlers()) == 1


def test_app_logger_emits_json(capsys):
    import json
    from app.logging_setup import configure_logging
    configure_logging("INFO")
    logging.getLogger("app.scheduler").info("probe-line", extra={"k": "v"})
    out = capsys.readouterr().out.strip().splitlines()
    rec = json.loads(out[-1])
    assert rec["level"] == "INFO"
    assert rec["logger"] == "app.scheduler"
    assert rec["msg"] == "probe-line"
    assert rec["k"] == "v"


def test_alembic_env_guards_fileconfig_for_in_process():
    """env.py 必须在 root 已有 handler（应用内运行）时跳过 fileConfig，
    否则会清掉应用 JSON 日志 + Sentry handler——本测试锁死这个回归。"""
    env_py = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "env.py"
    src = env_py.read_text(encoding="utf-8")
    # fileConfig 的触发条件必须 AND 上「root 无 handler」守卫（即仅 CLI 独立运行时配）
    assert "and not logging.getLogger().handlers:" in src, "缺少进程内跳过 fileConfig 的守卫"
    assert "\n    fileConfig(config.config_file_name)" in src, "fileConfig 调用形态变了，复核守卫"
