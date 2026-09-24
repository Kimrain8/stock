# Sequoia-X 选股策略独立版

从 [Sequoia-X](https://github.com/sngyai/Sequoia-X) 解析出的 7 个选股策略，每个策略一个独立 Python 文件，
不依赖原项目包，可单独运行。

## 文件清单

| 文件 | 策略 | 源项目类名 |
|---|---|---|
| `ma_volume_strategy.py` | 均线金叉 + 放量 | MaVolumeStrategy |
| `turtle_trade_strategy.py` | 海龟 20 日新高突破 | TurtleTradeStrategy |
| `high_tight_flag_strategy.py` | 高旗形整理 | HighTightFlagStrategy |
| `limit_up_shakeout_strategy.py` | 涨停洗盘 | LimitUpShakeoutStrategy |
| `uptrend_limit_down_strategy.py` | 上升趋势跌停 | UptrendLimitDownStrategy |
| `rps_breakout_strategy.py` | RPS 极强动量突破 | RpsBreakoutStrategy |
| `private_placement_strategy.py` | 定增公告监控 | PrivatePlacementStrategy |

## 通用输入格式（前 6 个策略）

标准 OHLCV 日 K DataFrame，列名必须完全一致：

| 列名 | 含义 | 示例 |
|---|---|---|
| symbol | 股票代码（纯数字） | "000001" |
| date | 交易日期 YYYY-MM-DD | "2025-01-01" |
| open | 开盘价 | 10.50 |
| high | 最高价 | 10.80 |
| low | 最低价 | 10.30 |
| close | 收盘价 | 10.60 |
| volume | 成交量（股） | 1_200_000 |
| turnover | 成交额（元） | 12_600_000 |

约定：单只股票按 `date` 升序；每个策略内部按 `symbol` 分组，多只股票一次传入即可。

## 各策略输入与参数

| 策略 | 最少 K 线数 | 特殊输入要求 | 可调参数 |
|---|---|---|---|
| ma_volume | 21 根 | 无 | — |
| turtle_trade | 21 根 | `turnover` 必填（流动性过滤）；可选 baostock 流通市值排序 | `sort_by_market_cap` |
| high_tight_flag | 40 根 | 无 | — |
| limit_up_shakeout | 3 根 | 无 | `limit_up_ratio=1.095`（20cm 板改 1.195） |
| uptrend_limit_down | 61 根 | 无 | `limit_down_ratio=0.905`（20cm 板改 0.805） |
| rps_breakout | 每只 ≥121 根 | **全市场截面数据**（数百只以上），单股无效 | `rps_period=120`, `rps_threshold=90` |
| private_placement | — | 增发公告表（akshare `stock_qbzf_em()` 结构），非 K 线 | `lookback_days=7` |

## 运行方式

```bash
# 每个文件自带演示数据，直接运行：
python ma_volume_strategy.py
python turtle_trade_strategy.py
...

# 实际使用：传入自己的 DataFrame
from ma_volume_strategy import run
result = run(my_ohlcv_df)   # -> ['000001', ...]
```

`private_placement_strategy.py` 不传参时自动通过 akshare 联网拉取东方财富增发数据
（需 `pip install akshare`）。

## 与原项目的行为差异（均为兼容性处理）

1. **turtle_trade**：原项目依赖 baostock 实时查询流通市值排序；独立版在未安装 baostock
   或网络失败时自动降级为按当日成交额排序，选股条件完全一致。
2. **private_placement**：原项目失败时静默返回空列表；独立版改为抛出异常，便于发现数据源问题。
3. **rps_breakout**：原项目直接从 SQLite 读全库；独立版改为接收全市场 DataFrame，
   逻辑与参数完全一致。
