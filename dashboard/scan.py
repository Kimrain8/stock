import asyncio
import logging
import io

import numpy as np
import pandas as pd
import panel as pn

from .auth import sessions
from .config import settings
from .db_utils import load_scan_data, load_stock_names
from .data_ops.growth_patterns import find_growth_patterns
from .shared_state import set_pending_codes
"""选股扫描页面 — 基于涨幅模式识别"""


logger = logging.getLogger("stock2.scan")
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
# 工厂函数
# ============================================================
def build_scan():
    def _factory():
        username, role = _get_current_user()
        logger.info("scan session | user=%s | role=%s", username, role)

        # ============ 状态 ============
        # 规则列表：每条含 days / threshold / day_type
        rules_state: list[dict] = [
            {"days": 14, "threshold": 0.4, "day_type": "natural"},
            {"days": 5,  "threshold": 0.2, "day_type": "natural"},
        ]

        # ============ UI 组件 ============
        days_range = pn.widgets.IntInput(
            name="数据范围（自然日）", value=365,
            start=30, end=3650, width=140,
        )

        mode_select = pn.widgets.RadioButtonGroup(
            name="输出模式",
            options=["high", "end", "all"],
            value="high",
            button_type="default",
        )

        rules_column = pn.Column(sizing_mode="stretch_width")

        scan_btn = pn.widgets.Button(
            name="开始扫描", button_type="primary", width=120,
        )
        

        status = pn.pane.Markdown(
            '**准备就绪**。点击"开始扫描"执行。', width=420,
        )

        results_table = pn.widgets.Tabulator(
            pd.DataFrame(columns=[
                "rule", "ts_code", "name", "start_date", "end_date",
                "growth (%)", "查看",
            ]),
            page_size=20,
            sizing_mode="stretch_width",
            height=480,
            selectable="checkbox",           # ← 只保留这一行
            show_index=False,
            header_filters=True,
            configuration={"editable": False},
            formatters={"查看": {"type": "html"}},
        )

        # 导出：读当前表格内容
        def _export_csv():
            """导出当前表格为 CSV。必须返回 BytesIO。"""
            try:
                csv_bytes = results_table.value.to_csv(
                    index=False,
                ).encode("utf-8-sig")
                return io.BytesIO(csv_bytes)
            except Exception as e:
                logger.exception("export csv failed")
                return io.BytesIO(b"error")

        export_btn = pn.widgets.FileDownload(
            callback=_export_csv,
            filename="scan_results.csv",
            label="导出 CSV",
            button_type="default",
            width=120,
        )

        # selected_info = pn.pane.Markdown("", width=420)
        selected_info = pn.pane.HTML("", width=520)
        
        # ---------- 发送到相似股扫描 ----------
        send_similar_btn = pn.widgets.Button(
            name="📤 发送到相似股扫描",
            button_type="success",
            width=200,
        )
        send_msg = pn.pane.Markdown("", width=400)
        
        def _on_send_similar(event):
            """把勾选（或全部）的代码发送到 /analysis 的相似股扫描。"""
            try:
                df = results_table.value
                if df is None or df.empty:
                    send_msg.object = "**⚠️ 结果为空**"
                    return

                sel = results_table.selection
                if sel:
                    codes = df.iloc[list(sel)]["ts_code"].tolist()
                    prefix = "勾选"
                else:
                    codes = df["ts_code"].tolist()
                    prefix = "全部"

                if not codes:
                    send_msg.object = "**⚠️ 无有效代码**"
                    return

                codes_str = "\n".join(codes)
                set_pending_codes(username, codes_str)

                send_msg.object = (
                    f"**✅ 已发送 {len(codes)} 个代码（{prefix}）**\n\n"
                    f"切到 `/analysis` → 🎯 相似股扫描，"
                    f"点「📥 从 /scan 拉取」即可加载。"
                )
            except Exception as e:
                logger.exception("send similar failed")
                send_msg.object = f"**❌ {type(e).__name__}: {e}**"

        send_similar_btn.on_click(_on_send_similar)

        send_wb_btn = pn.widgets.Button(
            name="📤 发送到工作台",
            button_type="success",
            width=180,
        )
        send_wb_msg = pn.pane.Markdown("", width=400)

        def _on_send_wb(event):
            from .shared_state import set_pending
            try:
                df = results_table.value
                if df is None or df.empty:
                    send_wb_msg.object = "**⚠️ 结果为空**"
                    return
                set_pending(username, "scan_result", df.copy())
                send_wb_msg.object = (
                    f"**✅ 已发送 {len(df)} 行到工作台**\n\n"
                    f"去 `/workbench` → 点「📤 拉取 /scan 结果」"
                )
            except Exception as e:
                send_wb_msg.object = f"**❌ {e}**"

        send_wb_btn.on_click(_on_send_wb)

        def _on_row_select(event):
            sel = results_table.selection
            if sel is None or sel == []:
                selected_info.object = ""
                return
            idx = sel if isinstance(sel, int) else sel[0]
            try:
                row = results_table.value.iloc[idx]
            except Exception:
                selected_info.object = ""
                return
            code = row["ts_code"]
            selected_info.object = (
                f"<b>选中：</b> <code>{code}</code> · "
                f"<a href='/trend?symbol={code}' target='_blank' "
                f"style='color:#1976d2'>在趋势页查看 →</a>"
            )

        results_table.param.watch(_on_row_select, "selection")

        # ============ 规则渲染 ============
        add_rule_btn = pn.widgets.Button(
            name="+ 添加规则", button_type="default", width=120,
        )

        def _make_rule_row(idx: int) -> pn.Row:
            rule = rules_state[idx]

            days_input = pn.widgets.IntInput(
                value=rule["days"], start=1, end=250, width=80,
            )
            thresh_input = pn.widgets.FloatInput(
                value=rule["threshold"] * 100,
                start=0.1, end=100.0, step=0.5, width=90,
            )
            type_select = pn.widgets.Select(
                options=["natural", "trading"],
                value=rule["day_type"], width=120,
            )

            def _on_change(event):
                rules_state[idx]["days"] = days_input.value
                rules_state[idx]["threshold"] = thresh_input.value / 100.0
                rules_state[idx]["day_type"] = type_select.value

            days_input.param.watch(_on_change, "value")
            thresh_input.param.watch(_on_change, "value")
            type_select.param.watch(_on_change, "value")

            remove_btn = pn.widgets.Button(
                name="×", width=40, button_type="danger",
            )

            def _remove(event):
                if len(rules_state) <= 1:
                    status.object = "**至少保留一条规则**"
                    return
                rules_state.pop(idx)
                _render_rules()

            remove_btn.on_click(_remove)

            return pn.Row(
                pn.pane.Markdown(f"**规则 {idx + 1}**", width=80),
                pn.pane.Markdown("天数", width=40),
                days_input,
                pn.pane.Markdown("涨幅 ≥", width=60),
                thresh_input,
                pn.pane.Markdown("%", width=20),
                pn.pane.Markdown("类型", width=40),
                type_select,
                remove_btn,
                sizing_mode="stretch_width",
            )

        def _render_rules():
            rules_column.clear()
            for i in range(len(rules_state)):
                rules_column.append(_make_rule_row(i))
            rules_column.append(add_rule_btn)

        def _add_rule(event):
            rules_state.append(
                {"days": 10, "threshold": 0.3, "day_type": "natural"}
            )
            _render_rules()

        add_rule_btn.on_click(_add_rule)
        _render_rules()

        # ============ 扫描逻辑 ============
        async def _on_scan(event):
            if not rules_state:
                status.object = "**请至少添加一条规则**"
                return

            # 校验
            for i, r in enumerate(rules_state):
                if r["days"] < 1:
                    status.object = f"**规则 {i + 1} 天数必须 ≥ 1**"
                    return
                if r["threshold"] <= 0:
                    status.object = f"**规则 {i + 1} 涨幅必须 > 0**"
                    return

            scan_btn.disabled = True
            status.object = "**正在加载数据...**"

            try:
                # 1. 加载数据（线程池，不阻塞 UI）
                df = await asyncio.to_thread(
                    load_scan_data, days_range.value,
                )
                if df.empty:
                    status.object = "**数据为空，请检查数据库**"
                    return

                n_codes = df["ts_code"].nunique()
                status.object = (
                    f"**扫描中** {n_codes} 只股票 / {len(df)} 条记录..."
                )

                # 2. 按 day_type 分组，每种类型单独调用
                by_type: dict[str, list[dict]] = {}
                for r in rules_state:
                    by_type.setdefault(r["day_type"], []).append(r)

                all_hits = []
                for day_type, group in by_type.items():
                    patterns = tuple(
                        (r["days"], r["threshold"]) for r in group
                    )
                    hits = await asyncio.to_thread(
                        find_growth_patterns,
                        df, patterns, mode_select.value, day_type,
                    )
                    if hits is not None and not hits.empty:
                        all_hits.append(hits)

                # 3. 无结果
                if not all_hits:
                    results_table.value = pd.DataFrame(columns=[
                        "rule", "ts_code", "start_date",
                        "end_date", "growth (%)",
                    ])
                    status.object = "**扫描完成，无匹配结果**"
                    return

                # 4. 合并 + 排序
                result = pd.concat(all_hits, ignore_index=True)
                result["growth_pct"] = (result["growth"] * 100).round(2)
                result = result.sort_values(
                    "growth", ascending=False
                ).reset_index(drop=True)

                stock_names = load_stock_names()
                display = result[[
                    "rule", "ts_code", "start_date", "end_date", "growth_pct",
                ]].rename(columns={"growth_pct": "growth (%)"})
                # 在 ts_code 后插入 name 列
                display.insert(2, "name",display["ts_code"].map(stock_names).fillna(""),)

                display["start_date"] = pd.to_datetime(
                    display["start_date"]
                ).dt.strftime("%Y-%m-%d")
                display["end_date"] = pd.to_datetime(
                    display["end_date"]
                ).dt.strftime("%Y-%m-%d")

                # ---- 加一列 HTML 链接 ----
                display["查看"] = display["ts_code"].apply(
                    lambda c: f'<a href="/trend?symbol={c}" target="_blank" '
                            f'style="color:#1976d2;text-decoration:none;font-weight:bold">'
                            f'打开 →</a>'
                )

                results_table.value = display

                status.object = (
                    f"**扫描完成** · 命中 **{len(result)}** 条 · "
                    f"股票 **{result['ts_code'].nunique()}** 只"
                )
                logger.info(
                    "scan done | user=%s | hits=%d | codes=%d",
                    username, len(result), result["ts_code"].nunique(),
                )

            except Exception as e:
                logger.exception("scan failed")
                status.object = f"**扫描失败：{type(e).__name__}: {e}**"
            finally:
                scan_btn.disabled = False

        scan_btn.on_click(_on_scan)

        # ============ 选中行提示 ============
        # def _on_row_click(event):
        #     """点击行 → 显示提示。跳转由"查看"列的 HTML 链接实现。"""
        #     row_idx = event.row
        #     try:
        #         row = results_table.value.iloc[row_idx]
        #     except Exception:
        #         return

        #     code = str(row["ts_code"])
        #     rule = str(row["rule"])
        #     grow = row["growth (%)"]
        #     selected_info.object = (
        #         f"<b>选中：</b> <code>{code}</code> · "
        #         f"<span style='color:#888'>{rule} · {grow}%</span> · "
        #         f"<a href='/trend?symbol={code}' target='_blank' "
        #         f"style='color:#1976d2'>在趋势页查看 →</a>"
        #     )

        # results_table.on_click(_on_row_click)

        # ============ 布局 ============
        return pn.Column(
            pn.pane.Markdown(f"### 🔍 选股扫描 · `{username}`"),
            pn.Row(
                days_range,
                pn.Spacer(width=20),
                pn.pane.Markdown("**输出模式：**"),
                mode_select,
                sizing_mode="stretch_width",
            ),
            pn.layout.Divider(),
            pn.pane.Markdown("**扫描规则**"),
            rules_column,
            # ---- 只有这一处按钮行 ----
            pn.Row(
                scan_btn,
                pn.Spacer(width=12),
                export_btn,
                pn.Spacer(width=12),
                send_similar_btn,
                pn.Spacer(width=20),
                send_wb_btn,
                pn.Spacer(width=20),
                status,
                sizing_mode="stretch_width",
            ),
            send_msg,
            send_wb_msg,
            pn.layout.Divider(),
            pn.pane.Markdown("**扫描结果**"),
            selected_info,
            results_table,
            sizing_mode="stretch_width",
            styles={"padding": "12px"},
        )

    return _factory