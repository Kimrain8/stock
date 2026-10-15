"""数据工作室 — SQL + Panel 原生图表配置 + 可拖拽仪表板"""
import logging
import os
import pickle
from decimal import Decimal

import numpy as np
import pandas as pd
import panel as pn
from bokeh.plotting import figure
from bokeh.models import (
    ColumnDataSource, LinearAxis, DataRange1d, HoverTool,
)
from bokeh.palettes import (
    Category10, Set1, Set2, Dark2, Paired, Spectral11,
    Viridis256, Plasma256, Inferno256, Magma256, Cividis256,
)

try:
    from bokeh.palettes import Turbo256
except ImportError:
    Turbo256 = None

from utils.dbManager import MySQLHelper
from .auth import sessions
from .config import settings

logger = logging.getLogger("stock2.data_studio")

pn.extension("tabulator", "codeeditor", sizing_mode="stretch_width")

try:
    from panel_tiles import TileGrid
    HAS_TILES = True
except ImportError:
    HAS_TILES = False
    logger.warning("panel-tiles 未安装，仪表板将使用简单 Column 布局")

MAX_ROWS_DEFAULT = 100000

# ============================================================
# 通用样式
# ============================================================
SMALL_SELECT_STYLE = {"font-size": "12px"}
SMALL_SELECT_MARGIN = (0, 2, 0, 0)


# ============================================================
# 色系
# ============================================================
PALETTE_OPTIONS = [
    "Category10", "Set1", "Set2", "Dark2", "Paired", "Spectral",
    "Viridis", "Plasma", "Inferno", "Magma", "Cividis", "Turbo",
]

_BIG_PALETTES = {
    "Viridis": Viridis256,
    "Plasma": Plasma256,
    "Inferno": Inferno256,
    "Magma": Magma256,
    "Cividis": Cividis256,
    "Turbo": Turbo256,
}

PALETTE_SIZE = 20


