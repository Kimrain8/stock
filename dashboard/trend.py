"""趋势探索页面 — 三图联动（成交量/成交额/换手率 + 均价/持续天数）"""
import logging

import numpy as np
import pandas as pd
import urllib.parse
import panel as pn
from bokeh.plotting import figure
from bokeh.models import (
    ColumnDataSource, Range1d, LinearAxis,HoverTool, PanTool, BoxSelectTool, BoxZoomTool,
    WheelZoomTool, ResetTool,
)
from bokeh.plotting import figure
from bokeh.events import SelectionGeometry
from .auth import sessions
from .config import settings
from .db_utils import load_user_symbols, load_kline, get_stock_name

logger = logging.getLogger("stock2.trend")

pn.extension("tabulator", sizing_mode="stretch_width")


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
# 数据处理
# ============================================================
def add_trend_columns(df: pd.DataFrame) -> pd.DataFrame:
    """向量化添加 asc_cnt / desc_cnt / low_days / high_days。"""
    df = df.copy()
    close = df["close"].to_numpy(dtype=np.float64)
    n = len(close)

    if n == 0:
        for c in ("asc_cnt", "desc_cnt", "low_days", "high_days"):
            df[c] = pd.Series(dtype=np.int64)
        return df

    # ---- asc_cnt / desc_cnt ----
    diff = np.empty(n, dtype=np.float64)
    diff[0] = np.nan
    diff[1:] = np.diff(close)
    sign = np.sign(diff)
    sign_series = pd.Series(sign).replace(0, np.nan).ffill().fillna(0)
    sign_arr = sign_series.to_numpy()

    is_up = (sign_arr > 0).astype(np.int32)
    is_down = (sign_arr < 0).astype(np.int32)

    def _consecutive(arr: np.ndarray) -> np.ndarray:
        group_id = np.cumsum(arr == 0)
        return pd.Series(arr).groupby(group_id).cumsum().to_numpy(dtype=np.int64)

    df["asc_cnt"] = _consecutive(is_up)
    df["desc_cnt"] = _consecutive(is_down)

    # ---- low_days / high_days ----
    idx = np.arange(n)
    lower = idx[:, None] > idx[None, :]

    less_mask = (close[:, None] > close[None, :]) & lower
    last_less = np.where(less_mask, idx[None, :], -1).max(axis=1)
    df["low_days"] = (idx - last_less).astype(np.int64)

    greater_mask = (close[:, None] < close[None, :]) & lower
    last_greater = np.where(greater_mask, idx[None, :], -1).max(axis=1)
    df["high_days"] = (idx - last_greater).astype(np.int64)

    return df

