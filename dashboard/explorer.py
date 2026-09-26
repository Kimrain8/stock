"""Panel 数据探索工作台（多用户隔离）"""
import logging

import numpy as np
import pandas as pd
import panel as pn
import plotly.graph_objects as go

from .auth import sessions
from .config import settings

logger = logging.getLogger("stock2.explorer")

pn.extension("plotly", "tabulator", "codeeditor")


# ============================================================
# 当前用户识别
# ============================================================
def _get_current_user() -> tuple[str, str]:
    """从 Panel 会话的 Cookie 中解析用户名与角色。

    返回 (username, role)。
    解析失败时返回 ("anonymous", "user")。
    """
    try:
        # Panel 在服务端会话中挂载了 cookies
        cookies = getattr(pn.state, "cookies", None) or {}
        token = cookies.get(settings.SESSION_COOKIE)
        if not token:
            logger.warning("no session cookie found")
            return "anonymous", "user"

        info = sessions.verify(token)
        if not info:
            logger.warning("invalid session token | token=%s...", token[:8])
            return "anonymous", "user"

        return info["username"], info.get("role", "user")
    except Exception as e:
        logger.warning("get current user failed | err=%s", e)
        return "anonymous", "user"


# ============================================================
# 按用户加载数据
# ============================================================
def _load_user_data(username: str) -> pd.DataFrame:
    """加载当前用户的数据。

    现在用 username 作随机种子生成演示数据，保证不同用户看到不同曲线。

    接真实数据时替换为：
        from utils.dbManager import MySQLHelper
        db = MySQLHelper(db=settings.DB_NAME)
        df = db.qry2df(sql=(
            "SELECT trade_date, open, high, low, close, volume "
            "FROM stock_daily WHERE owner = '<username>' "
            "ORDER BY trade_date"
        ))
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return df.set_index("trade_date")
    """
    seed = abs(hash(username)) % (2 ** 32)
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=200, freq="D")
    base = rng.standard_normal(200).cumsum() + 100
    return pd.DataFrame({
        "date": dates,
        "open": base + rng.standard_normal(200) * 0.5,
        "high": base + rng.standard_normal(200) * 1.5,
        "low": base - rng.standard_normal(200) * 1.5,
        "close": base + rng.standard_normal(200) * 0.5,
        "volume": rng.integers(1_000_000, 10_000_000, 200),
    }).set_index("date")


# ============================================================
# 构建单个会话的布局
# ============================================================
def _build_layout(df: pd.DataFrame, username: str, role: str) -> pn.Row:
    """根据数据和用户身份构建探索器布局。"""

    # ---------- 左侧：变量浏览器 ----------
    info = pd.DataFrame({
        "列名": list(df.columns),
        "类型": [str(df[c].dtype) for c in df.columns],
        "非空数": [int(df[c].notna().sum()) for c in df.columns],
    })
    var_table = pn.widgets.Tabulator(
        info,
        page_size=10,
        height=220,
        show_index=False,
        sizing_mode="stretch_width",
    )
    sidebar = pn.Column(
        pn.pane.Markdown(f"### 👤 `{username}`"),
        pn.pane.Markdown(f"角色：**{role}**"),
        pn.pane.Markdown("---"),
        pn.pane.Markdown("### 📋 变量浏览器"),
        pn.pane.Markdown(f"**`df`** — {len(df)} 行 × {len(df.columns)} 列"),
        var_table,
        width=280,
        styles={
            "background": "#f8f9fa",
            "padding": "10px",
            "border-radius": "6px",
            "border": "1px solid #e0e0e0",
        },
    )

    # ---------- 中间：编辑器 ----------
    editor = pn.widgets.CodeEditor(
        value="df.tail(20)",
        language="python",
        height=160,
        sizing_mode="stretch_width",
    )

    def execute(expr: str):
        expr = (expr or "").strip()
        if not expr:
            return pn.pane.Markdown("*请输入表达式*")
        try:
            result = eval(
                expr,
                {"__builtins__": {}},
                {"df": df, "pd": pd, "np": np, "go": go},
            )
        except Exception as e:
            return pn.pane.Alert(
                f"❌ `{type(e).__name__}`: {e}",
                alert_type="danger",
            )

        # DataFrame：有 OHLC 就画 K 线，否则表格
        if isinstance(result, pd.DataFrame):
            if (
                {"open", "high", "low", "close"}.issubset(result.columns)
                and len(result) > 0
            ):
                fig = go.Figure(data=[go.Candlestick(
                    x=result.index,
                    open=result["open"],
                    high=result["high"],
                    low=result["low"],
                    close=result["close"],
                )])
                fig.update_layout(
                    height=480,
                    xaxis_rangeslider_visible=False,
                    margin=dict(l=40, r=20, t=30, b=40),
                )
                return pn.pane.Plotly(fig, config={"responsive": True})
            return pn.widgets.Tabulator(
                result, page_size=15, sizing_mode="stretch_width",
            )

        # Series：转 DataFrame 显示
        if isinstance(result, pd.Series):
            return pn.widgets.Tabulator(
                result.to_frame(), page_size=15, sizing_mode="stretch_width",
            )

        # 其他：repr
        return pn.pane.Markdown(f"```\n{result!r}\n```")

    result_pane = pn.bind(execute, editor)

    # ---------- 快捷操作 ----------
    quick = pn.widgets.RadioButtonGroup(
        options=["尾 20 行", "头部", "筛选 close>100", "统计摘要", "K线图"],
        button_type="light",
        sizing_mode="stretch_width",
    )
    mapping = {
        "尾 20 行": "df.tail(20)",
        "头部": "df.head()",
        "筛选 close>100": "df[df['close'] > 100]",
        "统计摘要": "df.describe()",
        "K线图": "df.tail(60)",
    }

    def on_quick(event):
        new_expr = mapping.get(event.new)
        if new_expr:
            editor.value = new_expr

    quick.param.watch(on_quick, "value")

    # ---------- 组装 ----------
    main = pn.Column(
        pn.pane.Markdown("### ✏️ 表达式编辑器"),
        editor,
        pn.pane.Markdown("---"),
        pn.pane.Markdown("### 📊 结果面板"),
        quick,
        pn.panel(result_pane, sizing_mode="stretch_width"),
        sizing_mode="stretch_width",
    )

    return pn.Row(sidebar, main, sizing_mode="stretch_width")


# ============================================================
# 对外入口：工厂函数
# ============================================================
def build_explorer():
    """返回一个工厂函数。

    Panel 通过 bokeh_fastapi 挂载到 FastAPI 时，每个新浏览器会话都会调用
    这个工厂函数一次，因此每个用户拿到独立的数据和布局。
    """
    def _factory():
        username, role = _get_current_user()
        logger.info("explorer session | user=%s | role=%s", username, role)

        try:
            df = _load_user_data(username)
        except Exception as e:
            logger.exception("load user data failed | user=%s", username)
            # 加载失败时给一个空 DataFrame，避免整页崩掉
            df = pd.DataFrame({
                "open": [], "high": [], "low": [], "close": [], "volume": [],
            })

        return _build_layout(df, username, role)

    return _factory