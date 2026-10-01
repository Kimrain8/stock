"""数据工作台 — Web 版 IPython 交互环境 + 数据源接入。

特性：
    - 会话级 Python 命名空间
    - 4 个折叠卡片（数据库/缓存、自然语言选股、代码编辑器、执行结果）
    - Ctrl+Enter 快捷键（浏览器脚本注入）
    - 5 个数据源：SQL、上传、缓存、scan 结果、analysis 结果
    - 自然语言选股（知识库驱动）
    - 结果标记：_all_pass / condN_pass / condN_reason
"""
import ast
import contextlib
import io
import logging
import pickle
import re
import traceback
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import panel as pn

from utils.dbManager import MySQLHelper
from .auth import sessions
from .config import settings
from .analysis.nl_filter import NLFilterEngine

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

    if head.startswith("SELECT") and not re.search(r"\blimit\b", sql, re.IGNORECASE):
        sql = f"{sql} LIMIT 100000"

    df = _db().qry2df(sql=sql)
    if df is None:
        return pd.DataFrame()

    # Decimal → float
    for col in df.columns:
        s = df[col]
        if s.dtype == object:
            sample = s.dropna().head(5)
            if len(sample) > 0 and isinstance(sample.iloc[0], Decimal):
                try:
                    df[col] = s.apply(
                        lambda x: float(x) if isinstance(x, Decimal) else x
                    )
                except Exception:
                    pass
    return df


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
# 渲染辅助
# ============================================================
def _sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """清理 DataFrame 中的不可序列化对象。"""
    df = df.copy()
    for col in df.columns:
        s = df[col]
        if s.dtype == object:
            sample_vals = s.dropna().head(5)
            if len(sample_vals) > 0:
                first = sample_vals.iloc[0]
                if isinstance(first, Decimal):
                    try:
                        df[col] = s.apply(
                            lambda x: float(x) if isinstance(x, Decimal)
                            else (None if pd.isna(x) else x)
                        )
                        continue
                    except Exception:
                        pass
                if hasattr(first, "tzinfo") and first.tzinfo is not None:
                    try:
                        df[col] = s.dt.tz_localize(None)
                        continue
                    except Exception:
                        pass
            try:
                df[col] = s.apply(
                    lambda x: x if isinstance(x, (str, int, float, bool, type(None)))
                    else (None if pd.isna(x) else str(x))
                )
            except Exception:
                pass
    return df


def _reorder_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    """列重排 + 按 _all_pass 降序。

    列顺序：_all_pass | condN_pass | condN_reason | 原始列
    """
    if df is None or df.empty:
        return df

    def _col_num(c):
        m = re.search(r"\d+", c)
        return int(m.group()) if m else 0

    prefix_cols = []
    if "_all_pass" in df.columns:
        prefix_cols.append("_all_pass")

    cond_pass_cols = sorted(
        [c for c in df.columns
         if c.startswith("cond") and c.endswith("_pass")],
        key=_col_num,
    )
    cond_reason_cols = sorted(
        [c for c in df.columns
         if c.startswith("cond") and c.endswith("_reason")],
        key=_col_num,
    )
    ordered = prefix_cols + cond_pass_cols + cond_reason_cols
    other_cols = [c for c in df.columns if c not in ordered]

    out = df[ordered + other_cols].copy()
    if "_all_pass" in out.columns:
        out = out.sort_values("_all_pass", ascending=False).reset_index(drop=True)
    return out


