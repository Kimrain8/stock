"""Panel 数据探索器 — 仿腾讯股票风格"""
import logging

import numpy as np
import pandas as pd
import panel as pn
import hvplot.pandas  # noqa: F401  必须导入以注册 .hvplot 访问器
import holoviews as hv

from .auth import sessions
from .config import settings
from .db_utils import load_user_symbols, load_kline, get_stock_name

logger = logging.getLogger("stock2.explorer")

pn.extension("tabulator", "codeeditor", sizing_mode="stretch_width")


# ============================================================
# 用户识别
# ============================================================
def _get_current_user() -> tuple[str, str]:
    try:
        cookies = getattr(pn.state, "cookies", None) or {}
        token = cookies.get(settings.SESSION_COOKIE)
        if not token:
            return "anonymous", "user"
        info = sessions.verify(token)
        if not info:
            return "anonymous", "user"
        return info["username"], info.get("role", "user")
    except Exception as e:
        logger.warning("get current user failed | err=%s", e)
        return "anonymous", "user"


# ============================================================
# 指标计算
# ============================================================
def _resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """按指定频率重采样 OHLCV。"""
    if df.empty:
        return df
    agg = {
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }
    return df.resample(rule).agg(agg).dropna(how="any")


def _apply_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df.empty:
        return df
    if period in ("分时", "五日"):
        return pd.DataFrame()
    if period == "日K":
        return df.tail(300)          # 原来 500，收紧到 300
    if period == "1年":
        return df.tail(250)
    if period == "周K":
        return _resample_ohlc(df, "W-FRI").tail(200)   # 加 tail
    if period == "月K":
        return _resample_ohlc(df, "ME").tail(60)
    if period == "3年":
        return _resample_ohlc(df, "W-FRI").tail(156)
    if period == "5年":
        return _resample_ohlc(df, "ME").tail(60)
    return df

