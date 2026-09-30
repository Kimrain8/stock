"""数据访问层：把数据库查询封装在这里，explorer.py 只调用函数。"""
import logging

import numpy as np
import pandas as pd

from utils.dbManager import MySQLHelper
from .config import settings

logger = logging.getLogger("stock2.db")
_stock_names_cache: dict[str, str] | None = None


def load_stock_names(use_cache: bool = True) -> dict[str, str]:
    """返回 {ts_code: name} 映射。进程内缓存。"""
    global _stock_names_cache
    if use_cache and _stock_names_cache is not None:
        return _stock_names_cache

    db = MySQLHelper(db=settings.DB_NAME)
    try:
        df = db.qry2df("SELECT ts_code, name FROM ts_stock_basic")
    except Exception as e:
        logger.error("load stock names failed | err=%s", e)
        return {}

    if df is None or df.empty:
        _stock_names_cache = {}
        return {}

    _stock_names_cache = dict(
        zip(df["ts_code"].astype(str), df["name"].astype(str))
    )
    logger.info("stock names loaded | count=%d", len(_stock_names_cache))
    return _stock_names_cache

def get_stock_name(code: str) -> str:
    """查单个股票名称，找不到返回空串。"""
    names = load_stock_names()
    return names.get(str(code), "").strip()

def load_user_symbols(username: str) -> list[str]:
    """返回当前用户可访问的股票代码列表。

    当前实现：查询股票表里所有 distinct 代码。
    未来可按用户/部门过滤，例如：
        SELECT ts_code FROM user_watchlist WHERE username = '<username>'
    """
    db = MySQLHelper(db=settings.DB_NAME)
    try:
        df = db.qry2df(
            "SELECT tsb.ts_code FROM ts_stock_basic AS tsb"
        )
        if df is None or df.empty:
            logger.warning("no symbols found | user=%s", username)
            return []
        return df["ts_code"].astype(str).tolist()
    except Exception as e:
        logger.error("load symbols failed | user=%s | err=%s", username, e)
        return []


def load_kline(symbol: str, limit_days: int = 2000) -> pd.DataFrame:
    """加载日线数据，强制数值类型为 float64。"""
    db = MySQLHelper(db=settings.DB_NAME)
    if not symbol or "'" in symbol or ";" in symbol:
        logger.warning("invalid symbol | symbol=%s", symbol)
        return pd.DataFrame()

    sql = (
        f"SELECT h.trade_date, "
        f"h.open_price AS open, h.high, h.low, "
        f"h.close_price AS close, "
        f"h.vol AS volume, h.amount, "
        f"qq.ltgb as float_share "
        f"FROM ts_stock_daily_his h join ts_stock_basic AS tsb on h.ts_code = tsb.ts_code "
        f"join qq_qt qq on tsb.symbol = qq.symbol "
        f"WHERE h.ts_code = '{symbol}' "
        f"AND qq.trade_date = (select max(qq2.trade_date) from qq_qt AS qq2) "
        f"ORDER BY h.trade_date DESC LIMIT {int(limit_days)}"
    )
    try:
        df = db.qry2df(sql)
    except Exception as e:
        logger.error("load kline failed | symbol=%s | err=%s", symbol, e)
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").sort_index()

    # ---------- 关键：Decimal → float64 ----------
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            # 先 apply(float) 处理 Decimal，再 astype(float) 统一类型
            df[col] = df[col].apply(
                lambda x: float(x) if x is not None else np.nan
            ).astype(np.float64)

    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[df["volume"] > 0]   # 剔除停牌等异常
    # logger.info("after cast | dtypes:\n%s", df.dtypes)
    # logger.info("amount head: %s", df["amount"].head().tolist())
    return df

def load_minute(symbol: str, days: int = 1) -> pd.DataFrame:
    """加载最近 N 天的分钟数据。"""
    db = MySQLHelper(db=settings.DB_NAME)
    if not symbol or "'" in symbol:
        return pd.DataFrame()
    sql = (
        f"SELECT trade_time, open, high, low, close, vol AS volume "
        f"FROM stock_minute WHERE ts_code = '{symbol}' "
        f"AND trade_time >= DATE_SUB(NOW(), INTERVAL {int(days)} DAY) "
        f"ORDER BY trade_time"
    )
    try:
        df = db.qry2df(sql)
    except Exception as e:
        logger.error("load minute failed | %s", e)
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df["trade_time"] = pd.to_datetime(df["trade_time"])
    df = df.set_index("trade_time")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def load_scan_data(days: int = 365) -> pd.DataFrame:
    """加载近 N 自然天所有股票的收盘价，用于扫描。

    返回列：ts_code, trade_date, close_price
    """
    db = MySQLHelper(db=settings.DB_NAME)
    sql = f"""
        SELECT ts_code, trade_date, close_price
        FROM ts_stock_daily_his
        WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL {int(days)} DAY)
        ORDER BY ts_code, trade_date
    """
    try:
        df = db.qry2df(sql)
    except Exception as e:
        logger.error("load scan data failed | err=%s", e)
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")
    df = df.dropna(subset=["close_price"])
    return df