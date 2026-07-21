"""轻量健康检查(纯逻辑 + 可选协程),无外部框架强依赖。

设计原则(尽量只新增、零核心侵入):
- 纯函数与不可变数据结构(age_ms / is_fresh / evaluate_health / HealthReport)
  完全离线、无副作用,便于单测。
- HealthMonitor 为可选的【进程内】状态持有器:主程序可在行情/评估循环中打点
  (mark_market / mark_eval / set_connector),并用 report() 读取当前健康快照;
  打点全部为内存操作,不触碰网络/磁盘。是否挂载完全可选。
- measure_loop_latency_ms() 为协程,用于探测事件循环调度延迟(存活/阻塞)。
- 提供 CLI(python -m arb.monitoring.health)供 Docker HEALTHCHECK 使用:
  通过读取 SQLite 落库最近一次行情时间戳,跨进程判断监控进程是否仍在正常写入。
  CLI 复用同一套纯函数判定新鲜度,退出码 0=健康 / 1=不健康。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass, field

# 默认新鲜度阈值:超过该时长未更新即判定为陈旧/不健康。
DEFAULT_MARKET_MAX_AGE_MS = 10_000
DEFAULT_EVAL_MAX_AGE_MS = 10_000
DEFAULT_LOOP_LATENCY_MAX_MS = 1_000.0


# --------------------------------------------------------------------------- #
# 纯函数:时间新鲜度判定
# --------------------------------------------------------------------------- #
def now_ms() -> int:
    """当前毫秒时间戳(与系统其余模块口径一致)。"""
    return int(time.time() * 1000)


def age_ms(last_ts_ms: int | None, now: int) -> int | None:
    """距离上次事件的毫秒数;从未发生(None)返回 None。"""
    if last_ts_ms is None:
        return None
    return now - last_ts_ms


def is_fresh(last_ts_ms: int | None, now: int, max_age_ms: int) -> bool:
    """上次事件是否在 max_age_ms 之内。从未发生视为不新鲜。"""
    age = age_ms(last_ts_ms, now)
    if age is None:
        return False
    return 0 <= age <= max_age_ms


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class HealthReport:
    healthy: bool
    ts_ms: int
    checks: tuple[CheckResult, ...]

    def as_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "ts_ms": self.ts_ms,
            "checks": [c.as_dict() for c in self.checks],
        }


@dataclass
class HealthState:
    """可选的进程内健康状态快照(全部为内存字段)。"""

    last_market_ts_ms: int | None = None   # 最近一次收到行情盘口的时间
    last_eval_ts_ms: int | None = None      # 最近一次策略/价差评估的时间
    connector_connected: bool = False       # 连接器是否处于已连接状态
    loop_alive: bool = True                 # 事件循环是否存活(未阻塞)


# --------------------------------------------------------------------------- #
# 纯函数:综合评估
# --------------------------------------------------------------------------- #
def evaluate_health(
    state: HealthState,
    now: int,
    *,
    market_max_age_ms: int = DEFAULT_MARKET_MAX_AGE_MS,
    eval_max_age_ms: int = DEFAULT_EVAL_MAX_AGE_MS,
) -> HealthReport:
    """把一份状态快照评估为 HealthReport(无副作用)。"""
    checks: list[CheckResult] = []

    checks.append(
        CheckResult(
            "event_loop",
            state.loop_alive,
            "alive" if state.loop_alive else "blocked",
        )
    )

    market_age = age_ms(state.last_market_ts_ms, now)
    checks.append(
        CheckResult(
            "market_data",
            is_fresh(state.last_market_ts_ms, now, market_max_age_ms),
            f"age_ms={market_age}" if market_age is not None else "no_data",
        )
    )

    eval_age = age_ms(state.last_eval_ts_ms, now)
    checks.append(
        CheckResult(
            "evaluation",
            is_fresh(state.last_eval_ts_ms, now, eval_max_age_ms),
            f"age_ms={eval_age}" if eval_age is not None else "no_data",
        )
    )

    checks.append(
        CheckResult(
            "connector",
            state.connector_connected,
            "connected" if state.connector_connected else "disconnected",
        )
    )

    healthy = all(c.ok for c in checks)
    return HealthReport(healthy=healthy, ts_ms=now, checks=tuple(checks))


# --------------------------------------------------------------------------- #
# 可选:进程内监视器(供主程序可选打点)
# --------------------------------------------------------------------------- #
@dataclass
class HealthMonitor:
    """可选挂载到主程序的健康监视器;不挂载不影响任何核心逻辑。"""

    market_max_age_ms: int = DEFAULT_MARKET_MAX_AGE_MS
    eval_max_age_ms: int = DEFAULT_EVAL_MAX_AGE_MS
    state: HealthState = field(default_factory=HealthState)

    def mark_market(self, ts_ms: int | None = None) -> None:
        self.state.last_market_ts_ms = ts_ms if ts_ms is not None else now_ms()

    def mark_eval(self, ts_ms: int | None = None) -> None:
        self.state.last_eval_ts_ms = ts_ms if ts_ms is not None else now_ms()

    def set_connector(self, connected: bool) -> None:
        self.state.connector_connected = connected

    def set_loop_alive(self, alive: bool) -> None:
        self.state.loop_alive = alive

    def report(self, now: int | None = None) -> HealthReport:
        return evaluate_health(
            self.state,
            now if now is not None else now_ms(),
            market_max_age_ms=self.market_max_age_ms,
            eval_max_age_ms=self.eval_max_age_ms,
        )


# --------------------------------------------------------------------------- #
# 协程:事件循环存活探测
# --------------------------------------------------------------------------- #
async def measure_loop_latency_ms() -> float:
    """测量一次 await sleep(0) 的调度延迟(毫秒),越大说明事件循环越拥塞。"""
    start = time.perf_counter()
    await asyncio.sleep(0)
    return (time.perf_counter() - start) * 1000.0


async def probe_event_loop(max_latency_ms: float = DEFAULT_LOOP_LATENCY_MAX_MS) -> CheckResult:
    """探测事件循环是否响应及时。"""
    latency = await measure_loop_latency_ms()
    return CheckResult(
        "event_loop",
        latency <= max_latency_ms,
        f"latency_ms={latency:.3f}",
    )


# --------------------------------------------------------------------------- #
# 协程 + CLI:基于 SQLite 落库的跨进程健康探针(供 Docker HEALTHCHECK)
# --------------------------------------------------------------------------- #
async def check_db_freshness(
    db_path: str,
    now: int | None = None,
    max_age_ms: int = DEFAULT_MARKET_MAX_AGE_MS,
) -> HealthReport:
    """读取 spreads 表最近一次写入时间,判定监控进程是否仍在正常落库。

    任何异常(库不存在/表未建/查询失败)都归一化为不健康的 CheckResult,
    不抛出,便于健康探针稳定取得退出码。
    """
    ref = now if now is not None else now_ms()
    # 只读探针:目标文件缺失直接判定不健康,避免 sqlite connect 创建空文件
    # 而掩盖卷挂载失败等故障。
    if not os.path.exists(db_path):
        return HealthReport(
            healthy=False,
            ts_ms=ref,
            checks=(CheckResult("db", False, "file_not_found"),),
        )
    try:
        import aiosqlite  # 延迟导入:纯函数测试无需该依赖

        async with aiosqlite.connect(db_path) as db:
            cur = await db.execute("SELECT MAX(ts) FROM spreads")
            row = await cur.fetchone()
        last_ts = int(row[0]) if row and row[0] is not None else None
    except Exception as exc:  # noqa: BLE001 探针需稳定给出结论
        return HealthReport(
            healthy=False,
            ts_ms=ref,
            checks=(CheckResult("db", False, f"error={exc}"),),
        )

    fresh = is_fresh(last_ts, ref, max_age_ms)
    detail = f"age_ms={age_ms(last_ts, ref)}" if last_ts is not None else "no_rows"
    return HealthReport(
        healthy=fresh,
        ts_ms=ref,
        checks=(CheckResult("db", fresh, detail),),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="套利系统健康探针(读取落库新鲜度)")
    parser.add_argument("--db-path", default="arb.db", help="SQLite 数据库路径")
    parser.add_argument(
        "--max-age-ms",
        type=int,
        default=DEFAULT_MARKET_MAX_AGE_MS,
        help="最近一次落库允许的最大陈旧毫秒数",
    )
    args = parser.parse_args(argv)
    report = asyncio.run(check_db_freshness(args.db_path, max_age_ms=args.max_age_ms))
    print(json.dumps(report.as_dict(), ensure_ascii=False))
    return 0 if report.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
