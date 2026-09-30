"""分析页面 — 3 个 Tab：分析 / 复盘 / 规则库"""
import asyncio
import logging
from datetime import datetime

import panel as pn

from .auth import sessions
from .config import settings

logger = logging.getLogger("stock2.analysis_page")

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
# 分析 Tab
# ============================================================
def _build_analysis_tab() -> pn.Column:
    from .analysis.agents import analyze_full

    symbol_input = pn.widgets.TextInput(
        name="股票代码",
        placeholder="例如 688508.SH",
        width=200,
    )
    scenario_select = pn.widgets.Select(
        name="场景提示",
        options={
            "自动判断（推荐）": "",
            "高成长股": "high_growth",
            "价值股": "value",
            "周期股": "cyclical",
            "反转股": "turnaround",
        },
        value="",
        width=180,
    )
    force_refresh = pn.widgets.Checkbox(
        name="强制刷新（忽略当天缓存）",
        value=False,
    )
    analyze_btn = pn.widgets.Button(
        name="🔍 开始分析",
        button_type="primary",
        width=140,
    )
    spinner = pn.indicators.LoadingSpinner(
        value=False, size=20, width=30, visible=False,
    )
    status = pn.pane.Markdown(
        "**请在上方输入股票代码，点击「开始分析」**",
        width=600,
    )
    report_pane = pn.pane.Markdown(
        "",
        sizing_mode="stretch_width",
        styles={
            "padding": "16px",
            "background": "#fafafa",
            "border": "1px solid #e0e0e0",
            "border-radius": "6px",
            "min-height": "200px",
        },
    )

    async def _on_analyze(event):
        code = (symbol_input.value or "").strip().upper()
        if not code:
            status.object = "**⚠️ 请输入股票代码**"
            return

        analyze_btn.disabled = True
        spinner.visible = True
        spinner.value = True
        status.object = (
            f"**⏳ 正在分析 `{code}`**（预计 50~90 秒）\n\n"
            f"- 抓取数据中...\n"
            f"- 4 个分析师并行分析中...\n"
            f"- 综合评审中...\n\n"
            f"请耐心等待，不要关闭页面。"
        )
        report_pane.object = ""

        try:
            result = await analyze_full(
                code,
                save=True,
                use_cache=not force_refresh.value,
                scenario_hint=scenario_select.value or "",
                force_refresh=force_refresh.value,
            )

            if result.get("cached"):
                status.object = (
                    f"**✅ 命中当天缓存**（`{result['report_path'].name}`）\n\n"
                    f"如需重新分析，请勾选「强制刷新」。"
                )
            else:
                status.object = (
                    f"**✅ 分析完成** · "
                    f"股票 `{result['name']}` · "
                    f"耗时 {result['elapsed']}s · "
                    f"报告已保存到 `{result['report_path'].name}`"
                )
            report_pane.object = result["report"]

        except Exception as e:
            logger.exception("analyze failed")
            status.object = f"**❌ 分析失败**：`{type(e).__name__}: {e}`"
        finally:
            analyze_btn.disabled = False
            spinner.value = False
            spinner.visible = False

    analyze_btn.on_click(_on_analyze)

    return pn.Column(
        pn.pane.Markdown("## 📊 个股分析"),
        pn.pane.Markdown(
            "输入股票代码，系统将抓取财报/公告/行情，"
            "调用 4 个分析师并行分析，由综合评审输出投资建议。"
            "\n\n分析结果自动保存到 `reports/` 目录。",
            styles={"color": "#666"},
        ),
        pn.Row(symbol_input, scenario_select, force_refresh),
        pn.Row(analyze_btn, spinner, status),
        pn.layout.Divider(),
        report_pane,
        sizing_mode="stretch_width",
        styles={"padding": "16px"},
    )