def add_rolling_metrics(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """计算滚动总和、VWAP、换手率、窗口起始日期。"""
    N = max(window, 0)
    win = N + 1
    d = df.copy()

    d["sum_vol"] = d["volume"].rolling(win, min_periods=1).sum()  # 手
    d["sum_amt"] = d["amount"].rolling(win, min_periods=1).sum()  # 千元

    # 窗口起始日期（N 天前的日期），N=0 时等于当天
    d["date_min"] = d["date"].shift(N).fillna(d["date"])

    # VWAP（元/股）：sum_amt(千元)×1000 / [sum_vol(手)×100] = sum_amt × 10 / sum_vol
    denom = d["sum_vol"].replace(0, np.nan)
    d["avg_price"] = d["sum_amt"] * 10 / denom

    if "float_share" in d.columns:
        fs = pd.to_numeric(d["float_share"], errors="coerce")
        d["turnover_pct"] = d["sum_vol"] * 100 / fs.replace(0, np.nan) * 100
    else:
        d["turnover_pct"] = np.nan

    return d

def _prepare_data(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """主流程：确保 date 列、加派生列、排序、类型转换。"""
    d = df.copy()
    if "date" not in d.columns:
        if "trade_date" in d.columns:
            d = d.rename(columns={"trade_date": "date"})
        else:
            d = d.reset_index()
            d = d.rename(columns={d.columns[0]: "date"})

    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").reset_index(drop=True)

    for col in ("open", "high", "low", "close", "volume", "amount",
                "float_share"):
        if col in d.columns:
            d[col] = pd.to_numeric(d[col], errors="coerce").astype(np.float64)

    d = add_trend_columns(d)
    d = add_rolling_metrics(d, window)
    return d

# ============================================================
# 工具
# ============================================================
def _gap_days(d: pd.DataFrame) -> float:
    """相邻日期中位间隔（天）。"""
    if len(d) > 1:
        gaps = d["date"].diff().dt.total_seconds() / 86400.0
        valid = gaps[gaps > 0]
        g = float(valid.median()) if len(valid) else 1.0
    else:
        g = 1.0
    if not np.isfinite(g) or g <= 0:
        g = 1.0
    return g


def _norm(arr: np.ndarray, q: float = 99.0) -> float:
    """99 分位归一化基准。"""
    if len(arr) == 0:
        return 1.0
    finite = arr[np.isfinite(arr)]
    if len(finite) == 0:
        return 1.0
    v = float(np.percentile(finite, q))
    return v if v > 0 else 1.0

def _read_url_params() -> dict:
    """从多个来源读取 URL 参数，返回 dict，值统一为 str。"""
    raw = {}

    # ---- 1. session_args ----
    try:
        args = getattr(pn.state, "session_args", None)
        if args:
            raw = dict(args)
    except Exception:
        pass

    # ---- 2. location.search ----
    if not raw:
        try:
            loc = getattr(pn.state, "location", None)
            search = getattr(loc, "search", "") if loc else ""
            if search:
                qs = urllib.parse.parse_qs(search.lstrip("?"))
                raw = qs
        except Exception:
            pass

    # ---- 3. 原始 ASGI scope ----
    if not raw:
        try:
            scope = getattr(pn.state, "scope", None)
            if scope:
                qs_raw = scope.get("query_string", b"")
                if qs_raw:
                    raw = urllib.parse.parse_qs(qs_raw.decode("utf-8"))
        except Exception:
            pass

    # ---- 统一转成 {str: str} ----
    def _to_str(v):
        if isinstance(v, bytes):
            return v.decode("utf-8", errors="ignore")
        if isinstance(v, list):
            return _to_str(v[0]) if v else ""
        return str(v) if v is not None else ""

    return {_to_str(k): _to_str(v) for k, v in raw.items()}

# ============================================================
# 图 1：三根柱子，底部对齐 avg_price
# ============================================================
def _build_fig1(d: pd.DataFrame, gap: float, x_range: Range1d, code_label: str = ""):
    fig = figure(
        x_range=x_range, height=380,
        title=f"{code_label} ① 成交量 / 成交额 / 换手率（底部对齐均价）".strip(),
        x_axis_type="datetime",
        tools="pan,box_zoom,reset,xwheel_zoom",
        toolbar_location="above",
    )
    fig.xaxis.axis_label = "日期"
    fig.yaxis.axis_label = "价格（元）"

    max_vol = _norm(d["sum_vol"].to_numpy())
    max_amt = _norm(d["sum_amt"].to_numpy())
    max_to = _norm(d["turnover_pct"].to_numpy())

    price_min = float(d["avg_price"].min())
    price_max = float(d["avg_price"].max())
    price_span = price_max - price_min
    if not np.isfinite(price_span) or price_span <= 0:
        price_span = max(abs(float(d["avg_price"].mean())) * 0.05, 0.1)

    # ---- 柱子只占 20% 空间，让均价占 80% ----
    scale = price_span * 0.20

    y_lo = price_min - price_span * 0.03
    y_hi = price_max + scale + price_span * 0.03
    fig.y_range = Range1d(start=y_lo, end=y_hi)

    # ---- 右轴：与左轴同范围，标签为均价 ----
    fig.extra_y_ranges["avg"] = Range1d(start=y_lo, end=y_hi)
    fig.add_layout(
        LinearAxis(y_range_name="avg", axis_label="均价（元）"), "right"
    )

    bar_w_ms = gap * 86400000 * 0.25
    offset_ms = gap * 86400000 * 0.30

    xs_vol, xs_amt, xs_to = [], [], []
    tops_vol, tops_amt, tops_to = [], [], []
    bottoms = []
    dates, date_mins = [], []
    vals_vol, vals_amt, vals_to, avg_prices = [], [], [], []

    for _, row in d.iterrows():
        date_i = row["date"]
        ap = row["avg_price"]
        if not np.isfinite(ap):
            continue

        h_vol = (row["sum_vol"] / max_vol) * scale if np.isfinite(row["sum_vol"]) else 0
        h_amt = (row["sum_amt"] / max_amt) * scale if np.isfinite(row["sum_amt"]) else 0
        h_to = (row["turnover_pct"] / max_to) * scale if np.isfinite(row["turnover_pct"]) else 0

        xs_vol.append(date_i - pd.Timedelta(milliseconds=offset_ms))
        xs_amt.append(date_i)
        xs_to.append(date_i + pd.Timedelta(milliseconds=offset_ms))
        tops_vol.append(ap + h_vol)
        tops_amt.append(ap + h_amt)
        tops_to.append(ap + h_to)
        bottoms.append(ap)

        dates.append(date_i)
        date_mins.append(row["date_min"])
        vals_vol.append(row["sum_vol"])
        vals_amt.append(row["sum_amt"])
        vals_to.append(row["turnover_pct"])
        avg_prices.append(ap)

    src = ColumnDataSource(dict(
        x_vol=xs_vol, top_vol=tops_vol,
        x_amt=xs_amt, top_amt=tops_amt,
        x_to=xs_to, top_to=tops_to,
        bottom=bottoms,
        date=dates, date_min=date_mins,
        sum_vol=vals_vol, sum_amt=vals_amt,
        turnover=vals_to, avg_price=avg_prices,
    ))

    r_vol = fig.vbar(x="x_vol", top="top_vol", bottom="bottom", width=bar_w_ms,
                     source=src, color="#1e88e5", alpha=0.85,
                     legend_label="成交量")
    r_amt = fig.vbar(x="x_amt", top="top_amt", bottom="bottom", width=bar_w_ms,
                     source=src, color="#43a047", alpha=0.85,
                     legend_label="成交额")
    r_to = fig.vbar(x="x_to", top="top_to", bottom="bottom", width=bar_w_ms,
                    source=src, color="#fb8c00", alpha=0.85,
                    legend_label="换手率")

    # ---- 均价折线（穿过柱子底部）----
    avg_line = fig.line(
        x="date", y="avg_price", source=src,
        color="#0d47a1", line_width=2.5,
        legend_label="均价",
    )

    fig.add_tools(HoverTool(
        renderers=[r_vol, r_amt, r_to, avg_line],
        tooltips=[
            ("当前日期", "@date{%F}"),
            ("起始日期", "@date_min{%F}"),
            ("成交量",   "@sum_vol{0,0} 手"),
            ("成交额",   "@sum_amt{0,0} 千元"),
            ("均价",     "@avg_price{0.000} 元"),
            ("换手率",   "@turnover{0.00} %"),
        ],
        formatters={"@date": "datetime", "@date_min": "datetime"},
    ))

    fig.legend.orientation = "horizontal"
    fig.legend.location = "top_left"
    fig.legend.click_policy = "hide"
    fig.add_layout(fig.legend[0], "above")
    return fig


# ============================================================
# 图 2：三根柱子，从 0 开始，双轴
# ============================================================
from bokeh.models import (
    PanTool, BoxSelectTool, BoxZoomTool, WheelZoomTool, ResetTool,
)


def _build_fig2(d: pd.DataFrame, gap: float, x_range: Range1d,
                on_range_select=None, code_label: str = ""):
    pan_tool = PanTool(dimensions="width")
    box_sel_tool = BoxSelectTool(dimensions="width")
    box_zoom_tool = BoxZoomTool(dimensions="width")     # ← 框选放大也只水平
    wheel_zoom_tool = WheelZoomTool(dimensions="width") # ← 滚轮只缩放 x
    reset_tool = ResetTool()

    fig = figure(
        x_range=x_range, height=380,
        title=f"{code_label} ② 成交量 / 成交额 / 换手率 + 均价（工具栏切换：手形=拖动，方框=框选）".strip(),
        x_axis_type="datetime",
        tools=[pan_tool, box_sel_tool, box_zoom_tool, wheel_zoom_tool, reset_tool],
        toolbar_location="above",
    )
    fig.xaxis.axis_label = "日期"
    fig.yaxis.axis_label = "数值"

    # ---------- 关键：y 轴固定 [0, y_max × 1.15] ----------
    y_max = max(
        float(np.nanmax(d["sum_vol"].to_numpy())) if len(d) else 1.0,
        float(np.nanmax(d["sum_amt"].to_numpy())) if len(d) else 1.0,
    )
    if not np.isfinite(y_max) or y_max <= 0:
        y_max = 1.0
    y_hi = y_max * 1.15
    fig.y_range = Range1d(start=0, end=y_hi, bounds=(0, y_hi))   # ← bounds 锁定

    to_max = float(np.nanmax(d["turnover_pct"].to_numpy())) if len(d) else 0
    if not np.isfinite(to_max) or to_max <= 0:
        to_max = 10.0
    to_hi = to_max * 1.15
    fig.extra_y_ranges["pct"] = Range1d(start=0, end=to_hi, bounds=(0, to_hi))

    fig.add_layout(
        LinearAxis(y_range_name="pct", axis_label="换手率（%）"), "right"
    )

    # ---------- 3. 数据准备（省略，与之前相同） ----------
    bar_w_ms = gap * 86400000 * 0.25
    offset_ms = gap * 86400000 * 0.30

    xs_vol, xs_amt, xs_to = [], [], []
    vals_vol, vals_amt, vals_to = [], [], []
    dates, date_mins, avg_prices = [], [], []

    ap_arr = d["avg_price"].to_numpy(dtype=np.float64)
    finite = ap_arr[np.isfinite(ap_arr)]
    ap_min = float(finite.min()) if len(finite) else 0.0
    ap_max = float(finite.max()) if len(finite) else 1.0
    ap_span = ap_max - ap_min if ap_max > ap_min else 1.0
    avg_scaled = 0.15 * y_max + ((ap_arr - ap_min) / ap_span) * 0.70 * y_max

    for i, (_, row) in enumerate(d.iterrows()):
        date_i = row["date"]
        xs_vol.append(date_i - pd.Timedelta(milliseconds=offset_ms))
        xs_amt.append(date_i)
        xs_to.append(date_i + pd.Timedelta(milliseconds=offset_ms))
        vals_vol.append(row["sum_vol"] if np.isfinite(row["sum_vol"]) else 0)
        vals_amt.append(row["sum_amt"] if np.isfinite(row["sum_amt"]) else 0)
        vals_to.append(row["turnover_pct"] if np.isfinite(row["turnover_pct"]) else 0)
        dates.append(date_i)
        date_mins.append(row["date_min"])
        avg_prices.append(row["avg_price"])

    src = ColumnDataSource(dict(
        x_vol=xs_vol, y_vol=vals_vol,
        x_amt=xs_amt, y_amt=vals_amt,
        x_to=xs_to, y_to=vals_to,
        x_line=dates, y_line=avg_scaled.tolist(),
        date=dates, date_min=date_mins,
        sum_vol=vals_vol, sum_amt=vals_amt,
        turnover=vals_to, avg_price=avg_prices,
    ))

    r_vol = fig.vbar(x="x_vol", top="y_vol", bottom=0, width=bar_w_ms,
                     source=src, color="#1e88e5", alpha=0.6,
                     legend_label="成交量（手）")
    r_amt = fig.vbar(x="x_amt", top="y_amt", bottom=0, width=bar_w_ms,
                     source=src, color="#43a047", alpha=0.6,
                     legend_label="成交额（千元）")
    r_to = fig.vbar(x="x_to", top="y_to", bottom=0, width=bar_w_ms,
                    source=src, color="#fb8c00", alpha=0.6,
                    y_range_name="pct", legend_label="换手率（%）")
    avg_line = fig.line(
        x="x_line", y="y_line", source=src,
        color="#0d47a1", line_width=2.5,
        legend_label="均价（示意）",
    )

    fig.add_tools(HoverTool(
        renderers=[r_vol, r_amt, r_to, avg_line],
        tooltips=[
            ("当前日期", "@date{%F}"),
            ("起始日期", "@date_min{%F}"),
            ("成交量",   "@sum_vol{0,0} 手"),
            ("成交额",   "@sum_amt{0,0} 千元"),
            ("均价",     "@avg_price{0.000} 元"),
            ("换手率",   "@turnover{0.00} %"),
        ],
        formatters={"@date": "datetime", "@date_min": "datetime"},
    ))

        # ---------- 4. 隐藏的 picker 层（框选必须有可选择对象） ----------
    src_picker = ColumnDataSource(dict(
        x=dates,
        y=[y_max * 0.5] * len(dates),
        date=dates,
    ))
    picker = fig.scatter(
        x="x", y="y", source=src_picker,
        size=1,
        fill_alpha=0.0, line_alpha=0.0,
        fill_color="#000000", line_color="#000000",
        name="picker",
    )
    box_sel_tool.renderers = [picker]

    # ---------- 5. 默认左键拖动 ----------
    fig.toolbar.active_drag = pan_tool

    # ---------- 6. 框选事件监听 ----------
    if on_range_select is not None:
        def _on_box_select_event(event):
            geom = event.geometry
            if not geom or geom.get("type") != "rect":
                return
            x0 = geom.get("x0")
            x1 = geom.get("x1")
            if x0 is None or x1 is None:
                return
            span_ms = abs(x1 - x0)
            days = int(round(span_ms / 86400000.0))
            if days < 1:
                return
            logger.info("box select event | span_ms=%.0f | days=%d", span_ms, days)
            on_range_select(days)
            try:
                src_picker.selected.indices = []
            except Exception:
                pass
            fig.toolbar.active_drag = pan_tool

        fig.on_event(SelectionGeometry, _on_box_select_event)

    fig.legend.orientation = "horizontal"
    fig.legend.location = "top_left"
    fig.legend.click_policy = "hide"
    fig.add_layout(fig.legend[0], "above")
    return fig


# ============================================================
# 图 3：均价折线 + 最高/最低持续天数柱
# ============================================================
def _build_fig3(d: pd.DataFrame, gap: float, x_range: Range1d,code_label: str = ""):
    fig = figure(
        x_range=x_range, height=380,
        title=f"{code_label} ③ 平均价（折线）+ 最高/最低持续天数（柱子）".strip(),
        x_axis_type="datetime",
        tools="pan,box_zoom,reset,xwheel_zoom",
        toolbar_location="above",
    )
    fig.xaxis.axis_label = "日期"
    fig.yaxis.axis_label = "平均价（元）"

    # ---- 左轴：紧贴价格范围 ----
    price_min = float(d["avg_price"].min())
    price_max = float(d["avg_price"].max())
    span = price_max - price_min
    if not np.isfinite(span) or span <= 0:
        span = max(abs(price_max) * 0.01, 0.01)
    pad = span * 0.1
    fig.y_range = Range1d(start=price_min - pad, end=price_max + pad)

    # ---- 右轴：持续天数 ----
    hd_max = float(d["high_days"].max()) if len(d) else 10
    ld_max = float(d["low_days"].max()) if len(d) else 10
    days_max = max(hd_max, ld_max, 10) * 1.1
    fig.extra_y_ranges["days"] = Range1d(start=0, end=days_max)
    fig.add_layout(
        LinearAxis(y_range_name="days", axis_label="持续天数"), "right"
    )

    # ---- 构造共享 CDS ----
    bar_w_ms = gap * 86400000 * 0.35
    offset_ms = gap * 86400000 * 0.20

    xs_h = (d["date"] - pd.Timedelta(milliseconds=offset_ms)).tolist()
    xs_l = (d["date"] + pd.Timedelta(milliseconds=offset_ms)).tolist()

    src = ColumnDataSource(dict(
        # 折线用
        x_line=d["date"].tolist(),
        y_line=d["avg_price"].tolist(),
        # 高柱用
        x_h=xs_h,
        y_h=d["high_days"].tolist(),
        # 低柱用
        x_l=xs_l,
        y_l=d["low_days"].tolist(),
        # hover 公共字段
        date=d["date"].tolist(),
        avg_price=d["avg_price"].tolist(),
        high_days=d["high_days"].tolist(),
        low_days=d["low_days"].tolist(),
    ))

    line_r = fig.line(x="x_line", y="y_line", source=src,
                      color="#1e88e5", line_width=2,
                      legend_label="平均价")
    r_h = fig.vbar(x="x_h", top="y_h", bottom=0, width=bar_w_ms,
                   source=src, color="#e53935", alpha=0.5,
                   y_range_name="days", legend_label="多少天来最高")
    r_l = fig.vbar(x="x_l", top="y_l", bottom=0, width=bar_w_ms,
                   source=src, color="#43a047", alpha=0.5,
                   y_range_name="days", legend_label="多少天来最低")

    # ---- 关键：一个 HoverTool 绑三个 renderer，显示全部信息 ----
    fig.add_tools(HoverTool(
        renderers=[line_r, r_h, r_l],
        tooltips=[
            ("日期",         "@date{%F}"),
            ("均价",         "@avg_price{0.000} 元"),
            ("多少天来最高", "@high_days 天"),
            ("多少天来最低", "@low_days 天"),
        ],
        formatters={"@date": "datetime"},
    ))

    fig.legend.orientation = "horizontal"
    fig.legend.location = "top_left"
    fig.legend.click_policy = "hide"
    fig.add_layout(fig.legend[0], "above")

    return fig


# ============================================================
# 组合：三图共享 x_range
# ============================================================
def build_charts(df: pd.DataFrame, window: int, on_range_select=None, code_label: str = ""):
    d = _prepare_data(df, window)
    if d.empty:
        return None, None, None

    gap = _gap_days(d)
    x_max = d["date"].max()
    x_min = max(d["date"].min(), x_max - pd.Timedelta(days=90))
    x_range = Range1d(start=x_min, end=x_max)

    fig1 = _build_fig1(d, gap, x_range,code_label=code_label)
    fig2 = _build_fig2(d, gap, x_range, on_range_select=on_range_select,code_label=code_label)
    fig3 = _build_fig3(d, gap, x_range,code_label=code_label)
    return fig1, fig2, fig3


# ============================================================
# 工厂函数
# ============================================================
def build_trend():
    def _factory():
        username, role = _get_current_user()
        logger.info("trend session | user=%s | role=%s", username, role)

        symbols = load_user_symbols(username)
        # 归一化 symbols：全部转 str
        symbols = [
            s.decode("utf-8", errors="ignore") if isinstance(s, bytes) else str(s)
            for s in symbols
        ]
        if not symbols:
            return pn.Column(
                pn.pane.Markdown(f"### 👤 {username}"),
                pn.pane.Alert("无可用股票数据", alert_type="warning"),
            )

        # ---------- 读 URL 参数 ----------
        url_params = _read_url_params()
        logger.info("trend url params | %s", url_params)

        _raw = url_params.get("symbol", "")
        if isinstance(_raw, bytes):
            _raw = _raw.decode("utf-8", errors="ignore")
        elif isinstance(_raw, (list, tuple)):
            _raw = _raw[0] if _raw else ""
            if isinstance(_raw, bytes):
                _raw = _raw.decode("utf-8", errors="ignore")
        initial_symbol = str(_raw).strip()
        if initial_symbol:
            logger.info("trend url symbol | %s", initial_symbol)

        # ---------- 控件定义 ----------
        filter_input = pn.widgets.TextInput(
            name="搜索",
            placeholder="输入关键字过滤",
            width=140,
        )
        symbol_select = pn.widgets.Select(
            name="股票代码",
            options=symbols,
            value=None,                 # 初始不选
            width=260,
        )
        window_input = pn.widgets.IntInput(
            name="向前汇总多少天",
            value=0, start=0, end=60, width=160,
        )
        status = pn.pane.Markdown("**请从下拉选择股票代码**", width=320)

        fig1_pane = pn.pane.Bokeh(sizing_mode="stretch_width")
        fig2_pane = pn.pane.Bokeh(sizing_mode="stretch_width")
        fig3_pane = pn.pane.Bokeh(sizing_mode="stretch_width")

        # ---------- 过滤逻辑 ----------
        def on_filter(event):
            kw = (event.new or "").strip().lower()
            filtered = [s for s in symbols if kw in s.lower()] if kw else symbols
            current = symbol_select.value
            symbol_select.options = filtered
            if current not in filtered:
                symbol_select.value = None

        filter_input.param.watch(on_filter, "value")

        # ---------- 更新逻辑 ----------
        def update(symbol, window):
            if not symbol:
                status.object = "**请从下拉选择股票代码**"
                fig1_pane.object = None
                fig2_pane.object = None
                fig3_pane.object = None
                return

            def on_range_select(days: int):
                if days < 1:
                    return
                if days != window_input.value:
                    logger.info("box select → window_input.value = %d", days)
                    window_input.value = days
                    # pn.bind 会自动触发 update()

            try:
                df = load_kline(symbol)
                if df.empty:
                    status.object = f"**{symbol} 无数据**"
                    fig1_pane.object = None
                    fig2_pane.object = None
                    fig3_pane.object = None
                    return
                name = get_stock_name(symbol)
                code_label = f"{symbol} {name}".strip()   # 例如 "600519.SH 贵州茅台"

                fig1, fig2, fig3 = build_charts(
                    df, window, on_range_select=on_range_select,code_label=code_label,
                )
                if fig1 is None:
                    status.object = "**数据不足**"
                    return

                fig1_pane.object = fig1
                fig2_pane.object = fig2
                fig3_pane.object = fig3
                status.object = f"**{symbol}** · 共 {len(df)} 条记录"
                logger.info("trend update | symbol=%s | window=%s | rows=%d",
                            symbol, window, len(df))
            except Exception as e:
                logger.exception("trend update failed")
                status.object = f"**错误：{e}**"

        # ---------- 绑定 ----------
        pn.bind(update, symbol_select, window_input, watch=True)

        # ---------- 应用 URL 参数（必须在控件定义 + pn.bind 之后） ----------
        if initial_symbol:
            resolved = None
            if initial_symbol in symbols:
                resolved = initial_symbol
            else:
                matches = [s for s in symbols
                           if initial_symbol.lower() in s.lower()]
                if matches:
                    resolved = matches[0]
                    logger.info("trend url fuzzy match | %s → %s",
                                initial_symbol, resolved)

            if resolved:
                symbol_select.value = resolved
                logger.info("trend url applied | symbol=%s", resolved)
                # 关键：手动触发一次 update，确保图表立即加载
                update(resolved, window_input.value)
            else:
                logger.warning("trend url symbol not found | %s", initial_symbol)
                status.object = f"**URL 指定的 {initial_symbol} 不在可选列表**"

        # ---------- 布局 ----------
        return pn.Column(
            pn.Row(filter_input, symbol_select, window_input, status,
                   sizing_mode="stretch_width"),
            fig1_pane,
            fig2_pane,
            fig3_pane,
            sizing_mode="stretch_width",
            styles={"padding": "12px"},
        )

    return _factory