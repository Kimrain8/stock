"""真正的数据探索器 — 从 jhd_rd 数据库读取，表格/图形展示 + 导出。"""
import io
import logging

import numpy as np
import pandas as pd
import panel as pn

from utils.dbManager import MySQLHelper
from .auth import sessions
from .config import settings

logger = logging.getLogger("stock2.data_explorer")

pn.extension("tabulator", "plotly", sizing_mode="stretch_width")


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
        logger.warning("get current user failed | %s", e)
        return "anonymous", "user"


# ============================================================
# 数据库访问
# ============================================================
def _db() -> MySQLHelper:
    return MySQLHelper(db=settings.DB_NAME)


def _list_tables() -> list[str]:
    """列出数据库所有表。"""
    try:
        df = _db().qry2df(sql="SHOW TABLES")
        if df is None or df.empty:
            return []
        # 第一列就是表名
        return sorted(df.iloc[:, 0].astype(str).tolist())
    except Exception as e:
        logger.error("list tables failed | %s", e)
        return []


def _get_table_columns(table: str) -> list[str]:
    """获取表字段名。"""
    try:
        sql = f"SHOW COLUMNS FROM `{table}`"
        df = _db().qry2df(sql=sql)
        if df is None or df.empty:
            return []
        return df["Field"].astype(str).tolist()
    except Exception as e:
        logger.error("get columns failed | %s | %s", table, e)
        return []


def _exec_sql(sql: str, max_rows: int = 10000) -> pd.DataFrame:
    """执行 SQL。自动限制返回行数。

    参数：
        sql: SQL 语句
        max_rows: 最大返回行数（超出则截断）
    """
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return pd.DataFrame()

    # 简单防护：只允许 SELECT
    head = sql.lstrip().upper()
    if not head.startswith(("SELECT", "SHOW", "DESC", "EXPLAIN")):
        raise ValueError("只支持 SELECT / SHOW / DESC / EXPLAIN 语句")

    # 如果是 SELECT，且没有 LIMIT，则自动加
    if head.startswith("SELECT") and " LIMIT " not in sql.upper():
        sql = f"{sql} LIMIT {int(max_rows)}"

    df = _db().qry2df(sql=sql)
    return df if df is not None else pd.DataFrame()


