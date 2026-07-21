# 加密价差套利系统

一个 delta 中性的加密货币价差 / 资金费率套利系统,聚焦**单交易所统一账户内的现货-永续基差 / 资金费率套利**(模型 A),并支持**跨交易所价差监控**(模型 B)。项目按**风险递进的迭代**推进,当前已完成全部 6 个迭代的代码实现。

> 安全默认:`ARB_TESTNET=true`、`ARB_ALLOW_LIVE=false`。真实下单(live)必须显式开启开关,且强烈建议先在测试网充分验证。

## 迭代路线图(全部已实现)

| 迭代 | 目标 | 交付 | 风险 |
|---|---|---|---|
| **1** | 脚手架 + 连接层 + 只读价差监控 | `monitor` 模式:实时净价差、落库、超阈值告警 | 极低(零下单) |
| **2** | 策略信号 + 回测回放 | z-score 进出场信号 + `backtest` 模式绩效报告 | 极低(离线) |
| **3** | 纸上交易 + 风控雏形 | `paper` 模式模拟撮合 + 仓位/delta/回撤风控 + kill switch | 低(无真实资金) |
| **4** | 测试网真实双腿对冲下单 | 交易连接器 + `live` 模式限价对冲状态机(超时撤单/部分成交/回滚) | 中(测试网资金) |
| **5** | 资金管理 + 监控 + Docker | 名义分配/保证金监控/再平衡建议 + Telegram 告警 + Docker | 高(可极小额实盘) |
| **6** | 跨交易所价差扩展 | `cross` 模式多连接器跨所价差 + 再平衡调度建议 | 高 |

## 目录结构

```
arb/
  config/       settings.py(env 全参数)/ models.py(PairConfig)/ symbols.yaml
  connectors/   base.py(Connector/TradingConnector 接口)/ ccxt_connector.py(只读+交易)
  marketdata/   spread.py(单所净价差)/ cross_spread.py(跨所净价差)
  strategy/     signal.py(ZScoreSignalEngine)
  backtest/     metrics.py / engine.py(回放)/ loader.py(db|csv)
  execution/    models.py(方向/盈亏)/ executor.py(模拟撮合)/ live_executor.py(真实下单状态机)
  risk/         rules.py(纯规则)/ manager.py(有状态风控 + kill switch)
  capital/      allocator.py(名义分配/保证金/再平衡建议)
  monitoring/   logger.py(structlog)/ alerts.py(Telegram,可选)
  trading_engine.py  paper/live 编排:行情->信号->风控->执行
  cross_monitor.py   跨交易所只读监控 + 再平衡建议
  main.py       多模式入口:monitor|backtest|paper|live|cross
docker/         Dockerfile / docker-compose.yml
tests/          test_spread / test_signal / test_backtest / test_risk / test_execution / test_capital
```

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 配置

```powershell
Copy-Item .env.example .env
```

所有运行参数均可经 `.env`(前缀 `ARB_`)覆盖,详见 [.env.example](.env.example) 与 [arb/config/settings.py](arb/config/settings.py)。关键项:

- `ARB_EXCHANGE` / `ARB_TESTNET`:交易所 id 与测试网开关(建议 `true`)。
- `ARB_MODE`:缺省运行模式(命令行 `--mode` 优先)。
- 策略:`ARB_ZSCORE_WINDOW` / `ARB_ENTRY_Z` / `ARB_EXIT_Z`。
- 风控:`ARB_MAX_POSITION_NOTIONAL` / `ARB_MAX_DELTA_BPS` / `ARB_MAX_DRAWDOWN_PCT`。
- 资金:`ARB_TOTAL_CAPITAL` / `ARB_LEVERAGE` / `ARB_MARGIN_ALERT_RATIO`。
- 安全:`ARB_ALLOW_LIVE`(live 真实下单必须显式 `true`)。
- 告警:`ARB_TELEGRAM_TOKEN` / `ARB_TELEGRAM_CHAT_ID`(留空则不发送)。

