"""数据工作台 — Web 版 IPython 交互环境 + 数据源接入。

特性：
    - 会话级 Python 命名空间
    - 3 个折叠区域（SQL/缓存、代码编辑器、执行结果）
    - Ctrl+Enter 快捷键（浏览器脚本注入，含 Shadow DOM 递归查找）
    - 5 个数据源：SQL、上传、缓存、scan 结果、analysis 结果
    - 左栏选中变量时显示列名 + 样例值
"""
import ast
import contextlib
import io
import logging
import pickle
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import panel as pn

from utils.dbManager import MySQLHelper
from .auth import sessions
from .config import settings

logger = logging.getLogger("stock2.workbench")

pn.extension("tabulator", "codeeditor", sizing_mode="stretch_width")

BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# 用户 & 会话
# ============================================================
def _get_current_user():
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


def _get_session_id():
    try:
        return pn.state.curdoc.session_context.id
    except Exception:
        return "default"


# ============================================================
# 工作区
# ============================================================
class Workspace:
    def __init__(self):
        self.ns = {}
        self.counter = {}
        self.ns.update({"pd": pd, "np": np, "pn": pn, "Path": Path})
        try:
            import plotly.graph_objects as go
            import plotly.express as px
            self.ns["go"] = go
            self.ns["px"] = px
        except ImportError:
            pass

    def suggest_name(self, base):
        base = base.replace(".", "_").replace("-", "_")
        if not base or (not base[0].isalpha() and base[0] != "_"):
            base = "df_" + base
        if base not in self.ns:
            return base
        i = self.counter.get(base, 1) + 1
        while f"{base}_{i}" in self.ns:
            i += 1
        self.counter[base] = i
        return f"{base}_{i}"

    def put(self, name, value):
        if not name:
            name = self.suggest_name("df")
        self.ns[name] = value
        return name

    def remove(self, name):
        self.ns.pop(name, None)

    def list_user_vars(self):
        libs = {"pd", "np", "pn", "Path", "go", "px"}
        out = []
        for k, v in self.ns.items():
            if k.startswith("_") or k in libs:
                continue
            t = type(v).__name__
            if isinstance(v, pd.DataFrame):
                brief = f"{v.shape[0]}×{v.shape[1]}"
            elif isinstance(v, pd.Series):
                brief = f"{len(v)} 项"
            elif isinstance(v, (list, tuple, set, dict)):
                brief = f"{len(v)} 项"
            elif isinstance(v, str):
                brief = v[:20]
            else:
                brief = type(v).__name__
            out.append(f"{k}  [{t}]  {brief}")
        return sorted(out)


_WORKSPACES = {}


def _get_workspace(sid):
    if sid not in _WORKSPACES:
        _WORKSPACES[sid] = Workspace()
    return _WORKSPACES[sid]


# ============================================================
# 代码执行
# ============================================================
def execute_code(code, ws):
    result = {"stdout": "", "value": None, "error": None}
    buf = io.StringIO()
    code = (code or "").rstrip()
    if not code:
        return result
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        result["error"] = f"SyntaxError: {e}"
        return result
    last_expr = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = tree.body.pop()
    try:
        with contextlib.redirect_stdout(buf):
            if tree.body:
                exec(compile(tree, "<wb>", "exec"), ws.ns)
            if last_expr is not None:
                val = eval(compile(ast.Expression(last_expr.value),
                                   "<wb>", "eval"), ws.ns)
                result["value"] = val
                ws.ns["_"] = val
    except Exception:
        result["error"] = traceback.format_exc()
    result["stdout"] = buf.getvalue()
    return result


# ============================================================
# 数据库
# ============================================================
def _db():
    return MySQLHelper(db=settings.DB_NAME)


def _exec_sql(sql):
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return pd.DataFrame()
    head = sql.lstrip().upper()
    if not head.startswith(("SELECT", "SHOW", "DESC", "EXPLAIN")):
        raise ValueError("只支持 SELECT / SHOW / DESC / EXPLAIN")
    if head.startswith("SELECT") and " LIMIT " not in sql.upper():
        sql = f"{sql} LIMIT 100000"
    df = _db().qry2df(sql=sql)
    return df if df is not None else pd.DataFrame()


