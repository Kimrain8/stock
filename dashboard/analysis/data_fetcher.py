import logging
import pickle
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
"""数据抓取层 — 基于 akshare 多接口兜底。

所有函数失败返回空，不抛异常。
"""
logger = logging.getLogger("stock2.data_fetcher")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_PLEDGE_CACHE_FILE = BASE_DIR / "cache" / "pledge_all.pkl"
_PLEDGE_CACHE_TTL = 86400   # 24 小时


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

    try:
        df = ak.stock_financial_abstract(symbol=code)
        if df is not None and not df.empty:
            result["abstract"] = df
            logger.info("financials abstract OK | %s | %s", ts_code, df.shape)
    except Exception as e:
        logger.warning("stock_financial_abstract failed | %s", e)

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
    """公告：东财 → 巨潮，多接口兜底。"""
    import akshare as ak
    code = _strip_suffix(ts_code)

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    # 东财个股公告
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

    # 巨潮兜底
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
    """行情：日线 OHLCV。返回列：date, open, high, low, close, volume, amount"""
    import akshare as ak
    code = _strip_suffix(ts_code)

    # 新浪
    try:
        symbol = f"{_suffix(ts_code).lower()}{code}"
        df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq")
        if df is not None and not df.empty:
            df = df.reset_index()
            df = df.loc[:, ~df.columns.duplicated()]
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

    # 东财兜底
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
    """实时行情快照。"""
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
# 7. 解禁
# ============================================================
def fetch_share_release(ts_code: str) -> pd.DataFrame:
    """限售股解禁（历史记录）。

    返回空 DataFrame 表示无记录或数据源失败。
    """
    import akshare as ak
    code = _strip_suffix(ts_code)
    try:
        df = ak.stock_restricted_release_queue_em(symbol=code)
        if df is not None and not df.empty:
            logger.info("release OK | %s | rows=%d", ts_code, len(df))
            return df
    except Exception as e:
        logger.warning("stock_restricted_release_queue_em failed | %s | %s",
                       ts_code, e)
    return pd.DataFrame()


# ============================================================
# 8. 股东户数
# ============================================================
def fetch_holder_num(ts_code: str) -> pd.DataFrame:
    """股东户数变化。"""
    import akshare as ak
    code = _strip_suffix(ts_code)
    try:
        df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        if df is not None and not df.empty:
            logger.info("holder OK | %s | rows=%d", ts_code, len(df))
            return df
    except Exception as e:
        logger.warning("stock_zh_a_gdhs_detail_em failed | %s | %s",
                       ts_code, e)
    return pd.DataFrame()


# ============================================================
# 9. 质押
# ============================================================
_pledge_mem = {"df": None, "ts": 0.0}


def _get_pledge_all() -> pd.DataFrame | None:
    """获取全市场质押明细。

    优先顺序：内存 → 文件缓存（未过期）→ 在线 → 文件缓存（过期兜底）。
    返回 None 表示**数据源不可用**（区别于"该股票无记录"）。
    """
    # ---- 1. 内存缓存 ----
    if _pledge_mem["df"] is not None:
        if time.time() - _pledge_mem["ts"] < _PLEDGE_CACHE_TTL:
            return _pledge_mem["df"]

    # ---- 2. 文件缓存（未过期）----
    if _PLEDGE_CACHE_FILE.exists():
        age = time.time() - _PLEDGE_CACHE_FILE.stat().st_mtime
        if age < _PLEDGE_CACHE_TTL:
            try:
                with _PLEDGE_CACHE_FILE.open("rb") as f:
                    df = pickle.load(f)
                _pledge_mem["df"] = df
                _pledge_mem["ts"] = time.time()
                logger.info("pledge loaded from file | rows=%d | age=%.1fh",
                            len(df), age / 3600)
                return df
            except Exception as e:
                logger.warning("pledge file read failed | %s", e)

    # ---- 3. 在线拉取 ----
    import akshare as ak
    try:
        df = ak.stock_gpzy_pledge_ratio_detail_em()
        if df is None:
            df = pd.DataFrame()
        _PLEDGE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with _PLEDGE_CACHE_FILE.open("wb") as f:
                pickle.dump(df, f)
        except Exception as e:
            logger.warning("pledge file write failed | %s", e)

        _pledge_mem["df"] = df
        _pledge_mem["ts"] = time.time()
        logger.info("pledge fetched online | rows=%d", len(df))
        return df
    except Exception as e:
        logger.warning("stock_gpzy_pledge_ratio_detail_em failed | %s", e)

    # ---- 4. 兜底：用过期的文件缓存 ----
    if _PLEDGE_CACHE_FILE.exists():
        try:
            with _PLEDGE_CACHE_FILE.open("rb") as f:
                df = pickle.load(f)
            logger.warning("pledge using stale file cache | rows=%d", len(df))
            _pledge_mem["df"] = df
            _pledge_mem["ts"] = time.time()
            return df
        except Exception:
            pass

    # ---- 5. 彻底失败 ----
    logger.error("pledge data source unavailable | all fallbacks failed")
    return None


