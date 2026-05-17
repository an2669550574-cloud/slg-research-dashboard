"""slowapi 回归守卫：headers_enabled=True 时 sync_history 必须暴露名为
`response` 的 Response 参数，否则注入 X-RateLimit-* 时 500（AI/数据逻辑
根本没机会跑）。测试环境 RATE_LIMIT_DEFAULT 未设 → limiter 关闭，复现不了
真实 500，故用签名守卫精确锁这个坑。

同步端点的功能行为（事实性事件 / 保留手动 / 空来源）见 test_history.py。
"""
import inspect


def test_sync_history_has_response_param_for_slowapi():
    from fastapi import Response
    from app.routers.history import sync_history
    sig = inspect.signature(sync_history)
    assert "response" in sig.parameters, "缺 response 参数 → slowapi 注入头时会 500"
    assert sig.parameters["response"].annotation is Response