def _calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算 MA5/10/20、量均线、MACD。"""
    if df.empty:
        return df
    df = df.copy()
    for n in (5, 10, 20):
        df[f"ma{n}"] = df["close"].rolling(n).mean()
    for n in (5, 10):
        df[f"vma{n}"] = df["volume"].rolling(n).mean()

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["dif"] = ema12 - ema26
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    df["macd"] = (df["dif"] - df["dea"]) * 2

    df["up"] = df["close"] >= df["open"]
    return df


# ============================================================
# 图表构建
# ============================================================
# A 股风格：涨红跌绿
RED = "#e53935"
GREEN = "#43a047"

MA_COLORS = {"ma5": "#ff9800", "ma10": "#2196f3", "ma20": "#9c27b0"}
VMA_COLORS = {"vma5": "#ff9800", "vma10": "#2196f3"}


def _build_main_chart(df: pd.DataFrame, title: str) -> hv.Overlay:
    """K 线 + MA 均线。"""
    kline = df.hvplot.ohlc(
        ylabel="价格",
        grid=True,
        title=title,
        tools=["hover", "box_zoom", "reset", "xwheel_zoom"],
        hover_cols=["open", "high", "low", "close", "volume",
                    "ma5", "ma10", "ma20"],
        pos_color=RED,
        neg_color=GREEN,
        line_color="#333333",
        bar_width=0.7,
        legend=False,
    )
    mas = df.hvplot.line(
        y=["ma5", "ma10", "ma20"],
        color=[MA_COLORS["ma5"], MA_COLORS["ma10"], MA_COLORS["ma20"]],
        line_width=1.3,
        hover=False,
        legend="top_left",
    )
    return (kline * mas).opts(
        frame_height=620,             # 只设高度
        show_legend=True,
        fontsize={"title": 14, "labels": 11},
    )


def _build_volume_chart(df: pd.DataFrame) -> hv.Overlay:
    """成交量柱 + 量均线。"""
    plot_df = df.reset_index()
    date_col = plot_df.columns[0]

    up_df = plot_df[plot_df["up"]]
    down_df = plot_df[~plot_df["up"]]

    common = dict(
        x=date_col, y="volume", ylabel="成交量",
        tools=["hover"], hover_cols=["volume", "vma5", "vma10"],
        legend=False,
    )
    bars_up = up_df.hvplot.bar(color=RED, alpha=0.75, **common)
    bars_down = down_df.hvplot.bar(color=GREEN, alpha=0.75, **common)
    vma = plot_df.hvplot.line(
        x=date_col, y=["vma5", "vma10"],
        color=[VMA_COLORS["vma5"], VMA_COLORS["vma10"]],
        line_width=1.1, legend="top_left", hover=False,
    )
    return (bars_up * bars_down * vma).opts(
        frame_height=260,
        show_legend=True,
        fontsize={"labels": 10},
    )


def _build_macd_chart(df: pd.DataFrame) -> hv.Overlay:
    """MACD 柱 + DIF/DEA 线。"""
    plot_df = df.reset_index()
    date_col = plot_df.columns[0]

    up_df = plot_df[plot_df["macd"] >= 0]
    down_df = plot_df[plot_df["macd"] < 0]

    common = dict(
        x=date_col, y="macd", ylabel="MACD",
        tools=["hover"], hover_cols=["dif", "dea", "macd"],
        legend=False,
    )
    bars_up = up_df.hvplot.bar(color=RED, alpha=0.75, **common)
    bars_down = down_df.hvplot.bar(color=GREEN, alpha=0.75, **common)
    dif_line = plot_df.hvplot.line(
        x=date_col, y="dif", color="#ff9800",
        line_width=1.2, legend="top_left", hover=False,
    )
    dea_line = plot_df.hvplot.line(
        x=date_col, y="dea", color="#9c27b0",
        line_width=1.2, legend="top_left", hover=False,
    )
    return (bars_up * bars_down * dif_line * dea_line).opts(
        frame_height=260,
        show_legend=True,
        fontsize={"labels": 10},
    )


def _build_info_bar(df: pd.DataFrame) -> str:
    """顶部的开/高/低/收/涨跌/时间信息。"""
    if df.empty:
        return "<div style='color:#999'>无数据</div>"
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    chg = last["close"] - prev["close"]
    chg_pct = chg / prev["close"] * 100 if prev["close"] else 0.0
    color = RED if chg >= 0 else GREEN

    def span(label, val):
        return (
            f"<span style='margin-right:24px'>"
            f"<span style='color:#888'>{label}</span> "
            f"<b style='color:{color};font-family:monospace'>{val}</b>"
            f"</span>"
        )

    return (
        "<div style='padding:8px 14px;background:#fafafa;"
        "border:1px solid #e0e0e0;border-radius:4px;font-size:14px'>"
        + span("开", f"{last['open']:.2f}")
        + span("高", f"{last['high']:.2f}")
        + span("低", f"{last['low']:.2f}")
        + span("收", f"{last['close']:.2f}")
        + span("涨跌", f"{chg:+.2f} ({chg_pct:+.2f}%)")
        + span("时间", last.name.strftime("%Y-%m-%d"))
        + "</div>"
    )


# ============================================================
# 工厂函数
# ============================================================
def build_explorer():
    """返回工厂函数，Panel 每个会话调用一次。"""

    def _factory():
        username, role = _get_current_user()
        logger.info("explorer session | user=%s | role=%s", username, role)

        symbols = load_user_symbols(username)
        if not symbols:
            return pn.Column(
                pn.pane.Markdown(f"### 👤 {username}"),
                pn.pane.Alert("无可用股票数据，请检查数据库表 stock_daily",
                              alert_type="warning"),
            )

        # ---------- 顶部工具栏 ----------
        symbol_select = pn.widgets.Select(
            name="股票", options=symbols, value=symbols[0], width=180,
        )
        period_tabs = pn.widgets.RadioButtonGroup(
            name="周期",
            options=["分时", "五日", "日K", "周K", "月K", "1年", "3年", "5年"],
            value="日K",
            button_type="default",
            sizing_mode="stretch_width",     # ← 改这个
        )

        # ---------- 显示区 ----------
        info_pane = pn.pane.HTML(min_height=48, sizing_mode="stretch_width")
        main_pane = pn.pane.HoloViews(sizing_mode="stretch_width")   # ← 只用 stretch_width
        vol_pane  = pn.pane.HoloViews(sizing_mode="stretch_width")
        macd_pane = pn.pane.HoloViews(sizing_mode="stretch_width")
        # ---------- 更新逻辑 ----------
        def update(symbol, period):
            if not symbol:
                return
            try:
                if period in ("分时", "五日"):
                    info_pane.object = (
                        "<div style='color:#888;padding:8px'>"
                        "该周期需要分钟数据（当前数据源只提供日线）</div>"
                    )
                    main_pane.object = None
                    vol_pane.object = None
                    macd_pane.object = None
                    return

                df = load_kline(symbol)
                if df.empty:
                    info_pane.object = _build_info_bar(df)
                    main_pane.object = None
                    vol_pane.object = None
                    macd_pane.object = None
                    return

                df = _apply_period(df, period)
                logger.info("update | symbol=%s | period=%s | rows=%d",
                            symbol, period, len(df))

                if df.empty:
                    info_pane.object = (
                        "<div style='color:#888;padding:8px'>无数据</div>"
                    )
                    main_pane.object = None
                    vol_pane.object = None
                    macd_pane.object = None
                    return

                df = _calc_indicators(df)
                info_pane.object = _build_info_bar(df)
                name = get_stock_name(symbol)
                title = f"{symbol} {name} · {period}".strip()
                main_pane.object = _build_main_chart(df, title) #更改图像标题
                vol_pane.object = _build_volume_chart(df)
                macd_pane.object = _build_macd_chart(df)
            except Exception as e:
                logger.exception("chart update failed")
                info_pane.object = (
                    f"<div style='color:red;padding:8px'>错误：{e}</div>"
                )

        pn.bind(update, symbol_select, period_tabs, watch=True)
        update(symbol_select.value, period_tabs.value)   # ← 加这一行
        
        # ---------- 布局 ----------
        header = pn.Row(
            pn.pane.Markdown(
                f"### 📊 {username} <span style='color:#888;font-size:13px'>"
                f"· 角色 {role}</span>",
                margin=(0, 20, 0, 0),
            ),
            pn.Spacer(),
        )

        toolbar = pn.Row(
            symbol_select,
            pn.Spacer(width=12),
            period_tabs,
            sizing_mode="stretch_width",
        )

        return pn.Column(
            header,
            toolbar,
            info_pane,
            main_pane,
            vol_pane,
            macd_pane,
            sizing_mode="stretch_width",
            styles={"padding": "12px", "background": "#fff"},
        )

    return _factory