监控对象在 [arb/config/symbols.yaml](arb/config/symbols.yaml) 配置。跨所对象(模型 B)通过为 pair 设置不同的 `spot_exchange` / `perp_exchange` 启用(见文件内注释示例)。

## 运行模式

```powershell
# 迭代 1:只读价差监控(落库 + 告警)
python -m arb.main --mode monitor

# 迭代 2:在录制(SQLite)或 CSV 数据上回放策略并输出绩效报告
python -m arb.main --mode backtest      # ARB_BACKTEST_SOURCE=db|csv

# 迭代 3:测试网行情驱动的模拟撮合(不下单),验证信号/风控/kill switch
python -m arb.main --mode paper

# 迭代 4:测试网/实盘真实双腿对冲下单(需 ARB_ALLOW_LIVE=true)
python -m arb.main --mode live

# 迭代 6:跨交易所价差监控 + 再平衡建议(需配置跨所 pair)
python -m arb.main --mode cross
```

`Ctrl+C` 优雅退出;paper/live 退出时会尝试平掉所有在场仓位。

## 策略与执行逻辑

- **信号(迭代 2)**:对净价差维护滚动窗口 z-score。空仓时 `|z| >= entry_z` 且 `net_bps > threshold` 开仓(方向取净价差符号);持仓时 `|z| <= exit_z`(回归均值)平仓。
- **回测(迭代 2)**:复用信号引擎回放,近似口径 `realized_bps = entry_net - exit_net`,输出交易数、胜率、累计/平均收益、最大回撤、简化夏普。
- **模拟撮合(迭代 3)**:`SimulatedExecutor` 以含滑点的给定价立即成交,确定性、可测。
- **真实下单(迭代 4)**:`LiveExecutor` 双腿挂限价 → 轮询成交 → 超时撤单 → 按较小成交名义对齐两腿、市价回滚多余部分;若某腿完全未成交则回滚另一腿并判失败,避免裸露单腿。
- **风控(迭代 3)**:`RiskManager` 开仓前审批(仓位上限、delta 中性),结算盈亏跟踪权益/峰值,触发最大回撤即置 **kill switch**——拒绝新开仓并平掉所有仓位。
- **资金(迭代 5)**:`allocator` 按 `本金 * 杠杆` 均分可开名义,估算保证金占用率并告警;跨所场景给出把各所余额拉平到均值的**再平衡划转建议**(仅建议,不发起真实划转)。

## 净价差口径

```
net_bps = gross_bps - fee_bps + funding_bps
```
- `gross_bps`:两腿可成交价之差(基点),自动在正/负基差两个方向取更优者。
- `fee_bps`:开+平**往返**、双腿 taker 手续费之和(`2*(spot_fee+perp_fee)`)。
- `funding_bps`:资金费率方向化折算(空 perp 收、多 perp 付),作机会强弱参考。

## Docker 部署(迭代 5)

```powershell
docker compose -f docker/docker-compose.yml up --build
```
运行模式由 `.env` 的 `ARB_MODE` 决定,或运行时覆盖:`docker compose run --rm arb --mode paper`。

## 测试

```powershell
pytest
```
覆盖:净价差方向/手续费/资金费/过期数据;z-score 信号进出场;回测指标与回放;风控规则与 kill switch;模拟撮合往返盈亏与滑点;真实下单状态机(两腿成交/单腿回滚/部分成交对齐,用假连接器驱动);资金分配/保证金/再平衡;跨所价差。

## 风险与合规声明

- 本项目仅供学习与研究。请自行确认所在司法辖区从事加密货币交易的合法性;不含任何规避 KYC / 地域监管的手段。
- 聚焦统计价差收敛与资金费率套利,不做微秒级抢单。
- **跨交易所模型 B**:两腿分处不同交易所,保证金不共享,需各自备资金,并注意划转时间/成本与单腿风险敞口。
- 真实下单(live)默认关闭;开启前请在测试网完整验证开-平仓闭环,首次实盘务必极小额。
