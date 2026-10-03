"""基本面评分器 — 从缓存的 fetch_all 数据计算 basic_score。

输入：
    cache/data/{ts_code}.pkl    ← fetch_all 的输出

输出：
    {
        "ts_code": str,
        "name": str,
        "basic_score": int,           # 0~100
        "basic_grade": str,           # S/A/B/C/D
        "hard_pass": bool,            # 硬门槛是否通过
        "hard_fail_reason": str,      # 未通过的原因
        "details": dict,              # 每项得分明细
    }

⚠️ 数据表日期顺序说明（重要）：
    - abstract 表：列名是日期（如 '20260630'），是【降序】（最新在前）
    - indicators 表：有 '日期' 列，是【升序】（最旧在前）★ 必须重新排序
"""
import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("stock2.scoring")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = BASE_DIR / "cache" / "data"


# ============================================================
# 从缓存加载
# ============================================================
def load_cached_data(ts_code: str) -> dict | None:
    """加载一只股票的缓存数据。"""
    path = CACHE_DIR / f"{ts_code}.pkl"
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            return pickle.load(f)
    except Exception as e:
        logger.warning("load cache failed | %s | %s", ts_code, e)
        return None


# ============================================================
# 工具
# ============================================================
def _safe_float(v, default=None):
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _get_abstract_value(abstract: pd.DataFrame, keywords: list,
                        periods: int = 2) -> list | None:
    """从财务摘要里取某个指标的最近 N 期值（降序，第 0 个最新）。

    abstract 表结构：
        列 = ['选项', '指标', '20260630', '20260331', ...]（日期降序）
        行 = 各指标

    返回：[最新值, 次新值, ...]，取不到返回 None
    """
    if abstract is None or abstract.empty:
        return None

    # 日期列 = 除 '选项'、'指标' 外的其他列
    date_cols = [c for c in abstract.columns
                 if c not in ("选项", "指标")]

    # 防御：确保按日期降序（字符串形式的 'YYYYMMDD' 也能正确排序）
    try:
        date_cols = sorted(date_cols, reverse=True)
    except Exception as e:
        logger.warning("sort date_cols failed | %s", e)

    date_cols = date_cols[:periods]

    for kw in keywords:
        rows = abstract[
            abstract["指标"].astype(str).str.contains(kw, na=False, regex=False)
        ]
        if rows.empty:
            continue
        r = rows.iloc[0]
        vals = []
        for c in date_cols:
            v = _safe_float(r.get(c))
            if v is not None:
                vals.append(v)
        if vals:
            return vals

    return None


def _get_latest_indicator(indicators: pd.DataFrame,
                          col: str) -> float | None:
    """从 indicators 表取最新一期的某列值。

    ★ 关键修复（P1）：
        akshare 的 indicators 表是【升序】（最旧在前），
        iloc[0] 取到的是 2022 年的数据。
        必须先按日期降序排序，再取 iloc[0]。
    """
    if indicators is None or indicators.empty:
        return None
    if col not in indicators.columns:
        return None

    df = indicators.copy()

    # 按日期降序
    if "日期" in df.columns:
        try:
            df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
            df = df.sort_values("日期", ascending=False).reset_index(drop=True)
        except Exception as e:
            logger.warning("sort indicators failed | %s", e)

    return _safe_float(df.iloc[0].get(col))


# ============================================================
# 硬门槛
# ============================================================
def check_hard_thresholds(data: dict) -> tuple:
    """检查硬门槛。

    返回：(pass: bool, reasons: list[str])
    """
    reasons = []

    basic = data.get("basic", {}) or {}
    name = basic.get("A股简称", "")

    # H1：ST
    if "ST" in name.upper() or "*" in name:
        reasons.append(f"H1 退市风险：股票名称含 ST（{name}）")

    # H2：上市满 2 年
    list_date_str = basic.get("上市日期", "")
    if list_date_str:
        try:
            list_date = datetime.strptime(str(list_date_str)[:10], "%Y-%m-%d")
            days_listed = (datetime.now() - list_date).days
            if days_listed < 730:
                reasons.append(f"H2 上市不足 2 年（{days_listed} 天）")
        except Exception:
            pass

    # H3：流通市值 ≥ 30 亿
    quotes = data.get("quotes")
    if quotes is not None and not quotes.empty:
        last_close = _safe_float(quotes.iloc[-1].get("close"))
        float_share = None
        spot = data.get("spot", {}) or {}
        if "流通股" in spot:
            float_share = _safe_float(spot.get("流通股"))
        if last_close and float_share:
            float_mv = last_close * float_share / 1e8
            if float_mv < 30:
                reasons.append(f"H3 流通市值不足 30 亿（{float_mv:.1f} 亿）")

    # H4：最近 2 年净利润均 > 0
    financials = data.get("financials", {}) or {}
    abstract = financials.get("abstract")
    net_profit = _get_abstract_value(
        abstract, ["归母净利润", "净利润"], periods=4,
    )
    if net_profit:
        neg = [v for v in net_profit if v < 0]
        if len(neg) >= 2:
            reasons.append(f"H4 最近 2 年净利润出现负值（{neg}）")

    # H5：最近 2 年营收均 > 0
    revenue = _get_abstract_value(
        abstract, ["营业总收入", "营业收入"], periods=4,
    )
    if revenue:
        neg = [v for v in revenue if v <= 0]
        if len(neg) >= 2:
            reasons.append(f"H5 最近 2 年营收出现非正值")

    return (len(reasons) == 0, reasons)


