"""Panel 数据探索器 — 仿腾讯股票风格(Bokeh 原生版，多 tab 并发安全)"""
import logging

import numpy as np
import pandas as pd
import panel as pn
from bokeh.plotting import figure
from bokeh.models import ColumnDataSource, Range1d, HoverTool

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
        return df.tail(300)
    if period == "1年":
        return df.tail(250)
    if period == "周K":
        return _resample_ohlc(df, "W-FRI").tail(200)
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
# 通用工具
# ============================================================
# A 股风格：涨红跌绿
RED = "#e53935"
GREEN = "#43a047"

MA_COLORS = {"ma5": "#ff9800", "ma10": "#2196f3", "ma20": "#9c27b0"}
VMA_COLORS = {"vma5": "#ff9800", "vma10": "#2196f3"}


def _ensure_date_col(df: pd.DataFrame) -> pd.DataFrame:
    """把 date 列标准化，确保是 datetime 类型、升序、列名是 'date'。

    同时把所有数值列统一转成 float64，避免 Decimal / object 类型
    导致 Bokeh ColumnDataSource 序列化失败。
    """
    d = df.copy()
    if "date" not in d.columns:
        d.index.name = "date"
        d = d.reset_index()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").reset_index(drop=True)

    # ★ 关键：把所有非 date、非字符串的列转成 float64
    for col in d.columns:
        if col == "date":
            continue
        # Decimal / object / int / float 统一转 float64
        # 遇到无法转换的（如字符串），用 to_numeric 并忽略错误
        try:
            d[col] = pd.to_numeric(d[col], errors="coerce").astype(np.float64)
        except Exception:
            # 无法转换的列（如字符串列）跳过
            pass

    return d


def _calc_bar_width_ms(df: pd.DataFrame, ratio: float = 0.7) -> float:
    """根据相邻日期中位间隔，算出柱子的宽度（毫秒）。"""
    if len(df) < 2:
        return 86400000 * 0.7
    gaps = df["date"].diff().dt.total_seconds().dropna()
    median_gap = float(gaps.median()) if len(gaps) else 86400.0
    if not np.isfinite(median_gap) or median_gap <= 0:
        median_gap = 86400.0
    return median_gap * 1000.0 * ratio


# ============================================================
# 图 1：K 线 + MA 均线
# ============================================================
def _build_main_chart(df: pd.DataFrame, title: str,
                      x_range: Range1d | None = None):
    d = _ensure_date_col(df)
    if d.empty:
        return None

    inc = d["close"] >= d["open"]
    dec = ~inc

    bar_width = _calc_bar_width_ms(d)

    fig = figure(
        height=620,
        title=title,
        x_axis_type="datetime",
        tools="pan,box_zoom,reset,xwheel_zoom",
        toolbar_location="above",
    )
    if x_range is not None:
        fig.x_range = x_range
    fig.yaxis.axis_label = "价格"
    fig.grid.grid_line_alpha = 0.3

    # ---- 影线 ----
    for mask, color in [(inc, RED), (dec, GREEN)]:
        sub = d[mask]
        if not sub.empty:
            src_sub = ColumnDataSource(sub.reset_index(drop=True))
            fig.segment(
                x0="date", y0="high",
                x1="date", y1="low",
                source=src_sub,
                color=color, line_width=1,
            )

    # ---- 实体：拆成涨/跌两份 CDS ----
    src_up = ColumnDataSource(d[inc].reset_index(drop=True))
    src_dn = ColumnDataSource(d[dec].reset_index(drop=True))

    r_up = fig.vbar(
        x="date", width=bar_width, top="close", bottom="open",
        source=src_up, fill_color=RED, line_color=RED, alpha=0.9,
    )
    r_dn = fig.vbar(
        x="date", width=bar_width, top="open", bottom="close",
        source=src_dn, fill_color=GREEN, line_color=GREEN, alpha=0.9,
    )

    # ---- MA 均线 ----
    src_all = ColumnDataSource(d)
    fig.line("date", "ma5", source=src_all,
             color=MA_COLORS["ma5"], line_width=1.3, legend_label="MA5")
    fig.line("date", "ma10", source=src_all,
             color=MA_COLORS["ma10"], line_width=1.3, legend_label="MA10")
    fig.line("date", "ma20", source=src_all,
             color=MA_COLORS["ma20"], line_width=1.3, legend_label="MA20")

    # ---- Hover ----
    fig.add_tools(HoverTool(
        renderers=[r_up, r_dn],
        tooltips=[
            ("日期", "@date{%F}"),
            ("开",   "@open{0.00}"),
            ("高",   "@high{0.00}"),
            ("低",   "@low{0.00}"),
            ("收",   "@close{0.00}"),
            ("量",   "@volume{0,0}"),
        ],
        formatters={"@date": "datetime"},
    ))

    fig.legend.orientation = "horizontal"
    fig.legend.location = "top_left"
    fig.legend.click_policy = "hide"
    fig.add_layout(fig.legend[0], "above")
    return fig