def fetch_pledge(ts_code: str) -> pd.DataFrame | None:
    """股权质押明细。

    返回值语义：
        None                 → 数据源不可用（**数据缺失**）
        pd.DataFrame()       → 该股票**无未解押记录**（安全）
        非空 DataFrame       → 有未解押记录
    """
    df_all = _get_pledge_all()

    # 数据源不可用
    if df_all is None:
        logger.warning("pledge: data source unavailable | %s", ts_code)
        return None

    if df_all.empty:
        logger.warning("pledge: empty source | %s", ts_code)
        return None

    if "股票代码" not in df_all.columns:
        logger.warning("pledge: missing column 股票代码 | cols=%s",
                       list(df_all.columns))
        return None

    code = _strip_suffix(ts_code)
    df = df_all[df_all["股票代码"].astype(str) == code].copy()

    # 只保留未解押
    if "状态" in df.columns:
        df = df[df["状态"].astype(str).str.contains("未解押", na=False)]

    logger.info("pledge OK | %s | rows=%d", ts_code, len(df))
    return df


# ============================================================
# 10. 统一入口
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
        "release": pd.DataFrame(),
        "holder": pd.DataFrame(),
        "pledge": pd.DataFrame(),
        "fetched_at": datetime.now(),
    }

    steps = [
        ("basic",         lambda: fetch_basic_info(ts_code)),
        ("financials",    lambda: fetch_financials(ts_code)),
        ("announcements", lambda: fetch_announcements(ts_code, announcement_days)),
        ("quotes",        lambda: fetch_quotes(ts_code, quote_days)),
        ("dividends",     lambda: fetch_dividends(ts_code)),
        ("spot",          lambda: fetch_spot(ts_code)),
        ("release",       lambda: fetch_share_release(ts_code)),
        ("holder",        lambda: fetch_holder_num(ts_code)),
        ("pledge",        lambda: fetch_pledge(ts_code)),
    ]
    for key, fn in steps:
        try:
            result[key] = fn()
        except Exception as e:
            logger.error("fetch %s failed | %s | %s", key, ts_code, e)

    logger.info("fetch_all done | %s", ts_code)
    logger.info("  basic: %s", bool(result["basic"]))
    logger.info("  financials.abstract: %s",
                "abstract" in result["financials"])
    logger.info("  financials.indicators: %s",
                "indicators" in result["financials"])
    logger.info("  announcements: %d", len(result["announcements"]))
    logger.info("  quotes: %d", len(result["quotes"]))
    logger.info("  dividends: %d", len(result["dividends"]))
    logger.info("  release: %d", len(result["release"]))
    logger.info("  holder: %d", len(result["holder"]))
    logger.info("  pledge: %s",
                "None(源不可用)" if result["pledge"] is None
                else f"{len(result['pledge'])} 条")
    logger.info("  spot: %s", bool(result["spot"]))
    return result