def _list_db_tables():
    try:
        df = _db().qry2df(sql="SHOW TABLES")
        if df is None or df.empty:
            return []
        return sorted(df.iloc[:, 0].astype(str).tolist())
    except Exception as e:
        logger.warning("list tables failed | %s", e)
        return []


def _list_cache_codes():
    d = BASE_DIR / "cache" / "data"
    if not d.exists():
        return []
    return sorted([p.stem for p in d.glob("*.pkl")])


# ============================================================
# 渲染
# ============================================================
def render_value(value):
    if value is None:
        return None
    if isinstance(value, pd.DataFrame):
        if value.empty:
            return pn.pane.Markdown("*(空 DataFrame)*")
        return pn.widgets.Tabulator(
            value.head(200), page_size=20, show_index=False,
            sizing_mode="stretch_width", height=320,
        )
    if isinstance(value, pd.Series):
        return pn.widgets.Tabulator(
            value.head(200).to_frame(), page_size=20,
            sizing_mode="stretch_width", height=320,
        )
    try:
        from plotly.graph_objects import Figure
        if isinstance(value, Figure):
            return pn.pane.Plotly(value, sizing_mode="stretch_width",
                                  height=400)
    except ImportError:
        pass
    try:
        r = repr(value)
    except Exception:
        r = "<repr error>"
    if len(r) > 2000:
        r = r[:2000] + "\n...(截断)"
    return pn.pane.Markdown(f"```\n{r}\n```")


def _sample_value(series, max_len=22):
    """取 Series 中第一个非空值作为样例。"""
    try:
        for v in series:
            if pd.isna(v):
                continue
            s = str(v)
            if len(s) > max_len:
                s = s[:max_len] + "..."
            return s
    except Exception:
        pass
    return ""


def _make_var_info(name, value):
    """生成变量信息的 Markdown。"""
    if isinstance(value, pd.DataFrame):
        lines = [
            f"**`{name}`**",
            f"*DataFrame · {value.shape[0]} 行 × {value.shape[1]} 列*",
            "",
        ]
        max_cols = 30
        for col in value.columns[:max_cols]:
            dtype = str(value[col].dtype)
            sample = _sample_value(value[col])
            lines.append(f"- `{col}` [{dtype}]: {sample}")
        if len(value.columns) > max_cols:
            lines.append(f"- ...（还有 {len(value.columns) - max_cols} 列）")
        return "\n".join(lines)

    if isinstance(value, pd.Series):
        dtype = str(value.dtype)
        sample = _sample_value(value)
        return (
            f"**`{name}`**\n\n"
            f"*Series · {len(value)} 项 · {dtype}*\n\n"
            f"- 样例值: {sample}"
        )

    if isinstance(value, dict):
        lines = [f"**`{name}`**", f"*dict · {len(value)} 键*", ""]
        for k in list(value.keys())[:20]:
            v = value[k]
            s = str(v)[:22]
            lines.append(f"- `{k}`: {s}")
        return "\n".join(lines)

    if isinstance(value, (list, tuple, set)):
        return (
            f"**`{name}`**\n\n"
            f"*{type(value).__name__} · {len(value)} 项*"
        )

    return f"**`{name}`**\n\n*{type(value).__name__}*"


# ============================================================
# Ctrl+Enter 脚本（浏览器端注入，含 Shadow DOM 递归查找）
# ============================================================
CTRL_ENTER_SCRIPT = """
<script>
(function() {
    if (window.__wbCtrlEnterBound) return;
    window.__wbCtrlEnterBound = true;

    function findAllButtons(root) {
        var btns = Array.from(root.querySelectorAll('button'));
        root.querySelectorAll('*').forEach(function(el) {
            if (el.shadowRoot) {
                btns = btns.concat(findAllButtons(el.shadowRoot));
            }
        });
        return btns;
    }

    document.addEventListener('keydown', function(e) {
        if (!e.ctrlKey) return;
        if (e.key !== 'Enter' && e.keyCode !== 13) return;

        var btns = findAllButtons(document);
        var runBtn = null;
        for (var i = 0; i < btns.length; i++) {
            var txt = (btns[i].textContent || '').trim();
            if (txt.indexOf('▶') !== -1) {
                runBtn = btns[i];
                break;
            }
        }
        if (!runBtn) return;

        e.preventDefault();
        e.stopPropagation();
        runBtn.click();
        return false;
    }, true);

    console.log('[workbench] Ctrl+Enter bound');
})();
</script>
"""


