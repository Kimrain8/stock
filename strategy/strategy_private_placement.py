# -*- coding: utf-8 -*-
"""定增公告监控策略（最近 N 天发布定向增发公告的股票）。

源项目: https://github.com/sngyai/Sequoia-X （PrivatePlacementStrategy）
本文件为独立可运行版本，不依赖 sequoia_x 包。

选股逻辑:
    1. 拉取东方财富"全部增发"公告数据（akshare.stock_qbzf_em()）
    2. 只保留发行方式 == "定向增发" 的记录（排除公开增发）
    3. 只保留发行日期在最近 7 天内的公告
    4. 提取 6 位股票代码并去重，按发行日期降序

注意:
    - 本策略不依赖行情 K 线，输入是【增发公告表】，与其它 6 个策略不同。
    - 默认自动联网拉取 akshare 数据；也可手动传入同结构 DataFrame。

────────────────────────────────────────────────────────────
输入信息（DataFrame，akshare stock_qbzf_em() 同结构，列名一致）:
    列名         含义              示例
    股票代码      6 位代码          "000001"
    股票简称      股票名称          "平安银行"
    发行方式      发行方式          "定向增发"  ← 必含此值才入选
    发行日期      公告日期          "2025-09-15"
    ...          （其余列忽略）

数据要求:
    - 发行日期列必须可解析为日期（pd.to_datetime）
    - 不传 DataFrame 时自动调用 akshare.stock_qbzf_em() 联网获取

运行方式:
    python private_placement_strategy.py
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

LOOKBACK_DAYS = 7  # 回看天数，覆盖一周内的新公告


def fetch_announcements() -> pd.DataFrame:
    """通过 akshare 拉取东方财富全部增发公告。

    Returns:
        增发公告 DataFrame；失败时返回空 DataFrame。

    Raises:
        ImportError: 未安装 akshare 时抛出。
    """
    import akshare as ak
    return ak.stock_qbzf_em()


def run(df: pd.DataFrame | None = None, lookback_days: int = LOOKBACK_DAYS) -> list[str]:
    """执行定增公告监控逻辑。

    Args:
        df: 增发公告 DataFrame（akshare stock_qbzf_em 同结构）。
            为 None 时自动联网获取。
        lookback_days: 回看天数，默认 7。

    Returns:
        最近 N 天内有定向增发公告的股票代码列表（去重、按日期降序）。
    """
    if df is None:
        try:
            df = fetch_announcements()
        except ImportError:
            raise RuntimeError(
                "未安装 akshare，请先 `pip install akshare`，或手动传入 DataFrame。"
            )
        except Exception as exc:
            raise RuntimeError(f"获取定增数据失败: {exc}") from exc

    if df is None or df.empty:
        return []

    # 只保留定向增发（排除公开增发）
    if "发行方式" not in df.columns:
        raise ValueError("输入缺少必要列: 发行方式")
    df = df[df["发行方式"] == "定向增发"]

    if df.empty:
        return []

    # 按发行日期过滤：最近 N 天
    if "发行日期" not in df.columns:
        raise ValueError("输入缺少必要列: 发行日期")

    today = date.today()
    cutoff = today - timedelta(days=lookback_days)

    df = df.copy()
    df["发行日期"] = pd.to_datetime(df["发行日期"], errors="coerce")
    df = df.dropna(subset=["发行日期"])
    df = df[df["发行日期"].dt.date >= cutoff]

    if df.empty:
        return []

    # 按发行日期降序（最新在前）
    df = df.sort_values("发行日期", ascending=False)

    # 提取 6 位股票代码
    if "股票代码" not in df.columns:
        raise ValueError("输入缺少必要列: 股票代码")
    symbols = df["股票代码"].astype(str).str.extract(r"(\d{6})")[0].dropna().tolist()

    # 去重（同一只票可能多次定增）
    unique_symbols = list(dict.fromkeys(symbols))
    return unique_symbols


def _demo_data() -> pd.DataFrame:
    """构造演示数据：含 2 条最近定向增发、1 条过期公告、1 条公开增发。"""
    today = date.today()
    return pd.DataFrame([
        {"股票代码": "000001. 平安银行", "股票简称": "平安银行", "发行方式": "定向增发",
         "发行日期": (today - timedelta(days=1)).strftime("%Y-%m-%d")},
        {"股票代码": "600519", "股票简称": "贵州茅台", "发行方式": "定向增发",
         "发行日期": (today - timedelta(days=3)).strftime("%Y-%m-%d")},
        {"股票代码": "000002", "股票简称": "万科A", "发行方式": "定向增发",
         "发行日期": (today - timedelta(days=30)).strftime("%Y-%m-%d")},  # 过期
        {"股票代码": "601318", "股票简称": "中国平安", "发行方式": "公开增发",
         "发行日期": (today - timedelta(days=2)).strftime("%Y-%m-%d")},  # 非定向
    ])


if __name__ == "__main__":
    demo = _demo_data()
    result = run(demo)
    print(f"选出的股票: {result}")
