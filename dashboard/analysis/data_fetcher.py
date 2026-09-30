import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
"""数据抓取层 — 基于 akshare 多接口兜底。

所有函数失败返回空，不抛异常。
"""
logger = logging.getLogger("stock2.data_fetcher")


# ============================================================
# 代码格式工具
# ============================================================
def _strip_suffix(ts_code: str) -> str:
    """'688508.SH' → '688508'"""
    return ts_code.split(".")[0]


def _suffix(ts_code: str) -> str:
    """'688508.SH' → 'SH'"""
    parts = ts_code.split(".")
    return parts[1].upper() if len(parts) > 1 else "SH"


def _market_of(ts_code: str) -> str:
    """判断科创板/创业板/主板"""
    code = _strip_suffix(ts_code)
    suffix = _suffix(ts_code)
    if suffix == "SH":
        if code.startswith("688"):
            return "科创板"
        return "沪市主板"
    if suffix == "SZ":
        if code.startswith("300"):
            return "创业板"
        return "深市主板"
    if suffix == "BJ":
        return "北交所"
    return "沪深京"


# ============================================================
# 1. 基本信息（巨潮）
# ============================================================
def fetch_basic_info(ts_code: str) -> dict:
    """基本信息：名称、行业、主营、法人、上市日期。"""
    import akshare as ak
    code = _strip_suffix(ts_code)

    try:
        df = ak.stock_profile_cninfo(symbol=code)
        if df is not None and not df.empty:
            row = df.iloc[0].to_dict()
            # 清理 NaN
            info = {
                k: ("" if (v is None or (isinstance(v, float) and np.isnan(v)))
                    else str(v))
                for k, v in row.items()
            }
            logger.info("basic_info OK | %s | %s", ts_code, info.get("A股简称"))
            return info
    except Exception as e:
        logger.warning("stock_profile_cninfo failed | %s | %s", ts_code, e)

    # 兜底：用实时行情里的名称
    try:
        code_full = f"{_suffix(ts_code).lower()}{code}"
        df = ak.stock_zh_a_spot()
        if df is not None and not df.empty:
            row = df[df["代码"] == code_full]
            if not row.empty:
                r = row.iloc[0]
                return {
                    "A股代码": code,
                    "A股简称": r.get("名称", ""),
                    "所属市场": _market_of(ts_code),
                }
    except Exception as e:
        logger.warning("stock_zh_a_spot fallback failed | %s", e)

    return {}


# ============================================================
# 2. 财报
# ============================================================
def fetch_financials(ts_code: str) -> dict:
    """财报：财务摘要 + 财务指标。"""
    import akshare as ak
    code = _strip_suffix(ts_code)
    result = {}

    # 财务摘要（新浪）
    try:
        df = ak.stock_financial_abstract(symbol=code)
        if df is not None and not df.empty:
            result["abstract"] = df
            logger.info("financials abstract OK | %s | %s", ts_code, df.shape)
    except Exception as e:
        logger.warning("stock_financial_abstract failed | %s", e)

    # 财务指标（新浪）
    try:
        df = ak.stock_financial_analysis_indicator(
            symbol=code, start_year="2022",
        )
        if df is not None and not df.empty:
            result["indicators"] = df
            logger.info("financials indicators OK | %s | %s", ts_code, df.shape)
    except Exception as e:
        logger.warning("stock_financial_analysis_indicator failed | %s", e)

    return result


# ============================================================
# 3. 公告
# ============================================================
def fetch_announcements(ts_code: str, days: int = 90) -> pd.DataFrame:
    """公告：东财 → 巨潮，多接口兜底。

    返回 DataFrame，含 [日期, 标题, 类型] 等字段。
    """
    import akshare as ak
    code = _strip_suffix(ts_code)

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    # ---- 接口 1：东财个股公告 ----
    # 签名: stock_individual_notice_report(security, symbol='全部', begin_date, end_date)
    for security_val in [code, f"{_suffix(ts_code)}{code}",
                         f"{_suffix(ts_code).lower()}{code}"]:
        try:
            df = ak.stock_individual_notice_report(
                security=security_val,
                symbol="全部",
                begin_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                logger.info("announcements (em) OK | %s | rows=%d",
                            ts_code, len(df))
                return df
        except Exception as e:
            logger.debug("notice attempt failed | security=%s | %s",
                         security_val, e)

    # ---- 接口 2：巨潮 ----
    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code,
            market="沪深京",
            start_date=start_date,
            end_date=end_date,
        )
        if df is not None and not df.empty:
            logger.info("announcements (cninfo) OK | %s | rows=%d",
                        ts_code, len(df))
            return df
    except Exception as e:
        logger.warning("cninfo announcement failed | %s", e)

    logger.warning("all announcement interfaces failed | %s", ts_code)
    return pd.DataFrame()


