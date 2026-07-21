"""健康检查纯逻辑测试(离线可跑,不触碰网络/磁盘)。"""
import asyncio
import time

from arb.monitoring.health import (
    HealthMonitor,
    HealthState,
    age_ms,
    check_db_freshness,
    evaluate_health,
    is_fresh,
    main,
    measure_loop_latency_ms,
    probe_event_loop,
)


def test_age_ms():
    assert age_ms(None, 1000) is None
    assert age_ms(1000, 1500) == 500
    assert age_ms(2000, 1500) == -500  # 时钟回拨/未来时间戳


def test_is_fresh():
    assert is_fresh(1000, 1500, 1000) is True     # age 500 <= 1000
    assert is_fresh(1000, 3000, 1000) is False    # age 2000 > 1000
    assert is_fresh(None, 3000, 1000) is False    # 从未发生
    assert is_fresh(3000, 1000, 1000) is False    # 负 age(异常时钟)不算新鲜


def test_evaluate_health_all_ok():
    state = HealthState(
        last_market_ts_ms=1000,
        last_eval_ts_ms=1200,
        connector_connected=True,
        loop_alive=True,
    )
    report = evaluate_health(state, now=1500, market_max_age_ms=1000, eval_max_age_ms=1000)
    assert report.healthy is True
    assert report.ts_ms == 1500
    names = {c.name for c in report.checks}
    assert names == {"event_loop", "market_data", "evaluation", "connector"}
    assert all(c.ok for c in report.checks)


def test_evaluate_health_stale_market_fails():
    state = HealthState(
        last_market_ts_ms=1000,
        last_eval_ts_ms=9000,
        connector_connected=True,
    )
    report = evaluate_health(state, now=9000, market_max_age_ms=1000, eval_max_age_ms=1000)
    assert report.healthy is False
    market = next(c for c in report.checks if c.name == "market_data")
    assert market.ok is False


def test_evaluate_health_disconnected_and_no_data():
    report = evaluate_health(HealthState(), now=5000)
    assert report.healthy is False
    by_name = {c.name: c for c in report.checks}
    assert by_name["connector"].ok is False
    assert by_name["market_data"].detail == "no_data"
    assert by_name["evaluation"].detail == "no_data"


def test_report_as_dict_shape():
    report = evaluate_health(HealthState(loop_alive=True), now=100)
    d = report.as_dict()
    assert set(d.keys()) == {"healthy", "ts_ms", "checks"}
    assert isinstance(d["checks"], list)
    assert set(d["checks"][0].keys()) == {"name", "ok", "detail"}


def test_health_monitor_marks():
    mon = HealthMonitor(market_max_age_ms=1000, eval_max_age_ms=1000)
    mon.mark_market(ts_ms=1000)
    mon.mark_eval(ts_ms=1000)
    mon.set_connector(True)
    report = mon.report(now=1500)
    assert report.healthy is True
    # 时间推进后行情/评估变陈旧
    stale = mon.report(now=5000)
    assert stale.healthy is False


def test_measure_loop_latency_ms():
    latency = asyncio.run(measure_loop_latency_ms())
    assert latency >= 0.0


def test_probe_event_loop_ok():
    result = asyncio.run(probe_event_loop(max_latency_ms=5000))
    assert result.name == "event_loop"
    assert result.ok is True


def test_check_db_freshness_missing_db_is_unhealthy(tmp_path):
    # 指向不存在的库:探针应稳定返回不健康,且不得创建文件(只读语义)
    missing = tmp_path / "nope.db"
    assert not missing.exists()  # 前置条件
    report = asyncio.run(check_db_freshness(str(missing), now=1000, max_age_ms=1000))
    assert report.healthy is False
    assert report.checks[0].name == "db"
    assert report.checks[0].detail == "file_not_found"
    assert not missing.exists()  # 探针未创建文件


def test_check_db_freshness_fresh_db_is_healthy(tmp_path):
    import sqlite3

    db_path = tmp_path / "arb.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE spreads (ts INTEGER)")
    conn.execute("INSERT INTO spreads (ts) VALUES (1000)")
    conn.commit()
    conn.close()
    report = asyncio.run(check_db_freshness(str(db_path), now=1500, max_age_ms=1000))
    assert report.healthy is True
    assert report.checks[0].name == "db"


def test_cli_main_exit_codes(tmp_path):
    import sqlite3

    # 缺失库 -> 退出码 1
    missing = tmp_path / "none.db"
    assert main(["--db-path", str(missing), "--max-age-ms", "1000"]) == 1

    # 新鲜落库 -> 退出码 0(用当前时间写入一行保证在默认阀值内)
    db_path = tmp_path / "arb.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE spreads (ts INTEGER)")
    conn.execute("INSERT INTO spreads (ts) VALUES (?)", (int(time.time() * 1000),))
    conn.commit()
    conn.close()
    assert main(["--db-path", str(db_path)]) == 0
