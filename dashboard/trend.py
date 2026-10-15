"""趋势探索页面 — 三图联动(成交量/成交额/换手率 + 均价/持续天数)
批量计算：D:/pyprj/stock2/scripts/batch_prepare_trend.py
"""
import io
import logging

import numpy as np
import pandas as pd
import urllib.parse
import panel as pn
from bokeh.plotting import figure
from bokeh.models import (
    ColumnDataSource, Range1d, LinearAxis, HoverTool, PanTool, BoxSelectTool,
    BoxZoomTool, WheelZoomTool, ResetTool,
)
from bokeh.events import SelectionGeometry
from dashboard.auth import sessions
from dashboard.config import settings
from dashboard.db_utils import load_user_symbols, load_kline, get_stock_name

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
def _calc_persistence(values: np.ndarray):
    """计算持续天数 + 起点索引。

    返回：
        low_days        低天数（int64 数组）
        high_days       高天数（int64 数组）
        low_start_idx   低天数对应窗口的起始索引
        high_start_idx  高天数对应窗口的起始索引

    low_days[i]=k 表示从 i-(k-1) 到 i 这 k 天里，values[i] 是最低。
    所以窗口起始索引 = i-(k-1)。
    """
    n = len(values)
    if n == 0:
        empty = np.array([], dtype=np.int64)
        return empty, empty, empty, empty

    idx = np.arange(n)
    lower = idx[:, None] > idx[None, :]

    less_mask = (values[:, None] > values[None, :]) & lower
    last_less = np.where(less_mask, idx[None, :], -1).max(axis=1)
    low_days = (idx - last_less).astype(np.int64)

    greater_mask = (values[:, None] < values[None, :]) & lower
    last_greater = np.where(greater_mask, idx[None, :], -1).max(axis=1)
    high_days = (idx - last_greater).astype(np.int64)

    # 起点索引：last = -1 时表示"从序列首日算起"，否则从 last+1 开始
    low_start_idx = np.where(last_less == -1, 0, last_less + 1)
    high_start_idx = np.where(last_greater == -1, 0, last_greater + 1)

    return low_days, high_days, low_start_idx, high_start_idx


def add_trend_columns(df: pd.DataFrame) -> pd.DataFrame:
    """向量化添加 asc_cnt / desc_cnt / low_days / high_days / 起点日期。

    同时为 close 和 avg_price 两套数列分别计算：
        - low_days_close / high_days_close / low_start_date_close / high_start_date_close
        - low_days_avg   / high_days_avg   / low_start_date_avg   / high_start_date_avg
    """
    df = df.copy()
    close = df["close"].to_numpy(dtype=np.float64)
    dates = df["date"].to_numpy()
    n = len(close)

    if n == 0:
        for c in ("asc_cnt", "desc_cnt",
                  "low_days_close", "high_days_close",
                  "low_start_date_close", "high_start_date_close",
                  "low_days_avg", "high_days_avg",
                  "low_start_date_avg", "high_start_date_avg"):
            df[c] = pd.Series(dtype=object)
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

    # ---- 基于 close ----
    low_c, high_c, low_start_c, high_start_c = _calc_persistence(close)
    df["low_days_close"] = low_c
    df["high_days_close"] = high_c
    df["low_start_date_close"] = dates[low_start_c]
    df["high_start_date_close"] = dates[high_start_c]

    # ---- 基于 avg_price ----
    if "avg_price" in df.columns:
        avg = df["avg_price"].to_numpy(dtype=np.float64)
        # 前几期可能有 NaN，用 ffill/bfill 补齐后再比较
        avg_clean = pd.Series(avg).ffill().bfill().to_numpy(dtype=np.float64)
        low_a, high_a, low_start_a, high_start_a = _calc_persistence(avg_clean)
        df["low_days_avg"] = low_a
        df["high_days_avg"] = high_a
        df["low_start_date_avg"] = dates[low_start_a]
        df["high_start_date_avg"] = dates[high_start_a]
    else:
        df["low_days_avg"] = 0
        df["high_days_avg"] = 0
        df["low_start_date_avg"] = pd.NaT
        df["high_start_date_avg"] = pd.NaT

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
    """主流程：确保 date 列、加派生列、排序、类型转换。

    ★ 顺序调整说明：
        必须先用 add_rolling_metrics 算出 avg_price，
        再用 add_trend_columns 计算基于 avg_price 的持续天数。
    """
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

    # ★ 先 rolling（算 avg_price），再 trend（算持续天数 + 起点日期）
    d = add_rolling_metrics(d, window)
    d = add_trend_columns(d)
    return d