# ============================================================
# 4. 行情
# ============================================================
def fetch_quotes(ts_code: str, days: int = 60) -> pd.DataFrame:
    """行情：日线 OHLCV。

    返回列：date, open, high, low, close, volume, amount
    """
    import akshare as ak
    code = _strip_suffix(ts_code)

    # 接口 1：新浪（已验证可用）
    try:
        symbol = f"{_suffix(ts_code).lower()}{code}"
        df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq")
        if df is not None and not df.empty:
            df = df.reset_index()
            df = df.loc[:, ~df.columns.duplicated()]  # 去重列名
            if "date" not in df.columns:
                df = df.rename(columns={df.columns[0]: "date"})
            df["date"] = pd.to_datetime(df["date"])

            keep_cols = ["date", "open", "high", "low", "close", "volume"]
            if "amount" in df.columns:
                keep_cols.append("amount")
            df = df[keep_cols].copy()

            df = df.sort_values("date").tail(int(days * 1.6))
            df = df.reset_index(drop=True)
            logger.info("quotes (sina) OK | %s | rows=%d", ts_code, len(df))
            return df
    except Exception as e:
        logger.warning("stock_zh_a_daily failed | %s", e)

    # 接口 2：东财（可能被反爬）
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start, end_date=end, adjust="qfq",
        )
        if df is not None and not df.empty:
            df = df.rename(columns={
                "日期": "date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume",
                "成交额": "amount",
            })
            df["date"] = pd.to_datetime(df["date"])
            logger.info("quotes (em) OK | %s | rows=%d", ts_code, len(df))
            return df
    except Exception as e:
        logger.warning("stock_zh_a_hist failed | %s", e)

    return pd.DataFrame()


# ============================================================
# 5. 分红
# ============================================================
def fetch_dividends(ts_code: str) -> pd.DataFrame:
    """分红历史（巨潮）。"""
    import akshare as ak
    code = _strip_suffix(ts_code)
    try:
        df = ak.stock_dividend_cninfo(symbol=code)
        if df is not None and not df.empty:
            logger.info("dividends OK | %s | rows=%d", ts_code, len(df))
            return df
    except Exception as e:
        logger.warning("stock_dividend_cninfo failed | %s", e)
    return pd.DataFrame()


# ============================================================
# 6. 实时快照
# ============================================================
def fetch_spot(ts_code: str) -> dict:
    """实时行情快照：最新价、涨跌幅、成交额。"""
    import akshare as ak
    code_full = f"{_suffix(ts_code).lower()}{_strip_suffix(ts_code)}"
    try:
        df = ak.stock_zh_a_spot()
        if df is not None and not df.empty:
            row = df[df["代码"] == code_full]
            if not row.empty:
                return row.iloc[0].to_dict()
    except Exception as e:
        logger.warning("stock_zh_a_spot failed | %s", e)
    return {}


# ============================================================
# 7. 统一入口
# ============================================================
def fetch_all(ts_code: str,
              announcement_days: int = 90,
              quote_days: int = 60) -> dict:
    """一次抓全。任何一项失败都不影响其他项。"""
    logger.info("=" * 60)
    logger.info("fetch_all start | %s", ts_code)
    logger.info("=" * 60)

    result = {
        "ts_code": ts_code,
        "basic": {},
        "financials": {},
        "announcements": pd.DataFrame(),
        "quotes": pd.DataFrame(),
        "dividends": pd.DataFrame(),
        "spot": {},
        "fetched_at": datetime.now(),
    }

    steps = [
        ("basic",         lambda: fetch_basic_info(ts_code)),
        ("financials",    lambda: fetch_financials(ts_code)),
        ("announcements", lambda: fetch_announcements(ts_code, announcement_days)),
        ("quotes",        lambda: fetch_quotes(ts_code, quote_days)),
        ("dividends",     lambda: fetch_dividends(ts_code)),
        ("spot",          lambda: fetch_spot(ts_code)),
    ]
    for key, fn in steps:
        try:
            result[key] = fn()
        except Exception as e:
            logger.error("fetch %s failed | %s | %s", key, ts_code, e)

    # 汇总日志
    logger.info("fetch_all done | %s", ts_code)
    logger.info("  basic: %s", bool(result["basic"]))
    logger.info("  financials.abstract: %s",
                "abstract" in result["financials"])
    logger.info("  financials.indicators: %s",
                "indicators" in result["financials"])
    logger.info("  announcements: %d", len(result["announcements"]))
    logger.info("  quotes: %d", len(result["quotes"]))
    logger.info("  dividends: %d", len(result["dividends"]))
    logger.info("  spot: %s", bool(result["spot"]))
    return result