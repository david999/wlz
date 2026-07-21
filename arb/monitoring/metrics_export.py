"""关键指标汇总为 Prometheus 文本或结构化日志(纯函数,零核心侵入)。

不改动核心业务逻辑:
- MetricsSnapshot:一次性只读快照(不可变),供渲染。
- MetricsCollector:可选的进程内计数器/量表持有器;主程序或交易引擎可在关键
  节点可选打点(如产生信号、开/平仓、更新权益、告警)。不打点则全部为初始值。
- render_prometheus / to_log_fields:纯函数渲染,离线可测,不引入任何外部框架。

Prometheus 文本遵循标准 exposition 格式(# HELP / # TYPE / 样本行),
可直接由 textfile collector 或简单 HTTP 端点暴露。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricsSnapshot:
    """一次采集的关键指标只读快照。"""

    signals: int = 0            # 累计生成的策略信号数
    opens: int = 0              # 累计开仓次数
    closes: int = 0             # 累计平仓次数
    open_positions: int = 0     # 当前在场仓位数
    equity: float = 0.0         # 当前权益(计价币)
    margin_ratio: float = 0.0   # 当前保证金占用率(0~1+)
    alerts: int = 0             # 累计告警数
    kill_switch: bool = False   # kill switch 是否已触发


# 指标注册表:(短名, 类型, 说明, 取值函数)。
_METRICS: tuple[tuple[str, str, str, str], ...] = (
    ("signals_total", "counter", "累计生成的策略信号数", "signals"),
    ("opens_total", "counter", "累计开仓次数", "opens"),
    ("closes_total", "counter", "累计平仓次数", "closes"),
    ("open_positions", "gauge", "当前在场仓位数", "open_positions"),
    ("equity", "gauge", "当前权益(计价币)", "equity"),
    ("margin_ratio", "gauge", "当前保证金占用率", "margin_ratio"),
    ("alerts_total", "counter", "累计告警数", "alerts"),
    ("kill_switch", "gauge", "kill switch 是否触发(1=是 0=否)", "kill_switch"),
)


def _fmt(value: object) -> str:
    """Prometheus 样本值格式化:bool->1/0,特殊浮点->+Inf/-Inf/NaN,其余用 repr。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        if value != value:            # NaN
            return "NaN"
        if value == float("inf"):
            return "+Inf"
        if value == float("-inf"):
            return "-Inf"
        return repr(value)
    return str(value)


def render_prometheus(snapshot: MetricsSnapshot, namespace: str = "arb") -> str:
    """把快照渲染为 Prometheus 文本 exposition 格式。"""
    lines: list[str] = []
    for short, mtype, help_, attr in _METRICS:
        fq = f"{namespace}_{short}"
        lines.append(f"# HELP {fq} {help_}")
        lines.append(f"# TYPE {fq} {mtype}")
        lines.append(f"{fq} {_fmt(getattr(snapshot, attr))}")
    return "\n".join(lines) + "\n"


def to_log_fields(snapshot: MetricsSnapshot) -> dict:
    """把快照转为结构化日志字段(可直接喂给 structlog)。"""
    return {
        "signals": snapshot.signals,
        "opens": snapshot.opens,
        "closes": snapshot.closes,
        "open_positions": snapshot.open_positions,
        "equity": snapshot.equity,
        "margin_ratio": snapshot.margin_ratio,
        "alerts": snapshot.alerts,
        "kill_switch": snapshot.kill_switch,
    }


class MetricsCollector:
    """可选的进程内指标收集器。全部为内存计数,线程外单事件循环内安全。"""

    def __init__(self) -> None:
        self.signals = 0
        self.opens = 0
        self.closes = 0
        self.open_positions = 0
        self.equity = 0.0
        self.margin_ratio = 0.0
        self.alerts = 0
        self.kill_switch = False

    def incr_signal(self, n: int = 1) -> None:
        self.signals += n

    def incr_open(self, n: int = 1) -> None:
        self.opens += n

    def incr_close(self, n: int = 1) -> None:
        self.closes += n

    def incr_alert(self, n: int = 1) -> None:
        self.alerts += n

    def set_open_positions(self, n: int) -> None:
        self.open_positions = n

    def set_equity(self, value: float) -> None:
        self.equity = value

    def set_margin_ratio(self, value: float) -> None:
        self.margin_ratio = value

    def set_kill_switch(self, value: bool) -> None:
        self.kill_switch = value

    def snapshot(self) -> MetricsSnapshot:
        return MetricsSnapshot(
            signals=self.signals,
            opens=self.opens,
            closes=self.closes,
            open_positions=self.open_positions,
            equity=self.equity,
            margin_ratio=self.margin_ratio,
            alerts=self.alerts,
            kill_switch=self.kill_switch,
        )

    def render_prometheus(self, namespace: str = "arb") -> str:
        return render_prometheus(self.snapshot(), namespace)

    def to_log_fields(self) -> dict:
        return to_log_fields(self.snapshot())