# ============================================================
# 加分项：财务类（满分 70）
# ============================================================
def score_financial(data: dict) -> tuple:
    financials = data.get("financials", {}) or {}
    abstract = financials.get("abstract")
    indicators = financials.get("indicators")

    score = 0
    details = {}

    # F1：最近 2 年净利润均为正
    net_profit = _get_abstract_value(abstract, ["归母净利润", "净利润"], periods=4)
    if net_profit and all(v > 0 for v in net_profit[:2]):
        score += 15
        details["F1"] = "+15 净利润连续为正"
    else:
        details["F1"] = "0"

    # F2：最近 2 年扣非净利润均为正
    kf_profit = _get_abstract_value(
        abstract, ["扣非净利润", "扣除非经常性损益"], periods=4,
    )
    if kf_profit and all(v > 0 for v in kf_profit[:2]):
        score += 15
        details["F2"] = "+15 扣非连续为正"
    else:
        details["F2"] = "0"

    # F3：最近 2 年营收均为正
    revenue = _get_abstract_value(abstract, ["营业总收入", "营业收入"], periods=4)
    if revenue and all(v > 0 for v in revenue[:2]):
        score += 10
        details["F3"] = "+10 营收连续为正"
    else:
        details["F3"] = "0"

    # F4：ROE ≥ 8%（取最新一期）★ 用 _get_latest_indicator
    roe = None
    for col in ["净资产收益率(%)", "加权净资产收益率(%)"]:
        v = _get_latest_indicator(indicators, col)
        if v is not None:
            roe = v
            break
    if roe is not None and roe >= 8.0:
        score += 10
        details["F4"] = f"+10 ROE = {roe:.2f}%"
    elif roe is not None:
        details["F4"] = f"0（ROE = {roe:.2f}%）"
    else:
        details["F4"] = "0（无数据）"

    # F5：资产负债率 ≤ 70%（取最新一期）★ 用 _get_latest_indicator
    debt_ratio = _get_latest_indicator(indicators, "资产负债率(%)")
    if debt_ratio is not None and debt_ratio <= 70.0:
        score += 10
        details["F5"] = f"+10 资产负债率 = {debt_ratio:.2f}%"
    elif debt_ratio is not None:
        details["F5"] = f"0（资产负债率 = {debt_ratio:.2f}%）"
    else:
        details["F5"] = "0（无数据）"

    # F6：经营现金流 / 净利润 ≥ 0.5
    if abstract is not None and not abstract.empty:
        cf = _get_abstract_value(
            abstract, ["经营现金流", "经营活动产生的现金流量净额"], periods=1,
        )
        np_ = _get_abstract_value(abstract, ["归母净利润", "净利润"], periods=1)
        if cf and np_ and np_[0] != 0:
            ratio = cf[0] / np_[0]
            if ratio >= 0.5:
                score += 10
                details["F6"] = f"+10 现金流/净利润 = {ratio:.2f}"
            else:
                details["F6"] = f"0（现金流/净利润 = {ratio:.2f}）"
        else:
            details["F6"] = "0（数据不足）"

    return (score, details)


