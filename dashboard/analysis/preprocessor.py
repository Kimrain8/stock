"""数据预处理 — 把 DataFrame 压缩成 LLM 能读的文本。

目标：把 fetch_all 的输出压成几千 token 的文本，给 agent 用。
"""
import logging
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger("stock2.preprocessor")


# ============================================================
# 工具
# ============================================================
def _is_nan(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and np.isnan(v):
        return True
    return False


def _fmt(v, unit: str = "", digits: int = 2) -> str:
    """把数值转成字符串。"""
    if _is_nan(v):
        return "—"
    try:
        f = float(v)
        if unit == "万元":
            return f"{f / 1e4:,.0f}"
        if unit == "亿元":
            return f"{f / 1e8:,.2f}"
        if abs(f) >= 1e8:
            return f"{f / 1e8:,.2f}亿"
        if abs(f) >= 1e4:
            return f"{f / 1e4:,.2f}万"
        return f"{f:,.{digits}f}"
    except Exception:
        return str(v)


# ============================================================
# 基本信息
# ============================================================
def build_basic_text(basic: dict) -> str:
    """基本信息压缩成一段文本。"""
    if not basic:
        return "（无基本信息）"

    keys = [
        ("公司名称", "公司名称"),
        ("A股简称", "简称"),
        ("A股代码", "代码"),
        ("所属市场", "市场"),
        ("所属行业", "行业"),
        ("法人代表", "法人"),
        ("注册资金", "注册资本(万元)"),
        ("成立日期", "成立"),
        ("上市日期", "上市"),
        ("主营业务", "主营"),
        ("经营范围", "经营范围"),
    ]
    lines = []
    for src, label in keys:
        v = basic.get(src)
        if v and not _is_nan(v):
            s = str(v)
            if len(s) > 200:
                s = s[:200] + "..."
            lines.append(f"- {label}: {s}")
    return "\n".join(lines) if lines else "（无基本信息）"


# ============================================================
# 财务摘要
# ============================================================
def build_financial_text(financials: dict, max_periods: int = 8) -> str:
    """财务摘要 → 关键指标表。

    输入 abstract 表结构：
        列 = ['选项', '指标', '20260630', '20260331', ...]
        行 = 各指标
    """
    if not financials:
        return "（无财务数据）"
    df = financials.get("abstract")
    if df is None or df.empty:
        return "（无财务数据）"

    # 日期列（排除"选项"和"指标"）
    date_cols = [c for c in df.columns if c not in ("选项", "指标")]
    date_cols = sorted(date_cols, reverse=True)[:max_periods]

    # 关注的指标（按顺序展示）
    key_indicators = [
        "营业总收入", "营业收入",
        "营业成本",
        "归母净利润", "净利润",
        "扣非净利润", "扣除非经常性损益",
        "销售毛利率", "毛利率",
        "销售净利率",
        "经营现金流", "经营活动产生的现金流量净额",
        "货币资金",
        "总资产", "净资产",
    ]

    lines = []
    # 表头
    header = f"{'指标':<16}" + "".join(f"{c:>14}" for c in date_cols)
    lines.append(header)
    lines.append("-" * len(header))

    used = set()
    for kw in key_indicators:
        if kw in used:
            continue
        # 匹配
        row = df[df["指标"].astype(str).str.contains(kw, na=False, regex=False)]
        if row.empty:
            continue
        r = row.iloc[0]
        used.add(r["指标"])
        values = []
        for c in date_cols:
            v = r.get(c)
            if _is_nan(v):
                values.append("—")
            else:
                try:
                    # 通用单位：元 → 万元
                    values.append(f"{float(v) / 1e4:,.0f}")
                except Exception:
                    values.append(str(v)[:12])
        lines.append(f"{str(r['指标'])[:16]:<16}" + "".join(f"{v:>14}" for v in values))

    return "\n".join(lines) + "\n\n（单位：万元）"


# ============================================================
# 财务指标
# ============================================================
def build_indicators_text(financials: dict, max_periods: int = 6) -> str:
    """财务指标（比率类）→ 文本。"""
    if not financials:
        return "（无指标数据）"
    df = financials.get("indicators")
    if df is None or df.empty:
        return "（无指标数据）"

    # 关注的关键比率
    key_cols = [
        "日期",
        "摊薄每股收益(元)", "加权每股收益(元)",
        "每股净资产_调整后(元)",
        "每股经营性现金流(元)",
        "净资产收益率(%)", "加权净资产收益率(%)",
        "销售毛利率(%)", "销售净利率(%)",
        "主营业务收入增长率(%)", "净利润增长率(%)",
        "资产负债率(%)", "流动比率", "速动比率",
        "总资产周转率(次)",
        "经营现金净流量与净利润的比率(%)",
        "总资产(元)",
    ]

    avail_cols = [c for c in key_cols if c in df.columns]
    if len(avail_cols) <= 1:
        return "（无关键指标）"

    # 取最近 N 期
    df = df.head(max_periods)

    lines = []
    # 表头
    header = f"{'指标':<24}" + "".join(f"{str(d):>10}" for d in df["日期"])
    lines.append(header)
    lines.append("-" * len(header))

    for col in avail_cols:
        if col == "日期":
            continue
        vals = []
        for v in df[col]:
            if _is_nan(v):
                vals.append("—")
            else:
                try:
                    f = float(v)
                    if abs(f) >= 1e8:
                        vals.append(f"{f / 1e8:.2f}亿")
                    else:
                        vals.append(f"{f:.2f}")
                except Exception:
                    vals.append(str(v)[:8])
        lines.append(f"{col[:24]:<24}" + "".join(f"{v:>10}" for v in vals))

    return "\n".join(lines)


# ============================================================
# 公告
# ============================================================
POSITIVE_KW = [
    "增持", "回购", "股权激励", "激励", "中标", "大额订单",
    "业绩预增", "预增", "分红", "派息", "送股", "转增",
    "战略合作", "签署", "获奖", "通过", "取得",
]
NEGATIVE_KW = [
    "减持", "违规", "处罚", "问询", "诉讼", "仲裁",
    "预亏", "商誉减值", "质押", "冻结", "警示", "风险提示",
    "终止", "失败", "延期", "辞职", "离职",
]


def _classify(title: str) -> str:
    t = str(title)
    pos = sum(1 for kw in POSITIVE_KW if kw in t)
    neg = sum(1 for kw in NEGATIVE_KW if kw in t)
    if pos > neg:
        return "● 利好"
    if neg > pos:
        return "▲ 利空"
    return "○ 中性"


def build_announcements_text(ann: pd.DataFrame, max_items: int = 25) -> str:
    """公告 → 事件列表。"""
    if ann is None or ann.empty:
        return "（近 90 天无公告）"

    lines = []
    for _, row in ann.head(max_items).iterrows():
        date = str(row.get("公告日期", ""))[:10]
        title = str(row.get("公告标题", "")).strip()
        atype = str(row.get("公告类型", "")).strip()
        tag = _classify(title)
        lines.append(f"[{date}] {tag} [{atype}] {title}")
    return "\n".join(lines)


# ============================================================
# 行情
# ============================================================
def build_quote_text(quotes: pd.DataFrame) -> str:
    """行情 → 涨跌幅、成交量分析。"""
    if quotes is None or quotes.empty:
        return "（无行情数据）"

    df = quotes.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if "close" not in df.columns:
        return "（行情缺收盘价）"

    close = df["close"]
    latest = close.iloc[-1]

    def _chg(days: int) -> str:
        if len(close) <= days:
            return "—"
        prev = close.iloc[-days - 1]
        if prev <= 0:
            return "—"
        pct = (latest / prev - 1) * 100
        return f"{pct:+.2f}%"

    lines = [
        f"- 最新价：{latest:.2f} 元",
        f"- 5 日涨跌：{_chg(5)}",
        f"- 20 日涨跌：{_chg(20)}",
        f"- 60 日涨跌：{_chg(60)}",
        f"- 近 20 日最高：{close.tail(20).max():.2f}",
        f"- 近 20 日最低：{close.tail(20).min():.2f}",
    ]

    # 成交量分析
    if "volume" in df.columns:
        v20 = df["volume"].tail(20).mean()
        v5 = df["volume"].tail(5).mean()
        if v20 > 0:
            ratio = v5 / v20
            if ratio > 1.3:
                tag = "放量"
            elif ratio < 0.7:
                tag = "缩量"
            else:
                tag = "平量"
            lines.append(f"- 近 5 日/20 日成交量比：{ratio:.2f}（{tag}）")

    # 最近 5 日
    lines.append("\n最近 5 个交易日：")
    for _, r in df.tail(5).iterrows():
        lines.append(
            f"  {r['date'].strftime('%Y-%m-%d')}  "
            f"开{r['open']:.2f} 高{r['high']:.2f} "
            f"低{r['low']:.2f} 收{r['close']:.2f}"
        )

    return "\n".join(lines)


# ============================================================
# 分红
# ============================================================
def build_dividend_text(div: pd.DataFrame) -> str:
    if div is None or div.empty:
        return "（无分红记录）"

    lines = []
    for _, r in div.tail(8).iterrows():
        date = str(r.get("实施方案公告日期", ""))[:10]
        desc = str(r.get("实施方案分红说明", "")).strip()
        report = str(r.get("报告时间", "")).strip()
        lines.append(f"  {date}  {report}  {desc}")
    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================
def preprocess(data: dict) -> dict:
    """把 fetch_all 的输出转成 LLM 友好的文本 dict。

    返回：
        {
            "ts_code": str,
            "name": str,
            "basic_text": str,
            "financial_text": str,
            "indicators_text": str,
            "announcements_text": str,
            "quote_text": str,
            "dividend_text": str,
            "spot_text": str,
        }
    """
    ts_code = data.get("ts_code", "")
    basic = data.get("basic") or {}

    result = {
        "ts_code": ts_code,
        "name": basic.get("A股简称", "") or basic.get("公司名称", ""),
        "basic_text": build_basic_text(basic),
        "financial_text": build_financial_text(data.get("financials") or {}),
        "indicators_text": build_indicators_text(data.get("financials") or {}),
        "announcements_text": build_announcements_text(
            data.get("announcements")
        ),
        "quote_text": build_quote_text(data.get("quotes")),
        "dividend_text": build_dividend_text(data.get("dividends")),
        "spot_text": _build_spot_text(data.get("spot") or {}),
    }
    logger.info(
        "preprocess done | %s | fin_len=%d | ann_len=%d | quote_len=%d",
        ts_code,
        len(result["financial_text"]),
        len(result["announcements_text"]),
        len(result["quote_text"]),
    )
    return result


def _build_spot_text(spot: dict) -> str:
    if not spot:
        return "（无实时快照）"
    keys = ["名称", "最新价", "涨跌幅", "成交量", "成交额", "昨收", "今开"]
    lines = [f"- {k}: {spot[k]}" for k in keys if k in spot and not _is_nan(spot[k])]
    return "\n".join(lines) if lines else "（无实时快照）"