def render_value(value):
    if value is None:
        return None
    if isinstance(value, pd.DataFrame):
        if value.empty:
            return pn.pane.Markdown("*(空 DataFrame)*")
        try:
            safe = _sanitize_df(value.head(500))
        except Exception:
            safe = value.head(500)
        return pn.widgets.Tabulator(
            safe, page_size=20, show_index=False,
            sizing_mode="stretch_width", height=320,
        )
    if isinstance(value, pd.Series):
        try:
            safe = _sanitize_df(value.head(500).to_frame())
        except Exception:
            safe = value.head(500).to_frame()
        return pn.widgets.Tabulator(
            safe, page_size=20,
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
        return (
            f"**`{name}`**\n\n"
            f"*Series · {len(value)} 项 · {value.dtype}*\n\n"
            f"- 样例值: {_sample_value(value)}"
        )
    if isinstance(value, dict):
        lines = [f"**`{name}`**", f"*dict · {len(value)} 键*", ""]
        for k in list(value.keys())[:20]:
            lines.append(f"- `{k}`: {str(value[k])[:22]}")
        return "\n".join(lines)
    if isinstance(value, (list, tuple, set)):
        return f"**`{name}`**\n\n*{type(value).__name__} · {len(value)} 项*"
    return f"**`{name}`**\n\n*{type(value).__name__}*"


# ============================================================
# Ctrl+Enter 脚本
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
    # 左栏控件
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

    var_info = pn.pane.Markdown(
        "*选中变量后显示列信息*",
        width=210,
        styles={
            "background": "#ffffff", "padding": "8px 10px",
            "border": "1px solid #e0e0e0", "border-radius": "4px",
            "font-size": "11px", "max-height": "400px",
            "overflow-y": "auto",
        },
    )

    # ============================================================
    # SQL 内容
    # ============================================================
    sql_content = pn.Column(
        pn.pane.Markdown("*点击左侧按钮加载数据*"),
        sizing_mode="stretch_width",
    )

    # ============================================================
    # NL 选股控件
    # ============================================================
    nl_engine = NLFilterEngine()
    nl_state = {"conditions": [], "target_df": None}

    nl_target = pn.widgets.Select(
        name="目标变量", options=[], width=280,
    )
    nl_input = pn.widgets.TextAreaInput(
        name="描述",
        placeholder="例：股价低于10元 且 换手率大于3%",
        height=60,
        sizing_mode="stretch_width",
    )
    nl_parse_btn = pn.widgets.Button(
        name="🔍 解析并添加条件", button_type="primary", width=180,
    )
    nl_reload_btn = pn.widgets.Button(
        name="🔄 重载知识库", width=120,
    )
    nl_apply_btn = pn.widgets.Button(
        name="✅ 应用全部条件", button_type="success", width=160,
    )
    nl_view_btn = pn.widgets.Button(
        name="📊 查看结果清单", width=140,
    )
    nl_view_passed_btn = pn.widgets.Button(
        name="📋 仅看通过", button_type="default", width=120,
    )
    nl_msg = pn.pane.Markdown("", sizing_mode="stretch_width")
    nl_cond_list = pn.Column(sizing_mode="stretch_width")

    # ============================================================
    # 代码编辑器
    # ============================================================
    editor = pn.widgets.CodeEditor(
        value=(
            "# 例：df_daily.head()\n"
            "# 例：df_daily[df_daily['_all_pass']]\n"
            "# 例：import plotly.express as px; px.bar(df_daily, x='ts_code', y='close')\n"
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
    # 折叠卡片（在回调之前定义，因为回调会引用）
    # ============================================================
    sql_card = pn.Card(
        sql_content,
        title="🔍 数据库 / 缓存",
        collapsed=True,
        sizing_mode="stretch_width",
    )
    nl_card = pn.Card(
        pn.Column(
            pn.Row(nl_target, nl_reload_btn),
            nl_input,
            pn.Row(nl_parse_btn, nl_apply_btn,
                   nl_view_btn, nl_view_passed_btn),
            nl_msg,
            pn.layout.Divider(),
            pn.pane.Markdown("**已添加条件**"),
            nl_cond_list,
            sizing_mode="stretch_width",
        ),
        title="🎯 自然语言选股",
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
    # 回调：变量
    # ============================================================
    def _refresh_vars():
        var_select.options = ws.list_user_vars()
        if var_select.value not in var_select.options:
            var_select.value = None
            var_info.object = "*选中变量后显示列信息*"
        df_options = []
        for v in ws.list_user_vars():
            name = v.split("  [")[0].strip()
            if name in ws.ns and isinstance(ws.ns[name], pd.DataFrame):
                df_options.append(v)
        nl_target.options = df_options

    def _on_var_select(event):
        if not event.new:
            var_info.object = "*选中变量后显示列信息*"
            return
        name = event.new.split("  [")[0].strip()
        if name not in ws.ns:
            var_info.object = "*选中变量后显示列信息*"
            return
        try:
            var_info.object = _make_var_info(name, ws.ns[name])
        except Exception as e:
            var_info.object = f"**错误**：{e}"

    def _delete_var(event):
        if not var_select.value:
            return
        name = var_select.value.split("  [")[0].strip()
        ws.remove(name)
        _refresh_vars()
        var_info.object = "*选中变量后显示列信息*"

    var_select.param.watch(_on_var_select, "value")
    delete_btn.on_click(_delete_var)

    # ============================================================
    # 回调：NL 选股
    # ============================================================
    def _render_conditions():
        nl_cond_list.clear()
        if not nl_state["conditions"]:
            nl_cond_list.append(pn.pane.Markdown("*尚未添加条件*"))
            return
        for i, cond in enumerate(nl_state["conditions"], 1):
            if cond.error:
                item = pn.pane.Markdown(
                    f"**{i}. {cond.raw}** ❌ {cond.error}",
                    styles={"color": "#c00", "font-size": "12px"},
                )
            else:
                item = pn.pane.Markdown(
                    f"**{i}. {cond.raw}** → `{cond.column}` · "
                    f"{cond.description}",
                    styles={"color": "#333", "font-size": "12px"},
                )
            remove = pn.widgets.Button(
                name="❌", width=50, button_type="danger",
            )

            def _remove(event, idx=i - 1):
                nl_state["conditions"].pop(idx)
                _render_conditions()

            remove.on_click(_remove)
            nl_cond_list.append(
                pn.Row(item, remove, sizing_mode="stretch_width")
            )

    def _on_parse(event):
        text = (nl_input.value or "").strip()
        if not text:
            nl_msg.object = "**⚠️ 请输入描述**"
            return
        if not nl_target.value:
            nl_msg.object = "**⚠️ 请先选择目标变量**"
            return
        name = nl_target.value.split("  [")[0].strip()
        df = ws.ns.get(name)
        if not isinstance(df, pd.DataFrame):
            nl_msg.object = f"**⚠️ `{name}` 不是 DataFrame**"
            return
        new_conds = nl_engine.parse(text, df)
        if not new_conds:
            nl_msg.object = "**⚠️ 未解析出条件**"
            return
        existing_raws = {c.raw for c in nl_state["conditions"]}
        added, skipped = [], []
        for c in new_conds:
            if c.raw in existing_raws:
                skipped.append(c.raw)
            else:
                added.append(c)
                existing_raws.add(c.raw)
        if not added:
            nl_msg.object = (
                f"**⚠️ {len(skipped)} 条条件已存在，未添加**"
            )
            return
        nl_state["conditions"].extend(added)
        nl_state["target_df"] = name
        _render_conditions()
        ok = sum(1 for c in added if not c.error)
        err = len(added) - ok
        msg = f"**✅ 已添加 {len(added)} 条条件**（成功 {ok}，失败 {err}）"
        if skipped:
            msg += f"\n\n跳过重复：{len(skipped)} 条"
        nl_msg.object = msg

    def _on_reload(event):
        nl_engine.reload()
        nl_msg.object = (
            f"**✅ 知识库已重载** · "
            f"字段 {len(nl_engine.aliases)} 条 · "
            f"模板 {len(nl_engine.templates)} 条"
        )

    def _on_apply(event):
        if not nl_state["conditions"]:
            nl_msg.object = "**⚠️ 无条件**"
            return
        name = nl_state["target_df"]
        df = ws.ns.get(name)
        if not isinstance(df, pd.DataFrame):
            nl_msg.object = f"**⚠️ `{name}` 不是 DataFrame**"
            return
        try:
            df_new, summary = nl_engine.apply(df, nl_state["conditions"])
            df_new = _reorder_and_sort(df_new)
            new_name = ws.put(f"{name}_filtered", df_new)
            _refresh_vars()
            lines = [
                f"- 条件 {i+1}: 通过 {c['passed']} / {c['total']}"
                + (f" · ⚠️ {c.get('error', '')}" if c.get("error") else "")
                for i, c in enumerate(summary["conditions"])
            ]
            nl_msg.object = (
                f"**✅ 已应用到 `{new_name}`** · "
                f"有效 **{summary['passed']}** / {summary['total']} 行\n\n"
                + "\n".join(lines)
                + "\n\n下一步可点「📊 查看结果清单」或「📋 仅看通过」。"
            )
        except Exception as e:
            logger.exception("apply conditions failed")
            nl_msg.object = f"**❌ {type(e).__name__}: {e}**"

    def _on_view(event):
        """显示 filtered 变量的完整内容（含标记列）。"""
        name = nl_state["target_df"]
        if not name:
            nl_msg.object = "**⚠️ 未指定目标**"
            return
        filtered_name = f"{name}_filtered"
        if filtered_name not in ws.ns:
            nl_msg.object = "**⚠️ 请先点「应用全部条件」**"
            return

        df = ws.ns[filtered_name]
        total = len(df)
        passed = (int(df["_all_pass"].sum())
                  if "_all_pass" in df.columns else total)

        nl_msg.object = (
            f"**✅ 显示 `{filtered_name}` 完整数据** · "
            f"**{passed} 通过** / {total} 总行\n\n"
            f"含全部行 + 标记列。在表格里点 `_all_pass` 列头排序，"
            f"或在 `condN_reason` 列筛选查看剔除原因。"
        )

        out_value.clear()
        try:
            widget = render_value(df.head(500))
        except Exception as e:
            logger.exception("render view failed")
            widget = pn.pane.Markdown(
                f"**渲染失败**：{type(e).__name__}: {e}"
            )
        if widget is not None:
            out_value.append(widget)
        out_stdout.object = ""
        out_error.object = ""
        result_card.collapsed = False

    def _on_view_passed(event):
        """只显示 _all_pass=True 的行，去掉标记列。"""
        name = nl_state["target_df"]
        if not name:
            nl_msg.object = "**⚠️ 未指定目标**"
            return
        filtered_name = f"{name}_filtered"
        if filtered_name not in ws.ns:
            nl_msg.object = "**⚠️ 请先点「应用全部条件」**"
            return

        df = ws.ns[filtered_name]
        if "_all_pass" not in df.columns:
            nl_msg.object = "**⚠️ 该 df 无 _all_pass 列**"
            return

        passed_df = df[df["_all_pass"]].reset_index(drop=True)

        drop_cols = [c for c in passed_df.columns
                     if c == "_all_pass"
                     or (c.startswith("cond")
                         and (c.endswith("_pass") or c.endswith("_reason")))]
        display_df = passed_df.drop(columns=drop_cols, errors="ignore")

        passed_name = ws.put(f"{name}_passed", display_df)
        nl_msg.object = (
            f"**✅ 仅通过清单已保存为 `{passed_name}`** · "
            f"{len(display_df)} 行（无标记列）"
        )

        out_value.clear()
        try:
            widget = render_value(display_df.head(500))
        except Exception as e:
            widget = pn.pane.Markdown(f"**渲染失败**：{e}")
        if widget is not None:
            out_value.append(widget)
        out_stdout.object = ""
        out_error.object = ""
        result_card.collapsed = False
        _refresh_vars()

    nl_parse_btn.on_click(_on_parse)
    nl_reload_btn.on_click(_on_reload)
    nl_apply_btn.on_click(_on_apply)
    nl_view_btn.on_click(_on_view)
    nl_view_passed_btn.on_click(_on_view_passed)

    # ============================================================
    # 回调：SQL
    # ============================================================
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

    # ============================================================
    # 回调：上传
    # ============================================================
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

    # ============================================================
    # 回调：缓存
    # ============================================================
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

    # ============================================================
    # 回调：拉取
    # ============================================================
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

    # ============================================================
    # 回调：执行代码
    # ============================================================
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

    # ============================================================
    # 初始化
    # ============================================================
    _refresh_vars()
    _render_conditions()

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
        nl_card,
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