# ============================================================
# 加分项：公告类（满分 20）
# ============================================================
def score_announcements(data: dict) -> tuple:
    ann = data.get("announcements")
    div = data.get("dividends")

    score = 0
    details = {}

    if ann is None or ann.empty:
        return (0, {"E*": "无公告数据"})

    cutoff = datetime.now() - timedelta(days=365)
    ann_recent = ann.copy()
    if "公告日期" in ann.columns:
        ann_recent["公告日期"] = pd.to_datetime(
            ann_recent["公告日期"], errors="coerce",
        )
        ann_recent = ann_recent[ann_recent["公告日期"] >= cutoff]

    titles = ann_recent["公告标题"].astype(str).tolist() \
        if "公告标题" in ann_recent.columns else []

    # E1：无减持
    has_reduce = any("减持" in t for t in titles)
    if not has_reduce:
        score += 5
        details["E1"] = "+5 无减持"
    else:
        details["E1"] = "0（有减持公告）"

    # E2：无诉讼/处罚
    has_lawsuit = any(
        ("诉讼" in t or "处罚" in t or "仲裁" in t or "问询" in t)
        for t in titles
    )
    if not has_lawsuit:
        score += 5
        details["E2"] = "+5 无诉讼/处罚"
    else:
        details["E2"] = "0（有诉讼/处罚）"

    # E3：无商誉减值
    has_goodwill = any("商誉减值" in t for t in titles)
    if not has_goodwill:
        score += 5
        details["E3"] = "+5 无商誉减值"
    else:
        details["E3"] = "0（有商誉减值）"

    # E4：近 2 年连续分红
    if div is not None and not div.empty:
        n_div = len(div)
        if n_div >= 2:
            score += 5
            details["E4"] = f"+5 近 2 年 {n_div} 次分红"
        else:
            details["E4"] = f"0（{n_div} 次分红，不足 2 次）"
    else:
        details["E4"] = "0（无分红记录）"

    return (score, details)


# ============================================================
# 加分项：增长类（满分 10）
# ============================================================
def score_growth(data: dict) -> tuple:
    financials = data.get("financials", {}) or {}
    indicators = financials.get("indicators")

    score = 0
    details = {}

    if indicators is None or indicators.empty:
        return (0, {"G*": "无增长数据"})

    # G1：营收同比 ≥ 0 ★ 用 _get_latest_indicator
    rev_growth = _get_latest_indicator(indicators, "主营业务收入增长率(%)")
    if rev_growth is not None and rev_growth >= 0:
        score += 5
        details["G1"] = f"+5 营收增速 {rev_growth:.1f}%"
    elif rev_growth is not None:
        details["G1"] = f"0（营收增速 {rev_growth:.1f}%）"
    else:
        details["G1"] = "0（无数据）"

    # G2：净利润同比 ≥ 0 ★ 用 _get_latest_indicator
    profit_growth = _get_latest_indicator(indicators, "净利润增长率(%)")
    if profit_growth is not None and profit_growth >= 0:
        score += 5
        details["G2"] = f"+5 净利润增速 {profit_growth:.1f}%"
    elif profit_growth is not None:
        details["G2"] = f"0（净利润增速 {profit_growth:.1f}%）"
    else:
        details["G2"] = "0（无数据）"

    return (score, details)


# ============================================================
# 分档
# ============================================================
def score_to_grade(score: int) -> str:
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 45:
        return "B"
    if score >= 25:
        return "C"
    return "D"


# ============================================================
# 主入口
# ============================================================
def evaluate_one(ts_code: str) -> dict | None:
    data = load_cached_data(ts_code)
    if data is None:
        return None

    basic = data.get("basic", {}) or {}
    name = basic.get("A股简称", "") or basic.get("公司名称", "")

    hard_pass, hard_reasons = check_hard_thresholds(data)

    if not hard_pass:
        return {
            "ts_code": ts_code,
            "name": name,
            "basic_score": 0,
            "basic_grade": "D",
            "hard_pass": False,
            "hard_fail_reason": "; ".join(hard_reasons),
            "details": {},
        }

    s1, d1 = score_financial(data)
    s2, d2 = score_announcements(data)
    s3, d3 = score_growth(data)

    total = s1 + s2 + s3
    total = min(100, max(0, total))

    return {
        "ts_code": ts_code,
        "name": name,
        "basic_score": total,
        "basic_grade": score_to_grade(total),
        "hard_pass": True,
        "hard_fail_reason": "",
        "details": {**d1, **d2, **d3},
    }


def evaluate_batch(ts_codes: list) -> pd.DataFrame:
    rows = []
    for code in ts_codes:
        r = evaluate_one(code)
        if r is not None:
            rows.append(r)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("basic_score", ascending=False).reset_index(drop=True)
    return df


# ============================================================
# 测试
# ============================================================
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s")

    codes = sys.argv[1:] or ["688508.SH", "002074.SZ", "300750.SZ"]
    df = evaluate_batch(codes)
    if df.empty:
        print("无结果")
    else:
        print(f"\n{'='*80}")
        print(f"基本面评分结果")
        print(f"{'='*80}")
        for _, r in df.iterrows():
            print(f"\n{r['ts_code']} {r['name']}")
            print(f"  硬门槛：{'✅ 通过' if r['hard_pass'] else '❌ 未通过'}")
            if not r["hard_pass"]:
                print(f"  原因：{r['hard_fail_reason']}")
            else:
                print(f"  评分：{r['basic_score']} → {r['basic_grade']}")
                for k, v in r["details"].items():
                    print(f"    {k}: {v}")