# ============================================================
# 图 2：成交量柱 + 量均线
# ============================================================
def _build_volume_chart(df: pd.DataFrame,
                        x_range: Range1d | None = None):
    d = _ensure_date_col(df)
    if d.empty:
        return None

    inc = d["close"] >= d["open"]
    dec = ~inc

    bar_width = _calc_bar_width_ms(d)

    fig = figure(
        height=260,
        x_axis_type="datetime",
        tools="pan,box_zoom,reset,xwheel_zoom",
        toolbar_location="above",
    )
    if x_range is not None:
        fig.x_range = x_range
    fig.yaxis.axis_label = "成交量"
    fig.grid.grid_line_alpha = 0.3

    src_up = ColumnDataSource(d[inc].reset_index(drop=True))
    src_dn = ColumnDataSource(d[dec].reset_index(drop=True))

    r_up = fig.vbar(
        x="date", width=bar_width, top="volume", bottom=0,
        source=src_up, fill_color=RED, line_color=RED, alpha=0.75,
    )
    r_dn = fig.vbar(
        x="date", width=bar_width, top="volume", bottom=0,
        source=src_dn, fill_color=GREEN, line_color=GREEN, alpha=0.75,
    )

    src_all = ColumnDataSource(d)
    fig.line("date", "vma5", source=src_all,
             color=VMA_COLORS["vma5"], line_width=1.1, legend_label="VMA5")
    fig.line("date", "vma10", source=src_all,
             color=VMA_COLORS["vma10"], line_width=1.1, legend_label="VMA10")

    fig.add_tools(HoverTool(
        renderers=[r_up, r_dn],
        tooltips=[
            ("日期",   "@date{%F}"),
            ("成交量", "@volume{0,0}"),
        ],
        formatters={"@date": "datetime"},
    ))

    fig.legend.orientation = "horizontal"
    fig.legend.location = "top_left"
    fig.legend.click_policy = "hide"
    fig.add_layout(fig.legend[0], "above")
    return fig


