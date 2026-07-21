"""标定产出:best_params.yaml + 样本内外对比摘要(含风险提示)。

- best_params.yaml:每个 pair 一组 window/entry_z/exit_z/threshold_bps;
- 摘要:并排展示样本内 vs 样本外指标,便于识别过拟合(样本外显著劣化即警示);
- 风险提示:回测收益 ≠ 实盘,须 paper → testnet live → 极小额实盘逐级放大。

本模块不改动 settings;新增 env 参数仅在 SUGGESTED_ENV_VARS 中登记,
交由后续集成迭代落地到 arb/config/settings.py。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from arb.backtest.metrics import BacktestMetrics
from arb.optimize.search import Params
from arb.optimize.walk_forward import WalkForwardResult

# 回测≠实盘的强制风险提示,随每份摘要一同产出。
RISK_WARNING = (
    "风险提示:回测收益 ≠ 实盘收益。历史样本不代表未来,且回测口径未完全刻画"
    "滑点、盘口深度、资金费与撮合延迟。上线务必遵循 paper → testnet live → "
    "极小额实盘 的逐级放大流程:每一级稳定验证后再放大资金,任一阶段出现异常"
    "(偏离预期的滑点/回撤/成交率)应立即回退并复盘。"
)

# 新增 env 参数仅在此登记(本迭代不改 settings,由后续集成落地为 Settings 字段)。
SUGGESTED_ENV_VARS: dict[str, str] = {
    "ARB_OPT_TRAIN_SIZE": "walk-forward 训练窗样本数",
    "ARB_OPT_TEST_SIZE": "walk-forward 紧邻样本外窗样本数",
    "ARB_OPT_STEP": "walk-forward 滚动步长(默认=test_size)",
    "ARB_OPT_MAX_DRAWDOWN_BPS": "硬约束:样本外最大回撤上限(基点)",
    "ARB_OPT_MIN_TRADES": "硬约束:最少交易笔数 N",
    "ARB_OPT_BEST_PARAMS_PATH": "best_params.yaml 输出路径",
}


def to_param_dict(params: Params) -> dict[str, float | int]:
    """转为可序列化的原始类型字典(供 yaml 落地)。"""
    return {
        "window": int(params.window),
        "entry_z": float(params.entry_z),
        "exit_z": float(params.exit_z),
        "threshold_bps": float(params.threshold_bps),
    }


def best_params_doc(params_by_pair: dict[str, Params]) -> dict:
    """构造 best_params.yaml 的文档结构:{pairs: {pair_name: {...}}}。"""
    return {"pairs": {name: to_param_dict(p) for name, p in params_by_pair.items()}}


def write_best_params_yaml(path: str | Path, params_by_pair: dict[str, Params]) -> Path:
    """把每 pair 的最优参数写入 best_params.yaml,返回写入路径。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# arb.optimize 生成的标定参数(每 pair 一组)\n"
        "# " + RISK_WARNING.replace("\n", " ") + "\n"
    )
    body = yaml.safe_dump(
        best_params_doc(params_by_pair),
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
    )
    out.write_text(header + body, encoding="utf-8")
    return out


def _fmt_metrics(m: BacktestMetrics | None) -> str:
    if m is None:
        return "(无:全部参数被硬约束淘汰)"
    return (
        f"交易 {m.num_trades} | 胜率 {m.win_rate:.1%} | 累计 {m.total_bps:.2f}bps | "
        f"回撤 {m.max_drawdown_bps:.2f}bps | 夏普 {m.sharpe:.2f}"
    )


def format_is_oos_summary(pair_name: str, wf: WalkForwardResult) -> str:
    """样本内 vs 样本外对比摘要(含逐段选参与风险提示)。"""
    lines = [
        f"===== 参数标定摘要: {pair_name} =====",
        f"分段数        : {len(wf.segments)}(选出参数 {wf.num_selected} 段)",
        f"样本内(聚合)  : {_fmt_metrics(wf.in_sample)}",
        f"样本外(聚合)  : {_fmt_metrics(wf.out_sample)}",
    ]
    # 过拟合提示:样本外夏普明显低于样本内即预警。
    # 仅在样本外确有成交时比较,避免 OOS 无信号(0 笔)导致的误报。
    if (
        wf.in_sample.num_trades
        and wf.out_sample.num_trades
        and wf.out_sample.sharpe < wf.in_sample.sharpe
    ):
        lines.append(
            "  ! 样本外夏普低于样本内,存在过拟合风险,谨慎放大资金。"
        )
    lines.append("-- 逐段选参 --")
    for i, seg in enumerate(wf.segments):
        sp = seg.split
        if seg.chosen is None:
            lines.append(
                f"  段{i} [train {sp.train_start}:{sp.train_end} | test "
                f"{sp.test_start}:{sp.test_end}] -> 无可行参数(淘汰)"
            )
            continue
        p = seg.chosen
        lines.append(
            f"  段{i} [train {sp.train_start}:{sp.train_end} | test "
            f"{sp.test_start}:{sp.test_end}] -> "
            f"window={p.window} entry_z={p.entry_z} exit_z={p.exit_z} "
            f"threshold_bps={p.threshold_bps} | OOS {_fmt_metrics(seg.out_sample)}"
        )
    lines.append("-- 风险提示 --")
    lines.append(RISK_WARNING)
    return "\n".join(lines)