# ============================================================
# 复盘 Tab
# ============================================================
def _build_reflect_tab() -> pn.Column:
    import pandas as pd
    from .analysis.reflection import (
        extract_predictions, reflect_and_save, _verdict_label,
    )

    symbol_input = pn.widgets.TextInput(
        name="股票代码",
        placeholder="例如 688508.SH",
        width=200,
    )
    reflect_btn = pn.widgets.Button(
        name="🔄 开始复盘",
        button_type="warning",
        width=140,
    )
    spinner = pn.indicators.LoadingSpinner(
        value=False, size=20, width=30, visible=False,
    )
    status = pn.pane.Markdown(
        "**输入股票代码，点击「开始复盘」**",
        width=600,
    )

    reports_table = pn.widgets.Tabulator(
        pd.DataFrame(columns=[
            "报告文件", "分析日期", "推荐", "区间",
            "周期(日)", "状态",
        ]),
        page_size=10,
        show_index=False,
        sizing_mode="stretch_width",
        height=260,
        configuration={"editable": False},
    )

    reflections_pane = pn.pane.Markdown(
        "",
        sizing_mode="stretch_width",
        styles={
            "padding": "16px",
            "background": "#fffdf5",
            "border": "1px solid #f0e0b0",
            "border-radius": "6px",
            "min-height": "200px",
        },
    )

    async def _on_reflect(event):
        code = (symbol_input.value or "").strip().upper()
        if not code:
            status.object = "**⚠️ 请输入股票代码**"
            return

        reflect_btn.disabled = True
        spinner.visible = True
        spinner.value = True
        status.object = f"**⏳ 正在复盘 `{code}`**..."
        reports_table.value = pd.DataFrame()
        reflections_pane.object = ""

        try:
            # ---- 1. 列出历史报告 ----
            preds = extract_predictions(code)
            if not preds:
                status.object = (
                    f"**⚠️ 未找到 `{code}` 的历史报告**\n\n"
                    f"请先在「分析」Tab 生成至少一份报告。"
                )
                return

            rows = []
            for p in preds:
                rows.append({
                    "报告文件": p.report_path.name,
                    "分析日期": p.analysis_date.strftime("%Y-%m-%d"),
                    "推荐": p.recommendation,
                    "区间": f"[{p.target_low}%, {p.target_high}%]",
                    "周期(日)": p.target_period,
                    "状态": "待验证",
                })
            reports_table.value = pd.DataFrame(rows)

            # ---- 2. 执行完整复盘 ----
            result = await reflect_and_save(code)

            # ---- 3. 更新表格状态 ----
            verdict_map = {
                v.prediction.analysis_date.strftime("%Y-%m-%d"): v
                for v in result.get("verdicts", [])
            }
            for i, row in enumerate(rows):
                d = row["分析日期"]
                if d in verdict_map:
                    v = verdict_map[d]
                    rows[i]["状态"] = (
                        f"{_verdict_label(v.verdict)}（{v.actual_return:+.2f}%）"
                    )
            reports_table.value = pd.DataFrame(rows)

            # ---- 4. 展示反思 ----
            n = len(result.get("verdicts", []))
            n_rules = result.get("candidates_count", 0)

            if n == 0:
                status.object = (
                    f"**ℹ️ 共 {len(preds)} 份报告，暂无到期可验证的**\n\n"
                    f"（未到期的报告需等待 `target_period` 个交易日后才能复盘）"
                )
                return

            status.object = (
                f"**✅ 复盘完成** · "
                f"验证 {n} 条 · 候选规则 {n_rules} 条\n\n"
                f"反思文件：`{result['reflection_path'].name if result['reflection_path'] else '-'}`"
            )

            if result["reflection_path"] and result["reflection_path"].exists():
                text = result["reflection_path"].read_text(encoding="utf-8")
                reflections_pane.object = text

        except Exception as e:
            logger.exception("reflect failed")
            status.object = f"**❌ 复盘失败**：`{type(e).__name__}: {e}`"
        finally:
            reflect_btn.disabled = False
            spinner.value = False
            spinner.visible = False

    reflect_btn.on_click(_on_reflect)

    return pn.Column(
        pn.pane.Markdown("## 🔄 复盘验证"),
        pn.pane.Markdown(
            "系统会读取该股票的所有历史报告，用**实际股价**验证预测准确性，"
            "并由 LLM 生成反思，沉淀为**候选规则**写入 `rules/_pending.md`。",
            styles={"color": "#666"},
        ),
        pn.Row(symbol_input, reflect_btn, spinner, status),
        pn.layout.Divider(),
        pn.pane.Markdown("### 历史报告"),
        reports_table,
        pn.layout.Divider(),
        pn.pane.Markdown("### 反思汇总"),
        reflections_pane,
        sizing_mode="stretch_width",
        styles={"padding": "16px"},
    )