# ============================================================
# 工厂函数
# ============================================================
def build_data_explorer():
    def _factory():
        username, role = _get_current_user()
        logger.info("data_explorer session | user=%s | role=%s",
                    username, role)

        # ---------- 状态 ----------
        state = {"tables": [], "current_df": pd.DataFrame()}

        # ---------- 左栏：表列表 ----------
        table_filter = pn.widgets.TextInput(
            placeholder="输入关键字过滤表名",
            width=200,
        )
        table_list = pn.widgets.RadioBoxGroup(
            options=[],
            width=200,
            styles={"overflow-y": "auto", "max-height": "600px"},
        )

        # ---------- SQL 编辑器 ----------
        sql_editor = pn.widgets.CodeEditor(
            value="-- 从左侧选择一个表，或直接写 SQL\nSELECT * FROM ts_stock_basic LIMIT 100",
            language="sql",
            height=140,
            sizing_mode="stretch_width",
        )

        # ---------- 按钮 ----------
        run_btn = pn.widgets.Button(
            name="▶ 执行",
            button_type="primary",
            width=100,
        )
        export_btn = pn.widgets.FileDownload(
            callback=lambda: io.BytesIO(
                state["current_df"].to_csv(index=False).encode("utf-8-sig")
            ) if not state["current_df"].empty
            else io.BytesIO(b""),
            filename="query_result.csv",
            label="💾 导出 CSV",
            button_type="default",
            width=140,
        )

        # ---------- 状态提示 ----------
        status = pn.pane.Markdown(
            "**就绪**", width=500,
        )

        # ---------- 结果表格 ----------
        result_table = pn.widgets.Tabulator(
            pd.DataFrame(),
            page_size=20,
            sizing_mode="stretch_width",
            height=400,
            show_index=False,
            header_filters=True,
            configuration={"editable": False},
        )

        # ---------- 图形化 ----------
        x_select = pn.widgets.Select(
            name="X 轴", options=[], width=180,
        )
        y_select = pn.widgets.Select(
            name="Y 轴（可多选）", options=[], width=260,
        )
        chart_type = pn.widgets.Select(
            name="图表类型",
            options=["折线图", "柱状图", "散点图", "面积图"],
            value="折线图",
            width=120,
        )
        plot_btn = pn.widgets.Button(
            name="📈 绘制",
            button_type="default",
            width=100,
        )
        chart_pane = pn.pane.Plotly(
            sizing_mode="stretch_width",
            height=400,
            config={"responsive": True},
        )

        # ============================================================
        # 逻辑
        # ============================================================
        def _refresh_table_list():
            tables = _list_tables()
            state["tables"] = tables
            _filter_tables()
            status.object = f"**共 {len(tables)} 张表**"

        def _filter_tables():
            kw = (table_filter.value or "").strip().lower()
            if kw:
                filtered = [t for t in state["tables"] if kw in t.lower()]
            else:
                filtered = state["tables"]
            table_list.options = filtered

        def _on_filter(event):
            _filter_tables()

        def _on_table_select(event):
            table = event.new
            if not table:
                return
            sql_editor.value = f"SELECT * FROM `{table}` LIMIT 100"
            status.object = f"**已选择表：** `{table}`，点「▶ 执行」查看"

        def _update_column_selectors(df: pd.DataFrame):
            cols = list(df.columns)
            x_select.options = cols
            y_select.options = cols
            if cols:
                x_select.value = cols[0]
                # 自动选一个数值列作为 y
                num_cols = df.select_dtypes(include="number").columns.tolist()
                y_select.value = num_cols[0] if num_cols else cols[0]

        def _on_run(event):
            try:
                status.object = "**⏳ 执行中...**"
                df = _exec_sql(sql_editor.value, max_rows=10000)
                state["current_df"] = df

                if df.empty:
                    result_table.value = pd.DataFrame()
                    status.object = "**✅ 执行成功，结果为空**"
                else:
                    result_table.value = df
                    status.object = (
                        f"**✅ 执行成功** · "
                        f"{len(df)} 行 × {len(df.columns)} 列"
                    )
                    _update_column_selectors(df)

                # 清空图表
                chart_pane.object = None
            except Exception as e:
                logger.exception("exec sql failed")
                status.object = f"**❌ {type(e).__name__}**: {e}"

        def _on_plot(event):
            df = state["current_df"]
            if df is None or df.empty:
                status.object = "**⚠️ 请先执行查询**"
                return

            try:
                import plotly.graph_objects as go

                x_col = x_select.value
                y_col = y_select.value
                if x_col not in df.columns or y_col not in df.columns:
                    status.object = "**⚠️ 列选择无效**"
                    return

                fig = go.Figure()
                ctype = chart_type.value

                if ctype == "折线图":
                    fig.add_trace(go.Scatter(
                        x=df[x_col], y=df[y_col],
                        mode="lines+markers", name=y_col,
                    ))
                elif ctype == "柱状图":
                    fig.add_trace(go.Bar(
                        x=df[x_col], y=df[y_col], name=y_col,
                    ))
                elif ctype == "散点图":
                    fig.add_trace(go.Scatter(
                        x=df[x_col], y=df[y_col],
                        mode="markers", name=y_col,
                    ))
                elif ctype == "面积图":
                    fig.add_trace(go.Scatter(
                        x=df[x_col], y=df[y_col],
                        mode="lines", fill="tozeroy", name=y_col,
                    ))

                fig.update_layout(
                    height=400,
                    margin=dict(l=40, r=20, t=30, b=40),
                    xaxis_title=x_col,
                    yaxis_title=y_col,
                )
                chart_pane.object = fig
                status.object = f"**✅ 已绘制**：{y_col} vs {x_col}"
            except Exception as e:
                logger.exception("plot failed")
                status.object = f"**❌ 绘图失败**: {e}"

        # ============================================================
        # 事件绑定
        # ============================================================
        table_filter.param.watch(_on_filter, "value")
        table_list.param.watch(_on_table_select, "value")
        run_btn.on_click(_on_run)
        plot_btn.on_click(_on_plot)

        # 首次加载
        _refresh_table_list()

        # ============================================================
        # 布局
        # ============================================================
        left_panel = pn.Column(
            pn.pane.Markdown("### 📋 数据库表"),
            table_filter,
            table_list,
            width=220,
            styles={
                "background": "#f8f9fa",
                "padding": "10px",
                "border-radius": "6px",
                "border": "1px solid #e0e0e0",
            },
        )

        main_panel = pn.Column(
            pn.pane.Markdown("### 🔍 SQL 查询"),
            sql_editor,
            pn.Row(run_btn, export_btn, pn.Spacer(width=20), status),
            pn.layout.Divider(),
            pn.pane.Markdown("### 📊 查询结果"),
            result_table,
            pn.layout.Divider(),
            pn.pane.Markdown("### 📈 图形化"),
            pn.Row(x_select, y_select, chart_type, plot_btn),
            chart_pane,
            sizing_mode="stretch_width",
        )

        return pn.Row(
            left_panel,
            main_panel,
            sizing_mode="stretch_width",
            styles={"padding": "12px"},
        )

    return _factory