# ============================================================
# Excel 导出
# ============================================================
EXPORT_COLS = [
    "date", "open", "high", "low", "close", "volume", "amount",
    "float_share", "asc_cnt", "desc_cnt", "low_days", "high_days",
    "sum_vol", "sum_amt", "date_min", "avg_price", "turnover_pct",
]


def _make_export_bytes(symbol: str, window: int, price_type: str) -> io.BytesIO:
    """生成 xlsx 内容（BytesIO）。"""
    if not symbol:
        return io.BytesIO(b"")
    try:
        df = load_kline(symbol)
        if df.empty:
            return io.BytesIO(b"")
        d = _prepare_data(df, window)
    except Exception as e:
        logger.exception("export prepare failed | %s", symbol)
        return io.BytesIO(b"")

    # 根据 price_type 统一成 low_days / high_days 两列
    if price_type == "close":
        d["low_days"] = d["low_days_close"]
        d["high_days"] = d["high_days_close"]
    else:
        d["low_days"] = d["low_days_avg"]
        d["high_days"] = d["high_days_avg"]

    cols = [c for c in EXPORT_COLS if c in d.columns]
    out = d[cols].copy()

    # 日期列转字符串，Excel 里好看
    for c in ("date", "date_min"):
        if c in out.columns:
            out[c] = pd.to_datetime(out[c]).dt.strftime("%Y-%m-%d")

    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        out.to_excel(w, index=False, sheet_name="trend")
    bio.seek(0)
    return bio


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
def _build_fig2(d: pd.DataFrame, gap: float, x_range: Range1d,
                on_range_select=None, code_label: str = ""):
    """三根柱子从 0 开始。

    归一化策略：
        成交量、成交额 → 各自独立归一到 [0, 1]（左轴）
        换手率         → 保留原始百分比（右轴）
    这样每根柱子的高度都反映了"该指标在其自身序列中的相对位置"，
    且图 1、图 2 视觉一致。

    ★ hover 改动：三个柱子共用同一个 CDS，
                 一个 HoverTool 绑三个 renderer，
                 显示信息一致（当前日期、起始日期、成交量、成交额、换手率）。
    """
    pan_tool = PanTool(dimensions="width")
    box_sel_tool = BoxSelectTool(dimensions="width")
    box_zoom_tool = BoxZoomTool(dimensions="width")
    wheel_zoom_tool = WheelZoomTool(dimensions="width")
    reset_tool = ResetTool()

    title = f"{code_label} ② 成交量 / 成交额（相对强度）+ 换手率".strip()

    fig = figure(
        x_range=x_range, height=380,
        title=title,
        x_axis_type="datetime",
        tools=[pan_tool, box_sel_tool, box_zoom_tool, wheel_zoom_tool, reset_tool],
        toolbar_location="above",
    )
    fig.xaxis.axis_label = "日期"
    fig.yaxis.axis_label = "相对强度 (0~1)"

    # ---------- 归一化基准 ----------
    max_vol = _norm(d["sum_vol"].to_numpy())
    max_amt = _norm(d["sum_amt"].to_numpy())
    max_to = _norm(d["turnover_pct"].to_numpy())

    # ---------- 左轴：0~1 ----------
    y_hi = 1.05
    fig.y_range = Range1d(start=0, end=y_hi, bounds=(0, y_hi))

    # ---------- 右轴：换手率 % ----------
    to_hi = max_to * 1.1
    if not np.isfinite(to_hi) or to_hi <= 0:
        to_hi = 10.0
    fig.extra_y_ranges["pct"] = Range1d(start=0, end=to_hi, bounds=(0, to_hi))
    fig.add_layout(
        LinearAxis(y_range_name="pct", axis_label="换手率（%）"), "right"
    )

    bar_w_ms = gap * 86400000 * 0.25
    offset_ms = gap * 86400000 * 0.30

    # ---------- 数据准备 ----------
    xs_vol, xs_amt, xs_to = [], [], []
    v_norm, a_norm, t_raw = [], [], []
    dates, date_mins = [], []
    v_raw_list, a_raw_list = [], []

    for _, row in d.iterrows():
        date_i = row["date"]

        v_raw = row["sum_vol"] if np.isfinite(row["sum_vol"]) else 0.0
        a_raw = row["sum_amt"] if np.isfinite(row["sum_amt"]) else 0.0
        t_raw_v = row["turnover_pct"] if np.isfinite(row["turnover_pct"]) else 0.0

        v = v_raw / max_vol if max_vol > 0 else 0.0
        a = a_raw / max_amt if max_amt > 0 else 0.0

        xs_vol.append(date_i - pd.Timedelta(milliseconds=offset_ms))
        xs_amt.append(date_i)
        xs_to.append(date_i + pd.Timedelta(milliseconds=offset_ms))
        v_norm.append(v)
        a_norm.append(a)
        t_raw.append(t_raw_v)
        v_raw_list.append(v_raw)
        a_raw_list.append(a_raw)
        dates.append(date_i)
        date_mins.append(row["date_min"])

    # ★ 三个柱子共用一个 CDS
    src = ColumnDataSource(dict(
        x_vol=xs_vol, y_vol=v_norm,
        x_amt=xs_amt, y_amt=a_norm,
        x_to=xs_to, y_to=t_raw,
        date=dates, date_min=date_mins,
        sum_vol=v_raw_list, sum_amt=a_raw_list, turnover=t_raw,
    ))

    # ---------- 柱 ----------
    r_vol = fig.vbar(
        x="x_vol", top="y_vol", bottom=0, width=bar_w_ms,
        source=src, color="#1e88e5", alpha=0.75,
        legend_label="成交量（相对强度）",
    )
    r_amt = fig.vbar(
        x="x_amt", top="y_amt", bottom=0, width=bar_w_ms,
        source=src, color="#43a047", alpha=0.75,
        legend_label="成交额（相对强度）",
    )
    r_to = fig.vbar(
        x="x_to", top="y_to", bottom=0, width=bar_w_ms,
        source=src, color="#fb8c00", alpha=0.75,
        y_range_name="pct",
        legend_label="换手率（%）",
    )

    # ---------- 统一 Hover：三个柱子显示一致的信息 ----------
    fig.add_tools(HoverTool(
        renderers=[r_vol, r_amt, r_to],
        tooltips=[
            ("当前日期", "@date{%F}"),
            ("起始日期", "@date_min{%F}"),
            ("成交量",   "@sum_vol{0,0} 手"),
            ("成交额",   "@sum_amt{0,0} 千元"),
            ("换手率",   "@turnover{0.00} %"),
        ],
        formatters={"@date": "datetime", "@date_min": "datetime"},
    ))

    # ---------- 隐藏 picker 层（框选用） ----------
    src_picker = ColumnDataSource(dict(
        x=dates,
        y=[0.5] * len(dates),
        date=dates,
    ))
    picker = fig.scatter(
        x="x", y="y", source=src_picker,
        size=1, fill_alpha=0.0, line_alpha=0.0,
        fill_color="#000000", line_color="#000000",
        name="picker",
    )
    box_sel_tool.renderers = [picker]
    fig.toolbar.active_drag = pan_tool

    # ---------- 框选回调 ----------
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
            logger.info("box select event | span_ms=%.0f | days=%d",
                        span_ms, days)
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
# 图 3：价格折线 + 最高/最低持续天数柱
# ============================================================
def _build_fig3(d: pd.DataFrame, gap: float, x_range: Range1d,
                price_type: str = "avg", code_label: str = ""):
    """价格折线 + 持续天数柱。

    参数：
        price_type: "avg"   → 折线显示 avg_price，柱显示 low/high_days_avg
                    "close" → 折线显示 close，柱显示 low/high_days_close
        code_label: 图表标题前缀（代码 + 名称）
    """
    if price_type == "close":
        price_col = "close"
        low_col = "low_days_close"
        high_col = "high_days_close"
        low_start_col = "low_start_date_close"
        high_start_col = "high_start_date_close"
        price_label = "收盘价"
    else:
        price_col = "avg_price"
        low_col = "low_days_avg"
        high_col = "high_days_avg"
        low_start_col = "low_start_date_avg"
        high_start_col = "high_start_date_avg"
        price_label = "平均价"

    fig = figure(
        x_range=x_range, height=380,
        title=f"{code_label} ③ {price_label}（折线）+ 最高/最低持续天数（柱子）".strip(),
        x_axis_type="datetime",
        tools="pan,box_zoom,reset,xwheel_zoom",
        toolbar_location="above",
    )
    fig.xaxis.axis_label = "日期"
    fig.yaxis.axis_label = f"{price_label}（元）"

    # ---- 左轴：紧贴价格范围 ----
    p_series = d[price_col]
    price_min = float(p_series.min())
    price_max = float(p_series.max())
    span = price_max - price_min
    if not np.isfinite(span) or span <= 0:
        span = max(abs(price_max) * 0.01, 0.01)
    pad = span * 0.1
    fig.y_range = Range1d(start=price_min - pad, end=price_max + pad)

    # ---- 右轴：持续天数 ----
    hd_max = float(d[high_col].max()) if len(d) else 10
    ld_max = float(d[low_col].max()) if len(d) else 10
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
        y_line=d[price_col].tolist(),
        # 高柱用
        x_h=xs_h,
        y_h=d[high_col].tolist(),
        # 低柱用
        x_l=xs_l,
        y_l=d[low_col].tolist(),
        # hover 公共字段（两个价格都带）
        date=d["date"].tolist(),
        avg_price=d["avg_price"].tolist(),
        close=d["close"].tolist(),
        high_days=d[high_col].tolist(),
        low_days=d[low_col].tolist(),
        # ★ 起点日期：转成字符串，避免 Bokeh 序列化成时间戳
        low_start_date=pd.to_datetime(d[low_start_col]).dt.strftime(
            "%Y-%m-%d"
        ).tolist(),
        high_start_date=pd.to_datetime(d[high_start_col]).dt.strftime(
            "%Y-%m-%d"
        ).tolist(),
    ))

    line_r = fig.line(x="x_line", y="y_line", source=src,
                      color="#1e88e5", line_width=2,
                      legend_label=price_label)
    r_h = fig.vbar(x="x_h", top="y_h", bottom=0, width=bar_w_ms,
                   source=src, color="#e53935", alpha=0.5,
                   y_range_name="days",
                   legend_label=f"{price_label}多少天来最高")
    r_l = fig.vbar(x="x_l", top="y_l", bottom=0, width=bar_w_ms,
                   source=src, color="#43a047", alpha=0.5,
                   y_range_name="days",
                   legend_label=f"{price_label}多少天来最低")

    # ---- HoverTool：起点日期直接用字符串显示 ----
    fig.add_tools(HoverTool(
        renderers=[line_r, r_h, r_l],
        tooltips=[
            ("日期",         "@date{%F}"),
            ("均价",         "@avg_price{0.000} 元"),
            ("收盘价",       "@close{0.000} 元"),
            ("多少天来最高", "@{high_days} 天(自 @{high_start_date})"),
            ("多少天来最低", "@{low_days} 天(自 @{low_start_date})"),
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
def build_charts(df: pd.DataFrame, window: int, on_range_select=None,
                 code_label: str = "", price_type: str = "avg"):
    d = _prepare_data(df, window)
    if d.empty:
        return None, None, None

    gap = _gap_days(d)
    x_max = d["date"].max()
    x_min = max(d["date"].min(), x_max - pd.Timedelta(days=90))
    x_range = Range1d(start=x_min, end=x_max)

    fig1 = _build_fig1(d, gap, x_range, code_label=code_label)
    fig2 = _build_fig2(d, gap, x_range, on_range_select=on_range_select,
                       code_label=code_label)
    fig3 = _build_fig3(d, gap, x_range, price_type=price_type,
                       code_label=code_label)
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
        status = pn.pane.Markdown("**请从下拉选择股票代码**", width=260)

        # ★ 图 3 专用：价格类型互斥单选框（只影响第三幅图）
        price_type_select = pn.widgets.RadioButtonGroup(
            name="",
            options={"平均价": "avg", "收盘价": "close"},
            value="avg",
            button_type="default",
            width=180,
        )

        # ★ 导出按钮：把整个后台 df 导成 xlsx
        export_btn = pn.widgets.FileDownload(
            callback=lambda: _make_export_bytes(
                symbol_select.value,
                window_input.value,
                price_type_select.value,
            ),
            filename="trend_export.xlsx",
            label="📥 导出 XLSX",
            button_type="default",
            width=130,
        )

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
        def update(symbol, window, price_type):
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
                code_label = f"{symbol} {name}".strip()

                fig1, fig2, fig3 = build_charts(
                    df, window,
                    on_range_select=on_range_select,
                    code_label=code_label,
                    price_type=price_type,
                )
                if fig1 is None:
                    status.object = "**数据不足**"
                    return

                fig1_pane.object = fig1
                fig2_pane.object = fig2
                fig3_pane.object = fig3
                status.object = f"**{symbol}** · 共 {len(df)} 条记录"
                logger.info("trend update | symbol=%s | window=%s | price=%s | rows=%d",
                            symbol, window, price_type, len(df))
            except Exception as e:
                logger.exception("trend update failed")
                status.object = f"**错误：{e}**"

        # ---------- 绑定 ----------
        pn.bind(update, symbol_select, window_input, price_type_select,
                watch=True)

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
                update(resolved, window_input.value, price_type_select.value)
            else:
                logger.warning("trend url symbol not found | %s", initial_symbol)
                status.object = f"**URL 指定的 {initial_symbol} 不在可选列表**"

        # ---------- 布局 ----------
        # 说明：price_type_select 只控制图 3，因此放在图 3 上方、紧贴图 3，
        #       不放到顶部工具栏。
        return pn.Column(
            pn.Row(
                filter_input,
                symbol_select,
                window_input,
                export_btn,
                status,
                sizing_mode="stretch_width",
            ),
            fig1_pane,
            fig2_pane,
            # ↓↓↓ 图 3 的专用切换栏（单行，紧贴图 3，不占图像高度）
            pn.Row(
                pn.pane.Markdown("**③ 折线/柱子的价格类型：**", width=180),
                price_type_select,
                sizing_mode="stretch_width",
            ),
            fig3_pane,
            sizing_mode="stretch_width",
            styles={"padding": "12px"},
        )

    return _factory


if __name__ == "__main__":
    symbol = '300748.SZ'
    df = load_kline(symbol)
    df2 = _prepare_data(df=df, window=20)
    df2.to_pickle('300748.pkl')