def _sample_palette(name: str, n: int = PALETTE_SIZE):
    if name == "Set1":
        cols = list(Set1[9])
    elif name == "Set2":
        cols = list(Set2[8])
    elif name == "Dark2":
        cols = list(Dark2[8])
    elif name == "Paired":
        cols = list(Paired[12])
    elif name == "Spectral":
        cols = list(Spectral11)
    elif name in _BIG_PALETTES:
        big = _BIG_PALETTES[name]
        if big and len(big) > 1:
            step = max(1, (len(big) - 1) // max(1, n - 1))
            cols = [big[min(i * step, len(big) - 1)] for i in range(n)]
        else:
            cols = list(Category10[10])
    else:
        cols = list(Category10[10])
    if not cols:
        cols = list(Category10[10])
    return cols


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
# 日期解析
# ============================================================
def _safe_to_datetime(s: pd.Series):
    if s is None or len(s) == 0:
        return None
    try:
        if pd.api.types.is_datetime64_any_dtype(s):
            return s
        if pd.api.types.is_integer_dtype(s) or pd.api.types.is_float_dtype(s):
            sample = s.dropna()
            if len(sample) == 0:
                return None
            v = float(sample.iloc[0])
            if 19000101 <= v <= 21001231:
                return pd.to_datetime(s.astype("Int64").astype(str),
                                      format="%Y%m%d", errors="coerce")
            if v < 1e10:
                return pd.to_datetime(s, unit="s", errors="coerce")
            if v < 1e13:
                return pd.to_datetime(s, unit="ms", errors="coerce")
            if v < 1e16:
                return pd.to_datetime(s, unit="us", errors="coerce")
            return pd.to_datetime(s, unit="ns", errors="coerce")
        r = pd.to_datetime(s, errors="coerce")
        if r.notna().sum() > 0:
            return r
        return None
    except Exception:
        return None


def _looks_like_datetime_col(col) -> bool:
    try:
        c = str(col or "").lower()
    except Exception:
        return False
    return "date" in c or "time" in c or c.endswith("_dt")


# ============================================================
# DataFrame 清洗 + 列名归一化
# ============================================================
def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    new_cols = []
    seen = {}
    for c in d.columns:
        name = str(c)
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        new_cols.append(name)
    d.columns = new_cols
    return d


def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    d = _normalize_columns(df)

    for col, dtype in d.dtypes.items():
        try:
            s = d[col]
        except Exception:
            continue
        if isinstance(s, pd.DataFrame):
            continue
        if dtype == object:
            sample = s.dropna().head(5)
            if len(sample) == 0:
                continue
            first = sample.iloc[0]
            if isinstance(first, Decimal):
                try:
                    d[col] = s.apply(lambda x: float(x) if isinstance(x, Decimal)
                                     else (np.nan if pd.isna(x) else x))
                    continue
                except Exception:
                    pass
            if hasattr(first, "tzinfo") and first.tzinfo is not None:
                try:
                    d[col] = pd.to_datetime(s, errors="coerce").dt.tz_localize(None)
                    continue
                except Exception:
                    pass
            if not _looks_like_datetime_col(col):
                try:
                    d[col] = pd.to_numeric(s, errors="raise")
                    continue
                except Exception:
                    pass
            try:
                d[col] = s.apply(lambda x: x
                                 if isinstance(x, (str, int, float, bool, type(None)))
                                 else (np.nan if pd.isna(x) else str(x)))
            except Exception:
                pass

    for col in d.columns:
        try:
            s = d[col]
            if s.dtype == object and len(s.dropna()) > 0:
                first = s.dropna().iloc[0]
                if hasattr(first, "year") and hasattr(first, "month") \
                        and hasattr(first, "day") and not isinstance(first, str):
                    try:
                        d[col] = pd.to_datetime(s, errors="coerce")
                    except Exception:
                        pass
        except Exception:
            continue

    return d


# ============================================================
# 数据库
# ============================================================
def _db() -> MySQLHelper:
    return MySQLHelper(db=settings.DB_NAME)


def _exec_sql(sql: str, max_rows: int = MAX_ROWS_DEFAULT) -> pd.DataFrame:
    import re as _re
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return pd.DataFrame()
    head = sql.lstrip().upper()
    if not head.startswith(("SELECT", "SHOW", "DESC", "EXPLAIN")):
        raise ValueError("只支持 SELECT / SHOW / DESC / EXPLAIN 语句")
    if head.startswith("SELECT") and not _re.search(r"\blimit\b", sql, _re.IGNORECASE):
        sql = f"{sql} LIMIT {int(max_rows)}"
    df = _db().qry2df(sql=sql)
    if df is None:
        return pd.DataFrame()
    return _sanitize_df(df)


def _list_tables() -> list:
    try:
        df = _db().qry2df(sql="SHOW TABLES")
        if df is None or df.empty:
            return []
        return sorted(df.iloc[:, 0].astype(str).tolist())
    except Exception as e:
        logger.warning("list tables failed | %s", e)
        return []


# ============================================================
# 从 pkl 文件加载 DataFrame
# ============================================================
def _load_dataframe_from_pkl(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    with open(path, "rb") as f:
        obj = pickle.load(f)

    if isinstance(obj, pd.DataFrame):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, pd.DataFrame):
                return v
        raise ValueError("pkl 为 dict，但内部没有 DataFrame")
    if isinstance(obj, (list, tuple)):
        for v in obj:
            if isinstance(v, pd.DataFrame):
                return v
        raise ValueError("pkl 为 list/tuple，但内部没有 DataFrame")
    raise ValueError(f"pkl 内容不是 DataFrame：{type(obj).__name__}")


# ============================================================
# 聚合
# ============================================================
AGG_OPS = ["(空)", "sum", "avg", "max", "min", "count", "ucount"]
_FUNC_MAP = {"sum": "sum", "avg": "mean", "max": "max",
             "min": "min", "count": "count", "ucount": "nunique"}


def _apply_aggregation(df: pd.DataFrame, specs: list) -> pd.DataFrame:
    group_keys, agg_ops, drop_cols = [], {}, set()
    for c, op in specs:
        if op == "(空)":
            drop_cols.add(c)
        elif op == c:
            group_keys.append(c)
        elif op in _FUNC_MAP:
            agg_ops[c] = op
        else:
            group_keys.append(c)

    if not agg_ops:
        keep = [c for c in df.columns if c not in drop_cols]
        return df[keep].copy().reset_index(drop=True)

    work = df.copy()
    vcols = {}
    for c, op in agg_ops.items():
        if op in ("sum", "avg", "max", "min"):
            work[f"__v_{c}__"] = pd.to_numeric(df[c], errors="coerce").values
        else:
            work[f"__v_{c}__"] = df[c].values
        vcols[c] = f"__v_{c}__"

    if not group_keys:
        work["__g__"] = 0
        grouped = work.groupby("__g__", sort=True)
    else:
        grouped = work.groupby(group_keys, dropna=False, sort=True)

    out = {c: grouped[vcols[c]].agg(_FUNC_MAP[op]) for c, op in agg_ops.items()}
    res = pd.DataFrame(out)
    if group_keys:
        res = res.reset_index()
    return res


# ============================================================
# 公共工具
# ============================================================
RIGHT_COLOR = "#ff9800"
CHART_TYPES = ["无", "折线图", "柱状图", "散点图", "面积图"]


def _prepare_x(d: pd.DataFrame, x_col: str):
    is_dt = False
    if _looks_like_datetime_col(x_col):
        dt = _safe_to_datetime(d[x_col])
        if dt is not None and dt.notna().sum() > 0:
            d = d.copy()
            d[x_col] = dt
            d = d.dropna(subset=[x_col]).sort_values(x_col).reset_index(drop=True)
            is_dt = True
    if is_dt:
        has_time = bool((d[x_col].dt.hour != 0).any() or (d[x_col].dt.minute != 0).any())
        fmt = "%Y-%m-%d %H:%M" if has_time else "%Y-%m-%d"
        d["_x_label"] = d[x_col].dt.strftime(fmt)
    else:
        d["_x_label"] = d[x_col].astype(str)
    return d, is_dt


def _dt_to_ms(s: pd.Series) -> pd.Series:
    epoch = pd.Timestamp("1970-01-01")
    return (s - epoch) / pd.Timedelta("1ms")


# ============================================================
# 主图构建
# ============================================================
def _build_axis_figure(df: pd.DataFrame, x_col: str,
                       left_cfg: dict, right_cfg: dict, hidden_cfg: dict,
                       height: int = 500):
    d = df.copy()
    if d.empty or x_col not in d.columns:
        return None

    def _valid_rows(cfg):
        out = []
        for r in cfg.get("rows") or []:
            v = r.get("var")
            if v and v in d.columns:
                out.append({
                    "var": v,
                    "color": r.get("color") if r.get("color") not in (None, "", "(无)") else None,
                    "size": r.get("size") if r.get("size") not in (None, "", "(无)") else None,
                })
        return out

    left_rows = _valid_rows(left_cfg)
    right_rows = _valid_rows(right_cfg)
    hidden_rows = _valid_rows(hidden_cfg)
    left_type = left_cfg.get("type") or "无"
    right_type = right_cfg.get("type") or "无"
    hidden_type = hidden_cfg.get("type") or "无"

    if left_type == "无":
        left_rows = []
    if right_type == "无":
        right_rows = []
    if hidden_type == "无":
        hidden_rows = []

    if not (left_rows or right_rows or hidden_rows):
        return None

    d, is_dt = _prepare_x(d, x_col)
    if d.empty:
        return None

    series_defs = []

    def _collect(rows, chart_type, axis_key):
        for row in rows:
            yc = row["var"]
            cf = row.get("color")
            sf = row.get("size")
            ys = pd.to_numeric(d[yc], errors="coerce")
            if cf and cf in d.columns:
                groups = list(d[cf].astype(str).unique())[:10]
                for g in groups:
                    mask = ys.notna() & (d[cf].astype(str) == g)
                    idxs = d.index[mask].tolist()
                    if idxs:
                        series_defs.append({
                            "axis_key": axis_key, "chart_type": chart_type,
                            "var": yc, "group": g,
                            "color_field": cf, "size_field": sf,
                            "indices": idxs,
                        })
            else:
                idxs = d.index[ys.notna()].tolist()
                if idxs:
                    series_defs.append({
                        "axis_key": axis_key, "chart_type": chart_type,
                        "var": yc, "group": None,
                        "color_field": None, "size_field": sf,
                        "indices": idxs,
                    })

    _collect(left_rows, left_type, "left")
    _collect(right_rows, right_type, "right")
    _collect(hidden_rows, hidden_type, "hidden")

    if not series_defs:
        return None

    title_parts = []
    if left_rows:
        title_parts.append("左:" + ",".join(r["var"] for r in left_rows))
    if right_rows:
        title_parts.append("右:" + ",".join(r["var"] for r in right_rows))
    if hidden_rows:
        title_parts.append("隐:" + ",".join(r["var"] for r in hidden_rows))
    title = " | ".join(title_parts) + f"  vs  {x_col}"

    fig = figure(
        height=height, title=title,
        x_axis_type="datetime" if is_dt else "auto",
        tools="pan,box_zoom,reset,xwheel_zoom",
        toolbar_location="above",
    )
    fig.grid.grid_line_alpha = 0.3

    if right_rows:
        fig.extra_y_ranges["right"] = DataRange1d()
        ax2 = LinearAxis(y_range_name="right",
                         axis_label=", ".join(r["var"] for r in right_rows[:3]))
        ax2.axis_label_text_color = RIGHT_COLOR
        ax2.major_label_text_color = RIGHT_COLOR
        fig.add_layout(ax2, "right")

    if hidden_rows:
        fig.extra_y_ranges["hidden"] = DataRange1d()
        hax = LinearAxis(y_range_name="hidden")
        hax.axis_line_alpha = 0
        hax.major_tick_line_alpha = 0
        hax.minor_tick_line_alpha = 0
        hax.major_label_text_alpha = 0
        hax.axis_label_text_alpha = 0
        fig.add_layout(hax, "right")

    if left_rows:
        fig.yaxis.axis_label = ", ".join(r["var"] for r in left_rows[:3])

    if is_dt:
        uniq = pd.Series(d[x_col]).drop_duplicates().sort_values()
        if len(uniq) >= 2:
            diffs = uniq.diff().dropna().dt.total_seconds() * 1000.0
            gap = float(diffs.median()) if len(diffs) else 86400000.0
        else:
            gap = 86400000.0
        if not np.isfinite(gap) or gap <= 0:
            gap = 86400000.0
        base_bar_w = gap * 0.7
    else:
        base_bar_w = 0.7

    bar_counts = {"left": 0, "right": 0, "hidden": 0}
    for sd in series_defs:
        if sd["chart_type"] == "柱状图":
            bar_counts[sd["axis_key"]] += 1
    bar_idx = {"left": 0, "right": 0, "hidden": 0}

    axis_palettes = {
        "left":   _sample_palette(left_cfg.get("palette") or "Category10"),
        "right":  _sample_palette(right_cfg.get("palette") or "Category10"),
        "hidden": _sample_palette(hidden_cfg.get("palette") or "Category10"),
    }
    axis_color_seq = {"left": 0, "right": 0, "hidden": 0}

    def _next_color(axis_key):
        palette = axis_palettes[axis_key]
        i = axis_color_seq[axis_key]
        axis_color_seq[axis_key] = i + 1
        return palette[i % len(palette)]

    axis_renderers = {"left": [], "right": [], "hidden": []}
    all_renderers = []

    for sd in series_defs:
        axis_key = sd["axis_key"]
        chart_type = sd["chart_type"]
        yc = sd["var"]
        cf = sd["color_field"]
        sf = sd["size_field"]

        sub = d.loc[sd["indices"]].sort_values(x_col).reset_index(drop=True)
        sub = sub.copy()
        sub["_y"] = pd.to_numeric(sub[yc], errors="coerce").values
        sub["_x_label"] = sub["_x_label"].astype(str).values

        color = _next_color(axis_key)

        if sd["group"] is not None:
            legend = f"{yc}[{sd['group']}]"
        else:
            legend = yc

        sub["_series_name"] = [legend] * len(sub)
        if cf and cf in sub.columns:
            sub["_color_label"] = sub[cf].astype(str).values
        else:
            sub["_color_label"] = ["-"] * len(sub)
        if sf and sf in sub.columns:
            sv = pd.to_numeric(sub[sf], errors="coerce")
            sub["_size_val"] = sv.apply(
                lambda v: f"{v:.2f}" if pd.notna(v) else "-").values
        else:
            sub["_size_val"] = ["-"] * len(sub)

        y_range_name = None if axis_key == "left" else axis_key
        kw = {"y_range_name": y_range_name} if y_range_name else {}

        src = ColumnDataSource(sub)
        renderer = None

        if chart_type == "折线图":
            renderer = fig.line(x=x_col, y="_y", source=src, line_width=2,
                                color=color, legend_label=legend, **kw)

        elif chart_type == "柱状图":
            if is_dt:
                xb = _dt_to_ms(sub[x_col]).astype(float).values
            else:
                xb = np.arange(len(sub), dtype=float)
            n_bars = bar_counts[axis_key]
            if n_bars > 1:
                bw = base_bar_w / n_bars
                offset = (bar_idx[axis_key] - (n_bars - 1) / 2.0) * bw
                bar_idx[axis_key] += 1
                src.data["_x_off"] = xb + offset
                renderer = fig.vbar(x="_x_off", top="_y", width=bw * 0.85,
                                    source=src, fill_color=color, line_color=color,
                                    alpha=0.9, legend_label=legend, **kw)
            else:
                src.data["_x_off"] = xb
                renderer = fig.vbar(x="_x_off", top="_y",
                                    width=base_bar_w * 0.6, source=src,
                                    fill_color=color, line_color=color,
                                    alpha=0.9, legend_label=legend, **kw)

        elif chart_type == "散点图":
            if sf and sf in sub.columns:
                sv = pd.to_numeric(sub[sf], errors="coerce")
                if sv.notna().any():
                    smin, smax = float(sv.min()), float(sv.max())
                    if smax > smin:
                        vals = 4 + (sv - smin) / (smax - smin) * 16
                        vals = vals.fillna(8.0)
                    else:
                        vals = pd.Series([8.0] * len(sub))
                    src.data["_size_plot"] = vals.values.astype(float)
                else:
                    src.data["_size_plot"] = np.full(len(sub), 8.0)
                renderer = fig.scatter(x=x_col, y="_y", source=src,
                                       size="_size_plot", color=color,
                                       legend_label=legend, **kw)
            else:
                renderer = fig.scatter(x=x_col, y="_y", source=src, size=8,
                                       color=color, legend_label=legend, **kw)

        elif chart_type == "面积图":
            renderer = fig.line(x=x_col, y="_y", source=src, line_width=2,
                                color=color, legend_label=legend, **kw)
            fig.varea(x=x_col, y1=0, y2="_y", source=src,
                      fill_alpha=0.18, fill_color=color, **kw)

        if renderer is not None:
            all_renderers.append(renderer)
            axis_renderers[axis_key].append(renderer)

    if not all_renderers:
        return None

    if axis_renderers["left"]:
        try:
            if isinstance(fig.y_range, DataRange1d):
                fig.y_range.renderers = axis_renderers["left"]
            else:
                fig.y_range = DataRange1d(renderers=axis_renderers["left"])
        except Exception:
            logger.exception("bind left y_range failed")

    if axis_renderers["right"] and "right" in fig.extra_y_ranges:
        try:
            rng = fig.extra_y_ranges["right"]
            if isinstance(rng, DataRange1d):
                rng.renderers = axis_renderers["right"]
            else:
                fig.extra_y_ranges["right"] = DataRange1d(
                    renderers=axis_renderers["right"])
        except Exception:
            logger.exception("bind right y_range failed")

    if axis_renderers["hidden"] and "hidden" in fig.extra_y_ranges:
        try:
            rng = fig.extra_y_ranges["hidden"]
            if isinstance(rng, DataRange1d):
                rng.renderers = axis_renderers["hidden"]
            else:
                fig.extra_y_ranges["hidden"] = DataRange1d(
                    renderers=axis_renderers["hidden"])
        except Exception:
            logger.exception("bind hidden y_range failed")

    if fig.legend:
        fig.legend.orientation = "horizontal"
        fig.legend.location = "top_left"
        fig.legend.click_policy = "hide"
        try:
            fig.add_layout(fig.legend[0], "above")
        except Exception:
            pass

    tooltips = [
        (x_col, "@_x_label"),
        ("系列", "@_series_name"),
        ("值", "@_y{0,0.00}"),
        ("颜色", "@_color_label"),
        ("大小", "@_size_val"),
    ]
    hover = HoverTool(tooltips=tooltips, mode="mouse")
    hover.renderers = all_renderers
    fig.add_tools(hover)

    return fig


# ============================================================
# 工厂函数
# ============================================================
def build_data_studio():
    def _factory():
        username, role = _get_current_user()
        logger.info("data_studio session | user=%s | role=%s", username, role)

        state = {
            "df_raw": pd.DataFrame(),
            "df": pd.DataFrame(),
            "tile_objects": [],
            "tile_layout": [],
        }

        # ---------- SQL 编辑器 ----------
        sql_editor = pn.widgets.CodeEditor(
            value=(
                "SELECT ts_code, trade_date, open_price, high, low, close_price, vol\n"
                "FROM ts_stock_daily_his\n"
                "ORDER BY trade_date\n"
                "LIMIT 500"
            ),
            language="sql", height=140, sizing_mode="stretch_width",
        )

        # ---------- 单行操作栏 ----------
        exec_btn = pn.widgets.Button(
            name="▶ 执行", button_type="primary", width=76,
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        sample_btn = pn.widgets.Button(
            name="📋 示例", width=80,
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        path_input = pn.widgets.TextInput(
            placeholder="pkl 路径，如 D:/data/xxx.pkl",
            width=260, styles=SMALL_SELECT_STYLE,
            margin=SMALL_SELECT_MARGIN)
        load_btn = pn.widgets.Button(
            name="📂 Load", button_type="success", width=90,
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        clear_dash_btn = pn.widgets.Button(
            name="🗑 清空仪表板", width=110,
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)

        status = pn.pane.Markdown(
            "**就绪** · 输入 SQL 后点执行，或填 pkl 路径后点 Load",
            styles={"font-size": "12px"}, margin=(2, 0, 0, 0),
            sizing_mode="stretch_width")

        # ---------- 数据预览 + 聚合工具条 ----------
        preview_table = pn.widgets.Tabulator(
            pd.DataFrame(), page_size=20,
            sizing_mode="stretch_width", height=280,
            show_index=False, configuration={"editable": False},
        )
        agg_row = pn.Row(sizing_mode="stretch_width", margin=(0, 0, 0, 0))
        agg_widgets = []  # List[(col, Select)]

        # 工具条控件
        clear_all_btn = pn.widgets.Button(
            name="清空所有列", width=100, button_type="warning",
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        dup_col_sel = pn.widgets.Select(
            name="", options=[], width=150,
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        dup_btn = pn.widgets.Button(
            name="复制", width=60, button_type="default",
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        agg_hint = pn.pane.Markdown(
            "**列聚合设置**（默认=原字段作为 groupby key；"
            "选聚合函数触发聚合；选「空」丢弃该列）",
            margin=(4, 0, 0, 8), styles={"font-size": "11px"},
            sizing_mode="stretch_width",
        )

        # 全部一行：清空 + 焦点列下拉 + 复制 + 说明
        agg_toolbar = pn.Row(
            clear_all_btn,
            dup_col_sel,
            dup_btn,
            agg_hint,
            sizing_mode="stretch_width", margin=(0, 0, 2, 0),
        )

        preview_accordion = pn.Accordion(
            ("📊 数据预览 / 列聚合配置", pn.Column(
                agg_toolbar,
                agg_row,
                preview_table,
                sizing_mode="stretch_width",
                margin=(0, 0, 0, 0),
            )),
            active=[],
            sizing_mode="stretch_width",
            margin=(0, 0, 0, 0),
        )

        x_sel = pn.widgets.Select(name="X 轴", options=[], width=260,
                                  styles=SMALL_SELECT_STYLE,
                                  margin=SMALL_SELECT_MARGIN)

        axis_holders = {"left": [], "right": [], "hidden": []}
        axis_containers = {}
        axis_type_sels = {}
        axis_add_btns = {}
        axis_palette_sels = {}

        def _make_axis_row(axis_key):
            var_sel = pn.widgets.Select(
                name="", options=[], width=120,
                styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
            color_sel = pn.widgets.Select(
                name="", options=["(无)"], value="(无)", width=110,
                styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
            size_sel = pn.widgets.Select(
                name="", options=["(无)"], value="(无)", width=90,
                styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
            del_btn = pn.widgets.Button(name="×", width=26,
                                        button_type="default",
                                        styles=SMALL_SELECT_STYLE,
                                        margin=(0, 0, 0, 0))

            row_dict = {"var": var_sel, "color": color_sel,
                        "size": size_sel, "row": None}

            def _on_del(e):
                axis_holders[axis_key].remove(row_dict)
                _refresh_axis_rows(axis_key)
                _update_preview()

            del_btn.on_click(_on_del)
            row = pn.Row(var_sel, color_sel, size_sel, del_btn,
                         sizing_mode="stretch_width", margin=(0, 0, 0, 0))
            row_dict["row"] = row
            for w in (var_sel, color_sel, size_sel):
                w.param.watch(lambda e: _update_preview(), "value")
            return row_dict

        def _refresh_axis_rows(axis_key):
            axis_containers[axis_key].objects = [
                r["row"] for r in axis_holders[axis_key]
            ]

        def _on_add_row(axis_key):
            def _cb(e):
                row_dict = _make_axis_row(axis_key)
                cols = state["df"].columns.tolist() if not state["df"].empty else []
                num_cols = (state["df"].select_dtypes(include="number").columns.tolist()
                            if not state["df"].empty else [])
                row_dict["var"].options = cols
                row_dict["color"].options = ["(无)"] + cols
                row_dict["size"].options = ["(无)"] + num_cols
                if cols:
                    row_dict["var"].value = cols[0]
                axis_holders[axis_key].append(row_dict)
                _refresh_axis_rows(axis_key)
                _update_preview()
            return _cb

        for key in ("left", "right", "hidden"):
            axis_containers[key] = pn.Column(sizing_mode="stretch_width",
                                             margin=(0, 0, 0, 0))
            axis_add_btns[key] = pn.widgets.Button(
                name="+", width=28, button_type="default",
                styles=SMALL_SELECT_STYLE, margin=(0, 0, 0, 0))
            axis_add_btns[key].on_click(_on_add_row(key))

            axis_palette_sels[key] = pn.widgets.Select(
                name="色系", options=PALETTE_OPTIONS, value="Category10",
                width=150, styles=SMALL_SELECT_STYLE,
                margin=SMALL_SELECT_MARGIN)
            axis_palette_sels[key].param.watch(
                lambda e: _update_preview(), "value")

        left_type_sel = pn.widgets.Select(
            name="显示方式", options=CHART_TYPES, value="折线图", width=160,
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        right_type_sel = pn.widgets.Select(
            name="显示方式", options=CHART_TYPES, value="无", width=160,
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        hidden_type_sel = pn.widgets.Select(
            name="显示方式", options=CHART_TYPES, value="无", width=160,
            styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
        axis_type_sels = {"left": left_type_sel,
                          "right": right_type_sel,
                          "hidden": hidden_type_sel}
        for s in (left_type_sel, right_type_sel, hidden_type_sel):
            s.param.watch(lambda e: _update_preview(), "value")

        add_btn = pn.widgets.Button(name="➕ 添加到仪表板",
                                    button_type="primary", width=180,
                                    styles=SMALL_SELECT_STYLE)

        preview_pane = pn.pane.Bokeh(sizing_mode="stretch_width", height=500)

        if HAS_TILES:
            dashboard = TileGrid(objects=[], layout=[],
                                 sizing_mode="stretch_width", height=700)
        else:
            dashboard = pn.Column(sizing_mode="stretch_width")

        def _refresh_dashboard():
            if HAS_TILES:
                dashboard.objects = list(state["tile_objects"])
                dashboard.layout = list(state["tile_layout"])
            else:
                dashboard.clear()
                for obj in state["tile_objects"]:
                    dashboard.append(obj)

        def _read_axis_cfg(axis_key):
            rows = [{
                "var": r["var"].value,
                "color": r["color"].value,
                "size": r["size"].value,
            } for r in axis_holders[axis_key]]
            return {
                "type": axis_type_sels[axis_key].value,
                "palette": axis_palette_sels[axis_key].value,
                "rows": rows,
            }

        def _build_current_figure():
            df = state["df"]
            if df.empty or not x_sel.value:
                return None
            try:
                return _build_axis_figure(
                    df, x_sel.value,
                    _read_axis_cfg("left"),
                    _read_axis_cfg("right"),
                    _read_axis_cfg("hidden"),
                    height=500,
                )
            except Exception:
                logger.exception("build figure failed")
                return None

        def _update_preview():
            preview_pane.object = _build_current_figure()

        x_sel.param.watch(lambda e: _update_preview(), "value")

        # ---------- 聚合行构建 ----------
        def _build_agg_row(cols, preserve=None):
            """preserve: dict {col: op}，重建时保留已选 op。"""
            state["_building"] = True
            try:
                agg_widgets.clear()
                widgets = []
                for c in cols:
                    opts = [c] + AGG_OPS
                    default_val = c
                    if preserve and c in preserve and preserve[c] in opts:
                        default_val = preserve[c]
                    sel = pn.widgets.Select(
                        name="", options=opts, value=default_val, width=90,
                        styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
                    sel.param.watch(lambda e: _on_agg_change(), "value")
                    widgets.append(sel)
                    agg_widgets.append((c, sel))
                agg_row.objects = widgets
                dup_col_sel.options = list(cols)
                if dup_col_sel.value not in cols:
                    dup_col_sel.value = cols[0] if cols else None
            finally:
                state["_building"] = False

        def _on_agg_change():
            if state.get("_building"):
                return
            if state["df_raw"].empty:
                return
            specs = [(c, sel.value) for c, sel in agg_widgets]
            try:
                state["df"] = _apply_aggregation(state["df_raw"], specs)
            except Exception:
                logger.exception("aggregation failed")
                state["df"] = state["df_raw"].copy()

            # ---- 更新预览表（显示聚合后的数据）----
            try:
                preview_table.value = _sanitize_df(state["df"].head(20))
            except Exception:
                logger.exception("update preview table failed")

            # 状态栏明确说明：聚合基于完整数据
            try:
                status.object = (
                    f"聚合结果:{len(state['df']):,} 行 × "
                    f"{len(state['df'].columns)} 列 "
                    f"（基于原始 {len(state['df_raw']):,} 行计算，"
                    f"表格仅显示前20行）"
                )
            except Exception:
                pass
            
            cols = state["df"].columns.tolist()
            num_cols = state["df"].select_dtypes(include="number").columns.tolist()

            # ---- x 轴 ----
            x_sel.options = cols
            if cols:
                if not x_sel.value or x_sel.value not in cols:
                    x_sel.value = next(
                        (c for c in cols if _looks_like_datetime_col(c)),
                        cols[0],
                    )
            else:
                try:
                    x_sel.value = None
                except Exception:
                    pass

            # ---- 三个轴 ----
            for key in ("left", "right", "hidden"):
                for r in axis_holders[key]:
                    r["var"].options = cols
                    r["color"].options = ["(无)"] + cols
                    r["size"].options = ["(无)"] + num_cols
                    # 无效值重置
                    if r["var"].value not in cols:
                        r["var"].value = cols[0] if cols else None
                    if r["color"].value != "(无)" and r["color"].value not in cols:
                        r["color"].value = "(无)"
                    if r["size"].value != "(无)" and r["size"].value not in num_cols:
                        r["size"].value = "(无)"

            # ---- 若结果 0 列，彻底清空三个轴 ----
            if not cols:
                for key in ("left", "right", "hidden"):
                    try:
                        axis_type_sels[key].value = "无"
                    except Exception:
                        pass
                    axis_holders[key].clear()
                    _refresh_axis_rows(key)
                preview_pane.object = None
                return

            _update_preview()

        # ---------- 清空所有列 ----------
        def _on_clear_all_cols(event):
            if state["df_raw"].empty:
                status.object = "**⚠️ 尚未加载数据**"
                return

            cols = state["df_raw"].columns.tolist()

            # 1. 重建 agg 行，全部 "(空)"
            state["_building"] = True
            try:
                agg_widgets.clear()
                widgets = []
                for c in cols:
                    opts = [c] + AGG_OPS
                    sel = pn.widgets.Select(
                        name="", options=opts, value="(空)", width=90,
                        styles=SMALL_SELECT_STYLE, margin=SMALL_SELECT_MARGIN)
                    sel.param.watch(lambda e: _on_agg_change(), "value")
                    widgets.append(sel)
                    agg_widgets.append((c, sel))
                agg_row.objects = widgets
            finally:
                state["_building"] = False

            # 2. 清空 state["df"]
            state["df"] = pd.DataFrame()

            # 3. 清空预览表
            try:
                preview_table.value = pd.DataFrame()
            except Exception:
                pass

            # 4. 清空 x 轴（options + value）
            try:
                x_sel.options = []
                x_sel.value = None
            except Exception:
                pass

            # 5. 清空三个轴（显示方式 + 变量行）
            for key in ("left", "right", "hidden"):
                try:
                    axis_type_sels[key].value = "无"
                except Exception:
                    pass
                axis_holders[key].clear()
                _refresh_axis_rows(key)

            # 6. 清空预览图
            preview_pane.object = None

            status.object = "**🧹 已清空所有列，并清空 x轴 / 左轴 / 右轴 / 隐藏轴**"

        # ---------- 复制焦点列 ----------
        def _on_duplicate_col(event):
            if state["df_raw"].empty:
                status.object = "**⚠️ 请先加载数据**"
                return
            target = dup_col_sel.value
            if not target or target not in state["df_raw"].columns:
                status.object = "**⚠️ 请选择要复制的焦点列**"
                return
            # 找不冲突的新名字
            i = 1
            while True:
                new_name = f"{target}_c{i}"
                if new_name not in state["df_raw"].columns:
                    break
                i += 1
            try:
                state["df_raw"][new_name] = state["df_raw"][target].copy()
            except Exception as e:
                logger.exception("duplicate col failed")
                status.object = f"**❌ 复制失败：{e}**"
                return

            # 保留当前 op，重建聚合行（新列默认原字段名）
            old_specs = {c: sel.value for c, sel in agg_widgets}
            old_specs[new_name] = new_name
            _build_agg_row(state["df_raw"].columns.tolist(), preserve=old_specs)

            # 触发聚合 + 刷新预览表 + 刷新 x/左右隐藏轴
            _on_agg_change()

            dup_col_sel.value = new_name
            status.object = f"**✅ 已复制列 `{target}` → `{new_name}`**"

        clear_all_btn.on_click(_on_clear_all_cols)
        dup_btn.on_click(_on_duplicate_col)

        # ---------- 共用：加载 df 后的处理 ----------
        def _apply_loaded_df(df, source_label):
            if df is None or df.empty:
                preview_table.value = pd.DataFrame()
                status.object = f"**✅ {source_label}，但结果为空**"
                return
            df = _normalize_columns(df)
            state["df_raw"] = df
            state["df"] = df.copy()
            preview_table.value = _sanitize_df(df.head(20))
            status.object = (f"**✅ {source_label}** · "
                             f"{len(df)} 行 × {len(df.columns)} 列")

            cols = list(df.columns)
            num_cols = df.select_dtypes(include="number").columns.tolist()

            _build_agg_row(cols)
            x_sel.options = cols
            if cols:
                x_sel.value = next(
                    (c for c in cols if _looks_like_datetime_col(c)), cols[0])

            axis_holders["left"].clear()
            axis_holders["right"].clear()
            axis_holders["hidden"].clear()
            if cols:
                rd = _make_axis_row("left")
                rd["var"].options = cols
                rd["color"].options = ["(无)"] + cols
                rd["size"].options = ["(无)"] + num_cols
                rd["var"].value = (num_cols[0] if num_cols else cols[0])
                axis_holders["left"].append(rd)
            _refresh_axis_rows("left")
            _refresh_axis_rows("right")
            _refresh_axis_rows("hidden")
            _update_preview()

        # ---------- 执行 SQL ----------
        def _on_exec(event):
            try:
                status.object = "**⏳ 执行中...**"
                df = _exec_sql(sql_editor.value, max_rows=MAX_ROWS_DEFAULT)
                _apply_loaded_df(df, "SQL 执行成功")
                logger.info("exec ok | rows=%d | cols=%d",
                            len(df), len(df.columns) if df is not None else 0)
            except Exception as e:
                logger.exception("exec failed")
                status.object = f"**❌ 失败：{type(e).__name__}: {e}**"

        # ---------- 加载 PKL ----------
        def _on_load_pkl(event):
            path = (path_input.value or "").strip().strip('"').strip("'")
            if not path:
                status.object = "**⚠️ 请先填写 pkl 文件路径**"
                return
            try:
                status.object = "**⏳ 加载中...**"
                df = _load_dataframe_from_pkl(path)
                _apply_loaded_df(df, f"已加载 `{os.path.basename(path)}`")
                logger.info("load pkl ok | path=%s | rows=%d | cols=%d",
                            path, len(df), len(df.columns))
            except FileNotFoundError:
                logger.warning("pkl not found | %s", path)
                status.object = f"**❌ 文件不存在：`{path}`**"
            except Exception as e:
                logger.exception("load pkl failed")
                status.object = f"**❌ 加载失败：{type(e).__name__}: {e}**"

        exec_btn.on_click(_on_exec)
        load_btn.on_click(_on_load_pkl)

        # ---------- 其它按钮 ----------
        def _on_add(event):
            fig = _build_current_figure()
            if fig is None:
                status.object = "**⚠️ 请先配置图表**"
                return
            pane = pn.pane.Bokeh(fig, sizing_mode="stretch_width", height=500)
            state["tile_objects"].append(pane)
            state["tile_layout"].append({"width": 100, "height": 550, "visible": True})
            _refresh_dashboard()
            status.object = "**✅ 已添加到仪表板**"

        def _on_clear_dash(event):
            state["tile_objects"] = []
            state["tile_layout"] = []
            _refresh_dashboard()
            status.object = "**🗑 已清空**"

        def _on_sample(event):
            sql_editor.value = (
                "SELECT ts_code, trade_date, open_price, high, low, close_price, vol\n"
                "FROM ts_stock_daily_his\n"
                "ORDER BY trade_date\n"
                "LIMIT 500"
            )
            status.object = "**已填入示例 SQL**"

        sample_btn.on_click(_on_sample)
        add_btn.on_click(_on_add)
        clear_dash_btn.on_click(_on_clear_dash)

        tables = _list_tables()
        table_select = pn.widgets.Select(options=[""] + tables, width=180,
                                         styles=SMALL_SELECT_STYLE,
                                         margin=SMALL_SELECT_MARGIN)

        def _on_table(event):
            t = event.new
            if t:
                sql_editor.value = f"SELECT * FROM `{t}` LIMIT 500"
                status.object = f"**已插入 `{t}` 查询模板**"

        table_select.param.watch(_on_table, "value")

        header = pn.Row(
            pn.pane.Markdown(
                f"### 🎨 数据工作室 · {username} "
                f"<span style='color:#888;font-size:12px'>· {role}</span>",
                margin=(0, 8, 0, 0),
            ),
            sizing_mode="stretch_width", margin=(0, 0, 0, 0),
        )

        sql_section = pn.Column(
            pn.Row(
                pn.pane.Markdown("### 📝 SQL", margin=(0, 0, 0, 0),
                                 styles={"font-size": "14px"}),
                pn.Spacer(width=10),
                table_select,
                sizing_mode="stretch_width", margin=(0, 0, 2, 0),
            ),
            sql_editor,
            pn.Row(
                exec_btn, sample_btn,
                pn.Spacer(width=8),
                path_input, load_btn,
                pn.Spacer(width=8),
                clear_dash_btn,
                sizing_mode="stretch_width", margin=(0, 0, 0, 0),
            ),
            status,
            sizing_mode="stretch_width",
            margin=(0, 0, 4, 0),
            styles={"padding": "6px", "background": "#f8f9fa",
                    "border-radius": "6px", "border": "1px solid #e0e0e0"},
        )

        def _axis_block(title, key):
            return pn.Column(
                pn.Row(
                    pn.pane.Markdown(f"**{title}**", margin=(0, 0, 0, 0),
                                     styles={"font-size": "13px"}),
                    axis_add_btns[key],
                    sizing_mode="stretch_width", margin=(0, 0, 2, 0),
                ),
                pn.Row(
                    axis_type_sels[key],
                    axis_palette_sels[key],
                    sizing_mode="stretch_width",
                    margin=(0, 0, 2, 0),
                ),
                axis_containers[key],
                sizing_mode="stretch_width",
                margin=(0, 0, 4, 0),
                styles={"gap": "2px"},
            )

        config_panel = pn.Column(
            pn.pane.Markdown("### ⚙️ 图表配置", margin=(0, 0, 4, 0),
                             styles={"font-size": "14px"}),
            x_sel,
            pn.layout.Divider(margin=(2, 0, 2, 0)),
            _axis_block("🟦 左轴", "left"),
            pn.layout.Divider(margin=(2, 0, 2, 0)),
            _axis_block("🟧 右轴（可选）", "right"),
            pn.layout.Divider(margin=(2, 0, 2, 0)),
            _axis_block("⬜ 隐藏轴（可选，不显示刻度）", "hidden"),
            pn.layout.Divider(margin=(2, 0, 2, 0)),
            add_btn,
            width=340,
            styles={"padding": "6px", "background": "#fafafa",
                    "border-radius": "6px", "border": "1px solid #e0e0e0",
                    "gap": "2px"},
            margin=(0, 0, 0, 0),
        )

        preview_col = pn.Column(
            pn.pane.Markdown("### 📈 实时预览", margin=(0, 0, 4, 0),
                             styles={"font-size": "14px"}),
            preview_pane,
            sizing_mode="stretch_width", margin=(0, 0, 0, 0),
        )

        dashboard_section = pn.Column(
            pn.pane.Markdown("### 🖼️ 仪表板", margin=(0, 0, 4, 0),
                             styles={"font-size": "14px"}),
            dashboard,
            sizing_mode="stretch_width", margin=(0, 0, 0, 0),
        )

        return pn.Column(
            header,
            sql_section,
            preview_accordion,
            pn.Row(config_panel, preview_col, sizing_mode="stretch_width",
                   margin=(0, 0, 0, 0)),
            pn.layout.Divider(margin=(2, 0, 2, 0)),
            dashboard_section,
            sizing_mode="stretch_width",
            styles={"padding": "6px"},
            margin=(0, 0, 0, 0),
        )

    return _factory