# ============================================================
# 规则库 Tab
# ============================================================
def _build_rules_tab() -> pn.Column:
    from pathlib import Path
    from .analysis.rule_book import (
        load_global_rules, load_scenario_rules,
        list_rules, list_pending, invalidate_cache,
        VALID_SCENARIOS, RULES_DIR, SCENARIOS_DIR,
    )

    # ==================== 状态 ====================
    state = {"pending_blocks": []}   # 缓存已解析的 pending

    # ==================== 顶部控件 ====================
    scenario_select = pn.widgets.Select(
        name="场景",
        options=list(VALID_SCENARIOS),
        value="high_growth",
        width=180,
    )
    refresh_btn = pn.widgets.Button(
        name="🔄 刷新", button_type="default", width=100,
    )
    summary = pn.pane.Markdown("", width=600)

    # ==================== 展示区 ====================
    global_pane = pn.pane.Markdown(
        "",
        sizing_mode="stretch_width",
        styles={
            "padding": "12px", "background": "#f8f9fa",
            "border": "1px solid #e0e0e0", "border-radius": "6px",
        },
    )
    scenario_pane = pn.pane.Markdown(
        "",
        sizing_mode="stretch_width",
        styles={
            "padding": "12px", "background": "#f0f7ff",
            "border": "1px solid #c0d8f0", "border-radius": "6px",
        },
    )

    # ==================== Pending 列表（可操作） ====================
    # pending_list = pn.Column(sizing_mode="stretch_width")
    pending_list = pn.Column(
        pn.Spacer(height=0),
        sizing_mode="stretch_width",
    )

    # ==================== 编辑弹窗 ====================
    edit_overlay = pn.Column(
        pn.Spacer(height=0),   # ← 占位子组件，避免 EMPTY_LAYOUT 警告
        visible=False,
        sizing_mode="stretch_width",
    )
    edit_target = {"path": None}

    def _open_editor(path: Path, label: str):
        if not path.exists():
            path.write_text("", encoding="utf-8")

        current = path.read_text(encoding="utf-8")

        editor = pn.widgets.TextAreaInput(
            value=current,
            height=500,
            sizing_mode="stretch_width",
            styles={"font-family": "monospace", "font-size": "13px"},
        )
        save_btn = pn.widgets.Button(
            name="💾 保存", button_type="primary", width=100,
        )
        cancel_btn = pn.widgets.Button(
            name="✖ 取消", button_type="default", width=100,
        )
        msg = pn.pane.Markdown("")

        def _save(event):
            try:
                path.write_text(editor.value, encoding="utf-8")
                invalidate_cache()
                msg.object = f"**✅ 已保存** `{path.name}`"
                _load_all()
            except Exception as e:
                msg.object = f"**❌ 保存失败**：{e}"

        def _cancel(event):
            edit_overlay.visible = False
            edit_overlay.clear()

        save_btn.on_click(_save)
        cancel_btn.on_click(_cancel)

        edit_overlay.clear()
        edit_overlay.append(pn.pane.Markdown(f"### ✏️ 编辑 `{path.relative_to(RULES_DIR.parent)}`"))
        edit_overlay.append(pn.Row(save_btn, cancel_btn, msg))
        edit_overlay.append(editor)
        edit_overlay.visible = True

    # ==================== 采纳逻辑 ====================
    def _adopt_rule(block_text: str, block_idx: int, target_scope: str):
        """把一条 pending 规则迁移到目标文件。"""
        try:
            # 1. 解析规则内容
            rule_id_m = __import__("re").search(
                r"\*\*rule_id\*\*：`([^`]+)`", block_text,
            )
            name_m = __import__("re").search(
                r"\*\*名称\*\*：(.*?)(?:\n|$)", block_text,
            )
            scenario_m = __import__("re").search(
                r"\*\*场景\*\*：`([^`]+)`", block_text,
            )
            rule_m = __import__("re").search(
                r"\*\*规则\*\*：(.*?)(?=\n-\s*\*\*|\Z)", block_text, __import__("re").DOTALL,
            )
            reason_m = __import__("re").search(
                r"\*\*理由\*\*：(.*?)(?=\n-\s*\*\*|\Z)", block_text, __import__("re").DOTALL,
            )
            action_m = __import__("re").search(
                r"\*\*动作\*\*：(.*?)(?=\n-\s*\*\*|\Z)", block_text, __import__("re").DOTALL,
            )

            rule_id = (rule_id_m.group(1).strip() if rule_id_m
                       else f"R-{datetime.now().strftime('%Y%m%d%H%M')}")
            name = (name_m.group(1).strip() if name_m
                    else "未命名规则")
            rule_text = (rule_m.group(1).strip() if rule_m
                         else "（未提供）")
            reason_text = (reason_m.group(1).strip() if reason_m
                           else "（未提供）")
            action_text = (action_m.group(1).strip() if action_m
                           else "（未提供）")

            # 2. 选择目标文件
            target_scope = target_scope.strip().lower()
            if target_scope == "global":
                target_path = RULES_DIR / "global.md"
                prefix = "R-G"
            elif target_scope in VALID_SCENARIOS:
                SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
                target_path = SCENARIOS_DIR / f"{target_scope}.md"
                prefix = "R-HG" if target_scope == "high_growth" else "R-S"
            else:
                return False, f"非法目标：{target_scope}"

            # 3. 规范化新规则段
            new_block = (
                f"\n### {rule_id} {name}\n\n"
                f"- **规则**：{rule_text}\n"
                f"- **理由**：{reason_text}\n"
                f"- **动作**：{action_text}\n"
            )

            # 4. 写入目标文件
            current = target_path.read_text(encoding="utf-8") if target_path.exists() else ""

            # 找"## 更新日志"前的最后一个规则段，插进去
            update_log_marker = "## 更新日志"
            if update_log_marker in current:
                idx = current.index(update_log_marker)
                # 在更新日志前插
                new_content = current[:idx].rstrip() + "\n\n---\n" + new_block + "\n\n---\n\n" + current[idx:]
            else:
                # 没有更新日志，直接追加
                new_content = current.rstrip() + "\n\n---\n" + new_block

            # 更新日志
            date_str = datetime.now().strftime("%Y-%m-%d")
            log_line = f"| {date_str} | 新增 {rule_id} | 从 _pending.md 采纳 |\n"
            if "| 日期 | 变更 | 来源 |" in new_content:
                # 已有表格，追加行
                table_start = new_content.index("| 日期 | 变更 | 来源 |")
                table_end = new_content.index("\n\n", table_start) if "\n\n" in new_content[table_start:] else len(new_content)
                new_content = (
                    new_content[:table_end].rstrip() + "\n" + log_line
                    + new_content[table_end:]
                )
            else:
                new_content += f"\n\n## 更新日志\n\n| 日期 | 变更 | 来源 |\n| :--- | :--- | :--- |\n{log_line}"

            target_path.write_text(new_content, encoding="utf-8")

            # 5. 从 _pending.md 里删除这段
            pending_path = RULES_DIR / "_pending.md"
            pending_text = pending_path.read_text(encoding="utf-8")
            if block_text in pending_text:
                pending_text = pending_text.replace(block_text, "")
                pending_path.write_text(pending_text, encoding="utf-8")

            invalidate_cache()
            return True, f"已采纳 `{rule_id}` → `{target_path.name}`"
        except Exception as e:
            import logging
            logging.getLogger("stock2.analysis_page").exception("adopt failed")
            return False, f"{type(e).__name__}: {e}"

    def _make_pending_card(block_text: str, idx: int, time_str: str):
        """为一条 pending 生成卡片。"""
        # 提取标题
        title_m = __import__("re").search(
            r"\*\*名称\*\*：(.*?)(?:\n|$)", block_text,
        ) or __import__("re").search(
            r"### 规则 \d+\s*\n", block_text,
        )
        title = title_m.group(1).strip() if title_m and title_m.re.groups >= 1 else f"规则 {idx + 1}"

        preview_md = pn.pane.Markdown(
            f"**{title}**\n\n{block_text[:500]}{'...' if len(block_text) > 500 else ''}",
            sizing_mode="stretch_width",
            styles={"font-size": "13px"},
        )

        target_select = pn.widgets.Select(
            options={
                "→ 全局规则（global.md）": "global",
                "→ 高成长股": "high_growth",
                "→ 价值股": "value",
                "→ 周期股": "cyclical",
                "→ 反转股": "turnaround",
            },
            value="global",
            width=240,
        )
        adopt_btn = pn.widgets.Button(
            name="✅ 采纳", button_type="success", width=90,
        )
        msg = pn.pane.Markdown("", width=400)

        def _adopt(event):
            ok, m = _adopt_rule(block_text, idx, target_select.value)
            msg.object = f"**{'✅' if ok else '❌'}** {m}"
            if ok:
                _load_all()

        adopt_btn.on_click(_adopt)

        return pn.Column(
            preview_md,
            pn.Row(
                pn.pane.Markdown(f"*提议时间：{time_str}*", width=250),
                target_select,
                adopt_btn,
                msg,
                sizing_mode="stretch_width",
            ),
            sizing_mode="stretch_width",
            styles={
                "padding": "10px 12px",
                "background": "#fffdf5",
                "border": "1px solid #f0e0b0",
                "border-radius": "6px",
                "margin-bottom": "8px",
            },
        )

    def _load_all():
        invalidate_cache()

        # ---- global ----
        g = load_global_rules()
        global_pane.object = g if g else "*(全局规则为空)*"

        # ---- scenario ----
        s = load_scenario_rules(scenario_select.value)
        scenario_pane.object = (
            s if s else f"*(场景 `{scenario_select.value}` 暂无规则)*"
        )

        # ---- pending ----
        import re
        pending_path = RULES_DIR / "_pending.md"
        pending_list.clear()
        pending_list.append(pn.Spacer(height=0))   # ← 加这个
        state["pending_blocks"] = []

        if pending_path.exists():
            text = pending_path.read_text(encoding="utf-8")
            # 每段以 "### 规则 N" 开头
            blocks = re.findall(
                r"(### 规则 \d+\n(?:.*?)(?=\n### 规则 |\n## |\Z))",
                text, re.DOTALL,
            )
            # 时间：从最近的 "## 提议时间：" 里读
            time_matches = re.findall(r"## 提议时间：(\S+ \S+)", text)
            latest_time = time_matches[-1] if time_matches else "?"

            if blocks:
                pending_list.append(pn.pane.Markdown(
                    f"**共 {len(blocks)} 条待审核规则**（最新提议：{latest_time}）"
                ))
                for i, b in enumerate(blocks):
                    state["pending_blocks"].append(b)
                    pending_list.append(_make_pending_card(b, i, latest_time))
            else:
                pending_list.append(pn.pane.Markdown("*(暂无待审核规则)*"))
        else:
            pending_list.append(pn.pane.Markdown("*(暂无待审核规则)*"))

        # ---- summary ----
        rules = list_rules()
        pend = state["pending_blocks"]
        summary.object = (
            f"**规则统计**：共 **{len(rules)}** 条"
            f"（global: {sum(1 for r in rules if r['scope'] == 'global')}，"
            f"scenarios: {sum(1 for r in rules if r['scope'] != 'global')}）"
            f" · 待审核 **{len(pend)}** 条"
        )

    def _on_refresh(event):
        _load_all()

    def _on_scenario_change(event):
        _load_all()

    refresh_btn.on_click(_on_refresh)
    scenario_select.param.watch(_on_scenario_change, "value")

    # ---- 编辑按钮 ----
    edit_global_btn = pn.widgets.Button(
        name="✏️ 编辑 global.md", button_type="default", width=160,
    )
    edit_scenario_btn = pn.widgets.Button(
        name="✏️ 编辑当前场景", button_type="default", width=160,
    )

    def _on_edit_global(event):
        _open_editor(RULES_DIR / "global.md", "global.md")

    def _on_edit_scenario(event):
        path = SCENARIOS_DIR / f"{scenario_select.value}.md"
        _open_editor(path, path.name)

    edit_global_btn.on_click(_on_edit_global)
    edit_scenario_btn.on_click(_on_edit_scenario)

    # ---- 首次加载 ----
    _load_all()

    return pn.Column(
        pn.pane.Markdown("## 📖 规则库"),
        pn.pane.Markdown(
            "本页展示 **已生效规则** 和 **待审核规则**。"
            "待审核规则可一键采纳到正式规则，也可直接编辑 md 文件。",
            styles={"color": "#666"},
        ),
        pn.Row(
            pn.pane.Markdown("**场景选择**"),
            scenario_select,
            refresh_btn,
            summary,
            sizing_mode="stretch_width",
        ),
        pn.layout.Divider(),
        pn.pane.Markdown("### 🌐 全局规则"),
        pn.Row(edit_global_btn),
        global_pane,
        pn.layout.Divider(),
        pn.pane.Markdown("### 🎯 场景规则"),
        pn.Row(edit_scenario_btn),
        scenario_pane,
        pn.layout.Divider(),
        pn.pane.Markdown("### 📝 待审核规则"),
        pn.pane.Markdown(
            "*审核通过后点「✅ 采纳」，规则自动迁移到目标文件。*",
            styles={"color": "#999", "font-size": "12px"},
        ),
        edit_overlay,
        pending_list,
        sizing_mode="stretch_width",
        styles={"padding": "16px"},
    )


# ============================================================
# 工厂函数
# ============================================================
def build_analysis_page():
    def _factory():
        username, role = _get_current_user()
        logger.info("analysis page session | user=%s | role=%s", username, role)

        tabs = pn.Tabs(
            ("📊 分析", _build_analysis_tab()),
            ("🔄 复盘", _build_reflect_tab()),
            ("📖 规则库", _build_rules_tab()),
            dynamic=False,
            sizing_mode="stretch_width",
        )

        return pn.Column(
            pn.pane.Markdown(
                f"### 🧠 智能分析中心 · `{username}`",
                margin=(10, 0, 10, 0),
            ),
            tabs,
            sizing_mode="stretch_width",
        )

    return _factory