# ============================================================
# 图 3：MACD 柱 + DIF/DEA
# ============================================================
def _build_macd_chart(df: pd.DataFrame,
                      x_range: Range1d | None = None):
    d = _ensure_date_col(df)
    if d.empty:
        return None

    up = d["macd"] >= 0
    dn = ~up

    bar_width = _calc_bar_width_ms(d)

    fig = figure(
        height=260,
        x_axis_type="datetime",
        tools="pan,box_zoom,reset,xwheel_zoom",
        toolbar_location="above",
    )
    if x_range is not None:
        fig.x_range = x_range
    fig.yaxis.axis_label = "MACD"
    fig.grid.grid_line_alpha = 0.3

    src_up = ColumnDataSource(d[up].reset_index(drop=True))
    src_dn = ColumnDataSource(d[dn].reset_index(drop=True))

    r_up = fig.vbar(
        x="date", width=bar_width, top="macd", bottom=0,
        source=src_up, fill_color=RED, line_color=RED, alpha=0.75,
    )
    r_dn = fig.vbar(
        x="date", width=bar_width, top="macd", bottom=0,
        source=src_dn, fill_color=GREEN, line_color=GREEN, alpha=0.75,
    )

    src_all = ColumnDataSource(d)
    fig.line("date", "dif", source=src_all,
             color="#ff9800", line_width=1.2, legend_label="DIF")
    fig.line("date", "dea", source=src_all,
             color="#9c27b0", line_width=1.2, legend_label="DEA")

    fig.add_tools(HoverTool(
        renderers=[r_up, r_dn],
        tooltips=[
            ("日期", "@date{%F}"),
            ("MACD", "@macd{0.000}"),
            ("DIF",  "@dif{0.000}"),
            ("DEA",  "@dea{0.000}"),
        ],
        formatters={"@date": "datetime"},
    ))

    fig.legend.orientation = "horizontal"
    fig.legend.location = "top_left"
    fig.legend.click_policy = "hide"
    fig.add_layout(fig.legend[0], "above")
    return fig


# ============================================================
# 信息栏
# ============================================================
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
        filter_input = pn.widgets.TextInput(
            name="搜索",
            placeholder="输入关键字过滤",
            width=140,
        )
        symbol_select = pn.widgets.Select(
            name="股票", options=symbols, value=symbols[0], width=180,
        )
        period_tabs = pn.widgets.RadioButtonGroup(
            name="周期",
            options=["分时", "五日", "日K", "周K", "月K", "1年", "3年", "5年"],
            value="日K",
            button_type="default",
            width=520,
        )

        # ---------- 显示区 ----------
        # 用 Bokeh 原生容器，高度由 Bokeh figure 内部设置
        info_pane = pn.pane.HTML(min_height=48, sizing_mode="stretch_width")
        main_pane = pn.pane.Bokeh(sizing_mode="stretch_width")
        vol_pane  = pn.pane.Bokeh(sizing_mode="stretch_width")
        macd_pane = pn.pane.Bokeh(sizing_mode="stretch_width")

        # ---------- 搜索过滤（同 trend 页面逻辑） ----------
        def on_filter(event):
            kw = (event.new or "").strip().lower()
            if kw:
                filtered = [s for s in symbols if kw in s.lower()]
            else:
                filtered = symbols
            current = symbol_select.value
            symbol_select.options = filtered
            if current not in filtered:
                symbol_select.value = None

        filter_input.param.watch(on_filter, "value")

        # ---------- 更新逻辑 ----------
        def update(symbol, period):
            if not symbol:
                info_pane.object = (
                    "<div style='color:#888;padding:8px'>请选择股票</div>"
                )
                main_pane.object = None
                vol_pane.object = None
                macd_pane.object = None
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

                # ★ 三图共享 x_range → 拖动/缩放联动
                # 用数据实际范围初始化，避免 Range1d() 空范围显示 1970
                _d = _ensure_date_col(df)
                if not _d.empty:
                    x_range = Range1d(
                        start=_d["date"].iloc[0].to_pydatetime(),
                        end=_d["date"].iloc[-1].to_pydatetime(),
                    )
                else:
                    x_range = Range1d()

                main_pane.object = _build_main_chart(df, title, x_range)
                vol_pane.object = _build_volume_chart(df, x_range)
                macd_pane.object = _build_macd_chart(df, x_range)
            except Exception as e:
                logger.exception("chart update failed")
                info_pane.object = (
                    f"<div style='color:red;padding:8px'>错误：{e}</div>"
                )

        pn.bind(update, symbol_select, period_tabs, watch=True)
        update(symbol_select.value, period_tabs.value)

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
            filter_input,
            pn.Spacer(width=8),
            symbol_select,
            pn.Spacer(width=8),
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