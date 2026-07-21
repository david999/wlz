"""告警构建测试:build_alerter 的分支与 NullAlerter 行为(不触网)。

- 未配置 token/chat_id -> NullAlerter,send 返回 False;
- 部分配置(缺一)也回退到 NullAlerter;
- 配置齐全 -> TelegramAlerter,仅校验类型与属性,不真正发送网络请求。
"""
from __future__ import annotations

import asyncio

from arb.monitoring.alerts import NullAlerter, TelegramAlerter, build_alerter


def test_build_alerter_none_is_null():
    a = build_alerter(None, None)
    assert isinstance(a, NullAlerter)
    assert a.enabled is False
    assert asyncio.run(a.send("hello")) is False


def test_build_alerter_partial_config_is_null():
    # 仅有 token 或仅有 chat_id 都不足以启用 Telegram
    assert isinstance(build_alerter("token", None), NullAlerter)
    assert isinstance(build_alerter(None, "chat"), NullAlerter)
    assert isinstance(build_alerter("", ""), NullAlerter)


def test_build_alerter_full_config_is_telegram():
    a = build_alerter("t", "c")
    assert isinstance(a, TelegramAlerter)
    assert a.enabled is True
    assert a.token == "t"
    assert a.chat_id == "c"
    # 刻意不调用 a.send(),避免任何真实网络请求
