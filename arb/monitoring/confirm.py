"""开仓人工确认(半自动逐单)相关抽象与实现。

设计目标:开仓前先告警并进入"待确认",人工在 Telegram 回复确认(或命令)后
才真正下单;超时未确认则作废该机会。平仓与 kill switch 不经过此闸门,保持全自动。

- ConfirmationSource: 可注入的确认源接口(Protocol),便于离线测试(见 FakeConfirmationSource)。
- TelegramConfirmationSource: 轮询 Telegram getUpdates,匹配 confirm/reject 关键字或回调。
- FakeConfirmationSource: 测试用,预设确认/拒绝结果,不做任何网络请求。
"""
from __future__ import annotations

import asyncio
import time
from typing import Protocol

from arb.monitoring.alerts import Alerter

# 关键字(小写);支持中英文与斜杠命令
CONFIRM_WORDS = ("confirm", "yes", "ok", "/confirm", "确认")
REJECT_WORDS = ("reject", "cancel", "no", "/reject", "作废", "取消")


class ConfirmationSource(Protocol):
    async def wait_for_confirmation(self, request_id: str, timeout_sec: float) -> bool:
        """在 timeout_sec 内等待人工确认。

        返回 True 表示确认放行;返回 False 表示明确拒绝或超时(均作废该机会)。
        """
        ...


def _extract_text(update: dict) -> str:
    """从 Telegram update 中提取文本:普通消息文本或内联按钮回调 data。"""
    msg = update.get("message") or update.get("channel_post") or {}
    if isinstance(msg, dict) and msg.get("text"):
        return str(msg["text"])
    cq = update.get("callback_query") or {}
    if isinstance(cq, dict) and cq.get("data"):
        return str(cq["data"])
    return ""


def decide(text: str) -> bool | None:
    """把一条文本解析为确认/拒绝/无关:True=确认,False=拒绝,None=不相关。

    先判拒绝再判确认(更保守);匹配整词或以关键字开头(便于携带 request_id)。
    """
    t = text.strip().lower()
    if not t:
        return None
    for w in REJECT_WORDS:
        if t == w or t.startswith(w + " "):
            return False
    for w in CONFIRM_WORDS:
        if t == w or t.startswith(w + " "):
            return True
    return None


class TelegramConfirmationSource:
    """轮询 Telegram 更新以获取人工确认。

    任何 alerter(实现了 fetch_updates)均可注入;未配置告警渠道时(NullAlerter)
    fetch_updates 返回空列表,等待必然超时 -> 作废机会(安全默认)。
    """

    def __init__(self, alerter: Alerter, poll_interval: float = 2.0) -> None:
        self.alerter = alerter
        self.poll_interval = poll_interval
        self._offset: int | None = None

    async def wait_for_confirmation(self, request_id: str, timeout_sec: float) -> bool:  # noqa: ARG002
        # 先推进 offset,丢弃本轮等待开始前积累的旧消息,避免历史 confirm 误批
        try:
            stale = await self.alerter.fetch_updates(self._offset)
            for upd in stale:
                uid = upd.get("update_id")
                if isinstance(uid, int):
                    self._offset = uid + 1
        except Exception:  # noqa: BLE001 刷新失败不影响后续轮询
            pass
        deadline = time.monotonic() + max(0.0, timeout_sec)
        while time.monotonic() < deadline:
            try:
                updates = await self.alerter.fetch_updates(self._offset)
            except Exception:  # noqa: BLE001 轮询失败不应中断闸门,按未确认处理
                updates = []
            for upd in updates:
                uid = upd.get("update_id")
                if isinstance(uid, int):
                    self._offset = uid + 1
                dec = decide(_extract_text(upd))
                if dec is not None:
                    return dec
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(self.poll_interval, remaining))
        return False


class FakeConfirmationSource:
    """离线测试用确认源:按预设返回,不做任何网络请求。

    - approve=True 立即确认;approve=False 表示拒绝/超时(闸门作废)。
    - delay: 返回前的等待秒数,用于模拟慢速确认。
    - 记录被请求过的 request_id,便于断言。
    """

    def __init__(self, approve: bool = True, delay: float = 0.0) -> None:
        self.approve = approve
        self.delay = delay
        self.requests: list[str] = []

    async def wait_for_confirmation(self, request_id: str, timeout_sec: float) -> bool:
        self.requests.append(request_id)
        if self.delay:
            await asyncio.sleep(min(self.delay, max(0.0, timeout_sec)))
        return self.approve


def build_confirmation_source(alerter: Alerter) -> ConfirmationSource:
    """默认确认源:基于注入的 alerter 轮询 Telegram 更新。"""
    return TelegramConfirmationSource(alerter)