# ============================================================
# UI 构建
# ============================================================
def _build_ui():
    username, role = _get_current_user()
    sid = _get_session_id()
    ws = _get_workspace(sid)
    logger.info("workbench build | user=%s | session=%s", username, sid)

    # ============================================================
    # 左栏：数据源 & 变量
    # ============================================================
    sql_btn = pn.widgets.Button(name="🔍 SQL 查询", width=190)
    upload_input = pn.widgets.FileInput(width=190)
    cache_btn = pn.widgets.Button(name="📦 缓存加载", width=190)
    scan_btn = pn.widgets.Button(name="📤 拉取 scan 结果", width=190)
    ana_btn = pn.widgets.Button(name="📤 拉取 analysis 结果", width=190)
    src_msg = pn.pane.Markdown("", width=210)

    var_select = pn.widgets.Select(
        name="工作区变量", options=[], width=210,
    )
    delete_btn = pn.widgets.Button(name="🗑 删除选中", width=210)

    # ---- 变量信息面板（列名 + 样例值） ----
    var_info = pn.pane.Markdown(
        "*选中变量后显示列信息*",
        width=210,
        styles={
            "background": "#ffffff",
            "padding": "8px 10px",
            "border": "1px solid #e0e0e0",
            "border-radius": "4px",
            "font-size": "11px",
            "max-height": "400px",
            "overflow-y": "auto",
        },
    )

    # ============================================================
    # SQL / 缓存 内容区（可变内容）
    # ============================================================
    sql_content = pn.Column(
        pn.pane.Markdown("*点击左侧按钮加载数据*"),
        sizing_mode="stretch_width",
    )

    # ============================================================
    # 代码编辑器
    # ============================================================
    editor = pn.widgets.CodeEditor(
        value=(
            "# 例：df_scan.head()\n"
            "# 例：df_scan[df_scan['growth'] > 0.5]\n"
            "# 例：import plotly.express as px; px.bar(df_scan, x='ts_code', y='growth')\n"
            "#\n"
            "# 快捷键：Ctrl+Enter 执行\n"
        ),
        language="python",
        height=200,
        sizing_mode="stretch_width",
    )
    run_btn = pn.widgets.Button(
        name="▶ 执行", button_type="primary", width=120,
    )
    clear_btn = pn.widgets.Button(name="🗑 清空输出", width=120)

    out_stdout = pn.pane.Markdown("", sizing_mode="stretch_width")
    out_error = pn.pane.Markdown("", sizing_mode="stretch_width")
    out_value = pn.Column(sizing_mode="stretch_width")

    # ============================================================
    # 折叠卡片（3 个）
    # ============================================================
    sql_card = pn.Card(
        sql_content,
        title="🔍 数据库 / 缓存",
        collapsed=True,
        sizing_mode="stretch_width",
    )
    editor_card = pn.Card(
        pn.Column(
            editor,
            pn.Row(run_btn, clear_btn),
            sizing_mode="stretch_width",
        ),
        title="📝 代码编辑器",
        collapsed=False,
        sizing_mode="stretch_width",
    )
    result_card = pn.Card(
        pn.Column(
            out_error,
            out_stdout,
            out_value,
            sizing_mode="stretch_width",
        ),
        title="📊 执行结果",
        collapsed=False,
        sizing_mode="stretch_width",
    )

    # ============================================================
    # 逻辑
    # ============================================================
    def _refresh_vars():
        var_select.options = ws.list_user_vars()
        if var_select.value not in var_select.options:
            var_select.value = None
            var_info.object = "*选中变量后显示列信息*"

    def _on_var_select(event):
        """选中变量时更新列信息面板。"""
        if not event.new:
            var_info.object = "*选中变量后显示列信息*"
            return
        name = event.new.split("  [")[0].strip()
        if name not in ws.ns:
            var_info.object = "*选中变量后显示列信息*"
            return
        value = ws.ns[name]
        try:
            var_info.object = _make_var_info(name, value)
        except Exception as e:
            var_info.object = f"**错误**：{e}"

    var_select.param.watch(_on_var_select, "value")

    def _delete_var(event):
        if not var_select.value:
            return
        name = var_select.value.split("  [")[0].strip()
        ws.remove(name)
        _refresh_vars()
        var_info.object = "*选中变量后显示列信息*"

    delete_btn.on_click(_delete_var)

    # ---------- SQL ----------
    def _open_sql(event):
        tables = _list_db_tables()
        table_sel = pn.widgets.Select(
            options=[""] + tables, width=300, name="选表",
        )
        sql_edit = pn.widgets.CodeEditor(
            value="SELECT * FROM `ts_stock_basic` LIMIT 100",
            language="sql", height=120, sizing_mode="stretch_width",
        )
        var_name = pn.widgets.TextInput(value="df_sql", width=180)
        ok = pn.widgets.Button(
            name="✅ 执行并加载", button_type="primary", width=140,
        )
        msg = pn.pane.Markdown("")

        def _sel(event):
            t = event.new
            if t:
                sql_edit.value = f"SELECT * FROM `{t}` LIMIT 100"
                var_name.value = f"df_{t}"

        table_sel.param.watch(_sel, "value")

        def _run(event):
            try:
                df = _exec_sql(sql_edit.value)
                if df.empty:
                    msg.object = "**✅ 查询成功但为空**"
                    return
                name = ws.put(var_name.value or "df_sql", df)
                msg.object = f"**✅ 已加载 `{name}`** ({df.shape})"
                _refresh_vars()
            except Exception as e:
                msg.object = f"**❌ {type(e).__name__}: {e}**"

        ok.on_click(_run)

        sql_content.clear()
        sql_content.append(pn.pane.Markdown("### 数据库查询"))
        sql_content.append(table_sel)
        sql_content.append(sql_edit)
        sql_content.append(pn.Row(var_name, ok))
        sql_content.append(msg)
        sql_card.collapsed = False

    sql_btn.on_click(_open_sql)

    # ---------- 上传 ----------
    def _on_upload(event):
        if not event.new:
            return
        try:
            data = event.new
            name = event.filename or "upload.csv"
            suf = Path(name).suffix.lower()
            if suf == ".csv":
                df = pd.read_csv(io.BytesIO(data))
            elif suf in (".xlsx", ".xls"):
                df = pd.read_excel(io.BytesIO(data))
            elif suf == ".pkl":
                df = pickle.loads(data)
            else:
                src_msg.object = f"**❌ 不支持 {suf}**"
                return
            vname = ws.put(f"df_{Path(name).stem}", df)
            src_msg.object = f"**✅ `{vname}`** ({df.shape})"
            _refresh_vars()
        except Exception as e:
            logger.exception("upload failed")
            src_msg.object = f"**❌ {type(e).__name__}: {e}**"

    upload_input.param.watch(_on_upload, "value")

    # ---------- 缓存 ----------
    def _on_cache(event):
        codes = _list_cache_codes()
        if not codes:
            src_msg.object = "**⚠️ 无缓存文件**"
            return
        code_sel = pn.widgets.Select(options=codes, width=200, name="代码")
        type_sel = pn.widgets.Select(
            options=["quotes", "announcements", "holder",
                     "release", "pledge", "dividends"],
            value="quotes", width=180, name="类型",
        )
        ok = pn.widgets.Button(
            name="✅ 加载", button_type="primary", width=100,
        )
        msg = pn.pane.Markdown("")

        def _load(event):
            try:
                p = BASE_DIR / "cache" / "data" / f"{code_sel.value}.pkl"
                with p.open("rb") as f:
                    data = pickle.load(f)
                df = data.get(type_sel.value)
                if df is None or (hasattr(df, "empty") and df.empty):
                    msg.object = "**⚠️ 空**"
                    return
                if not isinstance(df, pd.DataFrame):
                    df = pd.DataFrame(df)
                vname = ws.put(
                    f"df_{code_sel.value.replace('.', '_')}_{type_sel.value}",
                    df,
                )
                msg.object = f"**✅ `{vname}`** ({df.shape})"
                _refresh_vars()
            except Exception as e:
                msg.object = f"**❌ {type(e).__name__}: {e}**"

        ok.on_click(_load)

        sql_content.clear()
        sql_content.append(pn.pane.Markdown("### 从缓存加载"))
        sql_content.append(pn.Row(code_sel, type_sel))
        sql_content.append(pn.Row(ok))
        sql_content.append(msg)
        sql_card.collapsed = False

    cache_btn.on_click(_on_cache)

    # ---------- 拉 scan ----------
    def _pull_scan(event):
        from .shared_state import pop_pending
        df = pop_pending(username, "scan_result")
        if df is None or (hasattr(df, "empty") and df.empty):
            src_msg.object = "**⚠️ 无 scan 结果**"
            return
        vname = ws.put("df_scan", df)
        src_msg.object = f"**✅ `{vname}`** ({df.shape})"
        _refresh_vars()

    scan_btn.on_click(_pull_scan)

    # ---------- 拉 analysis ----------
    def _pull_ana(event):
        from .shared_state import pop_pending
        payload = pop_pending(username, "analysis_result")
        if not payload:
            src_msg.object = "**⚠️ 无 analysis 结果**"
            return
        df = payload.get("df")
        if df is None or df.empty:
            src_msg.object = "**⚠️ 空**"
            return
        code = payload.get("ts_code", "unknown").replace(".", "_")
        vname = ws.put(f"df_{code}", df)
        src_msg.object = f"**✅ `{vname}`** ({df.shape})"
        _refresh_vars()

    ana_btn.on_click(_pull_ana)

    # ---------- 执行代码 ----------
    def _run_code(event):
        res = execute_code(editor.value, ws)
        _refresh_vars()
        out_stdout.object = ""
        out_error.object = ""
        out_value.clear()
        if res["stdout"]:
            out_stdout.object = f"```\n{res['stdout']}\n```"
        if res["error"]:
            out_error.object = (
                f"<pre style='color:#c00;font-size:12px;white-space:pre-wrap'>"
                f"{res['error']}</pre>"
            )
        elif res["value"] is not None:
            w = render_value(res["value"])
            if w is not None:
                out_value.append(w)
        result_card.collapsed = False

    run_btn.on_click(_run_code)

    def _clear_out(event):
        out_stdout.object = ""
        out_error.object = ""
        out_value.clear()

    clear_btn.on_click(_clear_out)

    _refresh_vars()

    # ============================================================
    # 布局
    # ============================================================
    left = pn.Column(
        pn.pane.Markdown("### 🔌 数据源"),
        sql_btn,
        pn.pane.Markdown("**上传文件**", margin=(5, 0, 0, 0)),
        upload_input,
        cache_btn,
        scan_btn,
        ana_btn,
        src_msg,
        pn.layout.Divider(),
        pn.pane.Markdown("### 📦 工作区变量"),
        var_select,
        delete_btn,
        var_info,
        width=240,
        styles={
            "background": "#f8f9fa", "padding": "10px",
            "border": "1px solid #e0e0e0", "border-radius": "6px",
        },
    )

    right = pn.Column(
        pn.pane.HTML(CTRL_ENTER_SCRIPT, width=0, height=0),
        sql_card,
        editor_card,
        result_card,
        sizing_mode="stretch_width",
    )

    return pn.Row(
        left, right,
        sizing_mode="stretch_width",
        styles={"padding": "12px"},
    )


# ============================================================
# 工厂
# ============================================================
def build_data_workbench():
    def _factory():
        try:
            return _build_ui()
        except Exception as e:
            err = traceback.format_exc()
            logger.exception("workbench build failed")
            return pn.Column(
                pn.pane.Markdown("## ❌ 数据工作台加载失败"),
                pn.pane.Markdown(f"**{type(e).__name__}**: {e}"),
                pn.pane.Markdown(f"```\n{err}\n```"),
                sizing_mode="stretch_width",
                styles={"padding": "20px", "background": "#fff5f5"},
            )
    return _factory