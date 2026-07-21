"""指标汇总纯函数测试(离线可跑)。"""
from arb.monitoring.metrics_export import (
    MetricsCollector,
    MetricsSnapshot,
    render_prometheus,
    to_log_fields,
)


def test_render_prometheus_format():
    snap = MetricsSnapshot(
        signals=12, opens=3, closes=2, open_positions=1,
        equity=20100.5, margin_ratio=0.25, alerts=4, kill_switch=False,
    )
    text = render_prometheus(snap)
    assert "# HELP arb_signals_total" in text
    assert "# TYPE arb_signals_total counter" in text
    assert "arb_signals_total 12" in text
    assert "arb_opens_total 3" in text
    assert "arb_equity 20100.5" in text
    assert "arb_kill_switch 0" in text          # bool -> 0
    assert text.endswith("\n")


def test_render_prometheus_kill_switch_true():
    text = render_prometheus(MetricsSnapshot(kill_switch=True))
    assert "arb_kill_switch 1" in text


def test_custom_namespace():
    text = render_prometheus(MetricsSnapshot(signals=1), namespace="x")
    assert "x_signals_total 1" in text
    assert "arb_signals_total" not in text


def test_to_log_fields():
    fields = to_log_fields(MetricsSnapshot(signals=5, equity=100.0))
    assert fields["signals"] == 5
    assert fields["equity"] == 100.0
    assert fields["kill_switch"] is False


def test_collector_increment_and_snapshot():
    c = MetricsCollector()
    c.incr_signal()
    c.incr_signal(2)
    c.incr_open()
    c.incr_close()
    c.incr_alert()
    c.set_open_positions(2)
    c.set_equity(19999.9)
    c.set_margin_ratio(0.42)
    c.set_kill_switch(True)
    snap = c.snapshot()
    assert snap == MetricsSnapshot(
        signals=3, opens=1, closes=1, open_positions=2,
        equity=19999.9, margin_ratio=0.42, alerts=1, kill_switch=True,
    )


def test_collector_render_matches_snapshot():
    c = MetricsCollector()
    c.incr_open()
    assert c.render_prometheus() == render_prometheus(c.snapshot())
    assert c.to_log_fields() == to_log_fields(c.snapshot())
