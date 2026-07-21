"""告警发送:Telegram(可选)+ 空实现。

- 未配置 token/chat_id 时返回 NullAlerter,send 为无操作,便于本地/回测运行。
- 已配置时通过 Telegram Bot HTTP API 发送(用 stdlib urllib,放到线程池避免阻塞事件循环)。
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from typing import Protocol


class Alerter(Protocol):
    enabled: bool

    async def send(self, text: str) -> bool:
        ...


class NullAlerter:
    """未配置告警渠道时使用:仅返回 False,不做任何网络请求。"""

    enabled = False

    async def send(self, text: str) -> bool:  # noqa: ARG002
        return False


class TelegramAlerter:
    def __init__(self, token: str, chat_id: str, timeout: float = 10.0) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.enabled = True

    def _post(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": self.chat_id, "text": text}
        ).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return bool(body.get("ok"))
        except Exception:  # noqa: BLE001 告警失败不应影响主流程
            return False

    async def send(self, text: str) -> bool:
        return await asyncio.to_thread(self._post, text)


def build_alerter(token: str | None, chat_id: str | None) -> Alerter:
    if token and chat_id:
        return TelegramAlerter(token, chat_id)
    return NullAlerter()
