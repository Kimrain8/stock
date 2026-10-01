"""分析页面 — 5 个 Tab：分析 / 批量分析 / 相似股扫描 / 复盘 / 规则库"""
import logging

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
        logger.warning("get current user failed | %s", e)
        return "anonymous", "user"


# ============================================================
# Tab 1：单只分析
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
# Tab 2：批量分析
# ============================================================
def _build_batch_tab(username: str,
                     shared_batch_input: pn.widgets.TextAreaInput) -> pn.Column:
    """批量分析 Tab。

    参数：
        username: 用于发送结果到工作台
        shared_batch_input: 由 build_analysis_page 传入的共享输入控件，
                            相似股扫描 Tab 的"发送"会写入它。
    """
    import re
    import asyncio
    import pandas as pd
    from .analysis.agents import analyze_full
    from .analysis.data_cache import get_data, refresh_cache, list_cache
    from .shared_state import set_pending

    # ---------- 使用共享输入控件 ----------
    codes_input = shared_batch_input

    # ---------- 控件 ----------
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
        name="强制刷新数据缓存", value=False,
    )

    warm_btn = pn.widgets.Button(
        name="📥 下载数据到缓存",
        button_type="default",
        width=160,
    )
    run_btn = pn.widgets.Button(
        name="🚀 开始批量分析",
        button_type="primary",
        width=160,
    )
    stop_btn = pn.widgets.Button(
        name="⏹ 停止",
        button_type="danger",
        width=100,
        disabled=True,
    )
    refresh_list_btn = pn.widgets.Button(
        name="🔄 刷新清单",
        button_type="default",
        width=120,
    )

    spinner = pn.indicators.LoadingSpinner(
        value=False, size=20, width=30, visible=False,
    )

    status = pn.pane.Markdown(
        "**推荐流程**：\n"
        "1. 粘贴股票代码（每行一个），或从「🎯 相似股扫描」一键发送\n"
        "2. 点「📥 下载数据到缓存」\n"
        "3. 点「🚀 开始批量分析」",
        width=700,
    )

    list_summary = pn.pane.Markdown("", width=500)

    list_table = pn.widgets.Tabulator(
        pd.DataFrame(columns=["股票代码", "缓存时间", "大小(KB)"]),
        page_size=15,
        sizing_mode="stretch_width",
        height=260,
        show_index=False,
        header_filters=True,
        configuration={"editable": False},
    )

    # ---------- 分析结果表 ----------
    results_table = pn.widgets.Tabulator(
        pd.DataFrame(columns=[
            "序号", "股票代码", "名称", "状态", "评级", "置信度",
            "耗时(s)", "报告路径", "错误",
        ]),
        page_size=20,
        sizing_mode="stretch_width",
        height=300,
        selectable=True,
        show_index=False,
        configuration={"editable": False},
    )

    # ---------- 发送到工作台 ----------
    send_wb_btn = pn.widgets.Button(
        name="📤 发送结果到工作台",
        button_type="success",
        width=200,
    )
    send_wb_msg = pn.pane.Markdown("", width=500)

    stop_flag = {"stop": False}

    # ============================================================
    # 缓存清单
    # ============================================================
    def _refresh_cache_list():
        try:
            items = list_cache()
            if not items:
                list_table.value = pd.DataFrame(
                    columns=["股票代码", "缓存时间", "大小(KB)"]
                )
                list_summary.object = "**缓存清单**：0 只"
                return
            rows = [{
                "股票代码": it["ts_code"],
                "缓存时间": str(it.get("updated_at", ""))[:19],
                "大小(KB)": it.get("size_kb", 0),
            } for it in items]
            df = pd.DataFrame(rows)
            list_table.value = df
            list_summary.object = f"**缓存清单**：共 **{len(df)}** 只"
        except Exception as e:
            logger.warning("refresh cache list failed | %s", e)
            list_summary.object = f"**缓存清单读取失败**：{e}"

    def _on_refresh_list(event):
        _refresh_cache_list()

    refresh_list_btn.on_click(_on_refresh_list)

    # ============================================================
    # 代码解析
    # ============================================================
    def _parse_codes(text: str) -> list[str]:
        text = text or ""
        tokens = re.split(r"[\s,;，；]+", text)
        codes, seen = [], set()
        for t in tokens:
            t = t.strip().upper()
            if not t:
                continue
            if re.match(r"^\d{6}(\.(SH|SZ|BJ))?$", t):
                if "." not in t:
                    if t.startswith(("60", "68", "51")):
                        t += ".SH"
                    elif t.startswith(("00", "30", "12")):
                        t += ".SZ"
                    elif t.startswith(("43", "83", "87", "92")):
                        t += ".BJ"
                if t not in seen:
                    seen.add(t)
                    codes.append(t)
        return codes

    # ============================================================
    # 下载缓存
    # ============================================================
    async def _on_warm(event):
        codes = _parse_codes(codes_input.value)
        if not codes:
            status.object = "**⚠️ 未识别到有效的股票代码**"
            return

        warm_btn.disabled = True
        run_btn.disabled = True
        spinner.visible = True
        spinner.value = True

        ok, fail = 0, 0
        for i, code in enumerate(codes):
            status.object = (
                f"**📥 下载缓存 {i+1}/{len(codes)}** · `{code}`\n\n"
                f"- 成功 {ok} · 失败 {fail}"
            )
            try:
                if force_refresh.value:
                    await asyncio.to_thread(refresh_cache, code)
                else:
                    await asyncio.to_thread(get_data, code, 24.0)
                ok += 1
            except Exception as e:
                logger.exception("warm failed | %s", code)
                fail += 1
            _refresh_cache_list()

        status.object = (
            f"**✅ 缓存下载完成** · 成功 **{ok}** / 失败 **{fail}** / "
            f"共 {len(codes)}\n\n现在可以点「🚀 开始批量分析」。"
        )
        warm_btn.disabled = False
        run_btn.disabled = False
        spinner.value = False
        spinner.visible = False

    # ============================================================
    # 批量分析
    # ============================================================
    async def _run_batch(codes: list[str]):
        rows = [{
            "序号": i, "股票代码": c, "名称": "", "状态": "待处理",
            "评级": "", "置信度": "", "耗时(s)": "",
            "报告路径": "", "错误": "",
        } for i, c in enumerate(codes, 1)]
        df = pd.DataFrame(rows)
        results_table.value = df

        ok, fail = 0, 0
        for i, code in enumerate(codes):
            if stop_flag["stop"]:
                status.object = (
                    f"**⏹ 已停止** · 成功 {ok} / 失败 {fail} / 共 {len(codes)}"
                )
                break

            df.at[i, "状态"] = "🔄 分析中"
            results_table.value = df
            status.object = (
                f"**⏳ 分析 {i+1}/{len(codes)}** · `{code}`\n\n"
                f"- 成功 {ok} · 失败 {fail}"
            )

            try:
                result = await analyze_full(
                    code, save=True,
                    use_cache=not force_refresh.value,
                    scenario_hint=scenario_select.value or "",
                    force_refresh=force_refresh.value,
                )

                rec, conf = "", ""
                try:
                    import yaml
                    if result.get("report"):
                        m = re.match(
                            r"^---\s*\n(.*?)\n---",
                            result["report"], re.DOTALL,
                        )
                        if m:
                            meta = yaml.safe_load(m.group(1)) or {}
                            rec = str(meta.get("recommendation", ""))
                            conf = str(meta.get("confidence", ""))
                except Exception:
                    pass

                df.at[i, "名称"] = result.get("name", "")
                df.at[i, "状态"] = (
                    "✅ 缓存命中" if result.get("cached") else "✅ 完成"
                )
                df.at[i, "评级"] = rec
                df.at[i, "置信度"] = conf
                df.at[i, "耗时(s)"] = str(result.get("elapsed", ""))
                df.at[i, "报告路径"] = (
                    result["report_path"].name
                    if result.get("report_path") else ""
                )
                ok += 1
            except Exception as e:
                logger.exception("batch item failed | %s", code)
                df.at[i, "状态"] = "❌ 失败"
                df.at[i, "错误"] = f"{type(e).__name__}: {str(e)[:100]}"
                fail += 1

            results_table.value = df

        stop_flag["stop"] = False

    async def _on_run(event):
        codes = _parse_codes(codes_input.value)
        if not codes:
            status.object = "**⚠️ 未识别到有效的股票代码**"
            return

        run_btn.disabled = True
        warm_btn.disabled = True
        stop_btn.disabled = False
        spinner.visible = True
        spinner.value = True
        stop_flag["stop"] = False

        status.object = f"**🚀 开始批量分析** · 共 **{len(codes)}** 只"
        try:
            await _run_batch(codes)
        finally:
            run_btn.disabled = False
            warm_btn.disabled = False
            stop_btn.disabled = True
            spinner.value = False
            spinner.visible = False

    def _on_stop(event):
        stop_flag["stop"] = True
        status.object = "**⏹ 收到停止信号，等待当前任务完成...**"

    warm_btn.on_click(_on_warm)
    run_btn.on_click(_on_run)
    stop_btn.on_click(_on_stop)

    # ============================================================
    # 发送到工作台
    # ============================================================
    def _on_send_wb(event):
        try:
            df = results_table.value
            if df is None or df.empty:
                send_wb_msg.object = "**⚠️ 结果为空，请先批量分析**"
                return
            set_pending(username, "analysis_result", {
                "df": df.copy(),
                "ts_code": "batch",
            })
            send_wb_msg.object = (
                f"**✅ 已发送 {len(df)} 行到工作台**\n\n"
                f"去 `/workbench` → 点「📤 拉取 /analysis 结果」"
            )
        except Exception as e:
            logger.exception("send wb failed")
            send_wb_msg.object = f"**❌ {type(e).__name__}: {e}**"

    send_wb_btn.on_click(_on_send_wb)

    # 首次加载显示已有缓存
    _refresh_cache_list()

    # ============================================================
    # 布局
    # ============================================================
    return pn.Column(
        pn.pane.Markdown("## 📋 批量分析"),
        pn.pane.Markdown(
            "**推荐流程**：\n"
            "1. 粘贴股票代码（每行一个），或从「🎯 相似股扫描」一键发送\n"
            "2. 点「📥 下载数据到缓存」\n"
            "3. 点「🚀 开始批量分析」\n"
            "4. 结果可「📤 发送结果到工作台」进一步处理",
            styles={"color": "#666"},
        ),
        codes_input,
        pn.Row(scenario_select, force_refresh),
        pn.Row(warm_btn, run_btn, stop_btn, spinner),
        status,
        pn.layout.Divider(),
        pn.pane.Markdown("### 📦 缓存清单（全部已缓存股票）"),
        pn.Row(refresh_list_btn, list_summary),
        list_table,
        pn.layout.Divider(),
        pn.pane.Markdown("### 📊 分析结果"),
        results_table,
        pn.Row(send_wb_btn, send_wb_msg),
        sizing_mode="stretch_width",
        styles={"padding": "16px"},
    )


# ============================================================
# Tab 3：相似股扫描
# ============================================================
def _build_similar_tab(username: str,
                       shared_batch_input: pn.widgets.TextAreaInput) -> pn.Column:
    """相似股扫描 Tab（初筛）。

    参数：
        username: 用于从共享状态拉取 /scan 结果
        shared_batch_input: 发送时写入这个共享控件，批量 Tab 会读到
    """
    import pandas as pd
    import asyncio
    from .analysis.feature_extractor import extract_batch, FEATURE_NAMES
    from .analysis.feature_store import (
        load_features, build_profile, list_feature_snapshots,
    )
    from .analysis.similarity_scanner import scan_similar
    from .analysis.data_cache import get_data
    from .shared_state import get_pending

    # ---------- 样本来源 ----------
    sample_input = pn.widgets.TextAreaInput(
        name="样本股票代码",
        placeholder="每行一个代码（来自 /scan 的历史大牛股）",
        height=120,
        sizing_mode="stretch_width",
        styles={"font-family": "monospace", "font-size": "13px"},
    )

    # ---------- 从 /scan 拉取 ----------
    pull_btn = pn.widgets.Button(
        name="📥 从 /scan 拉取",
        button_type="default",
        width=160,
    )
    pull_msg = pn.pane.Markdown("", width=500)

    def _on_pull(event):
        codes = get_pending(username, "codes")
        if not codes:
            pull_msg.object = (
                "**⚠️ 无待处理代码，请先在 /scan 点「📤 发送到相似股扫描」**"
            )
            return
        sample_input.value = codes
        pull_msg.object = (
            f"**✅ 已拉取 {len(codes.splitlines())} 个代码**"
        )

    pull_btn.on_click(_on_pull)

    # ---------- 特征库状态 ----------
    snapshot_info = pn.pane.Markdown("", width=500)

    top_n_input = pn.widgets.IntInput(
        name="返回 Top N", value=50, start=10, end=500, width=100,
    )

    scan_btn = pn.widgets.Button(
        name="🎯 扫描相似股", button_type="primary", width=160,
    )
    spinner = pn.indicators.LoadingSpinner(
        value=False, size=20, width=30, visible=False,
    )
    status = pn.pane.Markdown("**先填入样本代码，再点扫描**", width=500)

    result_table = pn.widgets.Tabulator(
        pd.DataFrame(),
        page_size=20,
        sizing_mode="stretch_width",
        height=400,
        selectable="checkbox",
        show_index=False,
        configuration={"editable": False},
    )

    send_btn = pn.widgets.Button(
        name="📤 发送到批量分析",
        button_type="success",
        width=180,
    )
    send_msg = pn.pane.Markdown("", width=500)

    # ---------- 特征库状态 ----------
    def _refresh_snapshot_info():
        try:
            snaps = list_feature_snapshots()
        except Exception as e:
            snapshot_info.object = f"**特征库读取失败**：{e}"
            return
        if not snaps:
            snapshot_info.object = (
                "**⚠️ 无特征快照**，请先运行 "
                "`python scripts\\warm_features.py --source hs300`"
            )
            return
        latest = snaps[0]
        snapshot_info.object = (
            f"**特征库**：{latest['tag']} · "
            f"{latest['size_kb']}KB · "
            f"{latest['updated_at'][:19]}"
        )

    _refresh_snapshot_info()

    # ---------- 扫描 ----------
    async def _on_scan(event):
        codes = [
            c.strip().upper()
            for c in (sample_input.value or "").splitlines()
            if c.strip()
        ]
        if not codes:
            status.object = "**⚠️ 请输入样本代码**"
            return

        scan_btn.disabled = True
        spinner.visible = True
        spinner.value = True
        status.object = f"**⏳ 提取 {len(codes)} 个样本的特征...**"

        try:
            sample_rows = []
            for code in codes:
                try:
                    data = await asyncio.to_thread(get_data, code, 24.0)
                    feats = extract_batch(data, as_of_date=None)
                    feats["ts_code"] = code
                    sample_rows.append(feats)
                except Exception as e:
                    logger.warning("extract sample failed | %s | %s", code, e)

            if not sample_rows:
                status.object = "**❌ 样本特征提取失败**"
                return

            samples_df = pd.DataFrame(sample_rows)
            valid_feats = [f for f in FEATURE_NAMES if f in samples_df.columns]
            profile = build_profile(samples_df[valid_feats])

            status.object = (
                f"**✅ 画像构建完成**（{len(samples_df)} 个样本）\n\n"
                f"**⏳ 正在扫描全市场...**"
            )

            universe = load_features()
            if universe is None or universe.empty:
                status.object = (
                    "**❌ 特征库为空**，请先运行 "
                    "`python scripts\\warm_features.py --source hs300`"
                )
                return

            result = scan_similar(universe, profile, top_n=top_n_input.value)
            if result.empty:
                status.object = "**⚠️ 无匹配结果**"
                return

            display_cols = [
                "ts_code", "name", "score", "matched_count",
                "price_level", "price_percentile_250d",
                "turnover_20d", "volume_ratio_20_60",
                "holder_num_change",
            ]
            display_cols = [c for c in display_cols if c in result.columns]
            result_table.value = result[display_cols].copy()

            status.object = (
                f"**✅ 扫描完成** · 命中 **{len(result)}** 只\n\n"
                f"勾选后点「📤 发送到批量分析」"
            )
        except Exception as e:
            logger.exception("scan failed")
            status.object = f"**❌ 失败**：{type(e).__name__}: {e}"
        finally:
            scan_btn.disabled = False
            spinner.value = False
            spinner.visible = False

    # ---------- 发送到批量 Tab ----------
    def _on_send(event):
        try:
            df = result_table.value
            if df is None or df.empty:
                send_msg.object = "**⚠️ 结果为空**"
                return
            sel = result_table.selection
            if not sel:
                codes = df["ts_code"].tolist()
                prefix = "全部"
            else:
                codes = df.iloc[list(sel)]["ts_code"].tolist()
                prefix = "勾选"
            if not codes:
                send_msg.object = "**⚠️ 无有效代码**"
                return
            shared_batch_input.value = "\n".join(codes)
            send_msg.object = (
                f"**✅ 已发送 {len(codes)} 个代码（{prefix}）"
                f"到「📋 批量分析」Tab**"
            )
        except Exception as e:
            send_msg.object = f"**❌ {type(e).__name__}: {e}**"

    scan_btn.on_click(_on_scan)
    send_btn.on_click(_on_send)

    return pn.Column(
        pn.pane.Markdown("## 🎯 相似股扫描（初筛）"),
        pn.pane.Markdown(
            "**用途**：从 /scan 找出历史大牛股样本 → 提取**起涨前特征** → "
            "扫描当前市场，找出相似的股票。\n\n"
            "**后续**：勾选结果 → 「📤 发送到批量分析」→ 深度分析。",
            styles={"color": "#666"},
        ),
        snapshot_info,
        pn.layout.Divider(),
        pn.pane.Markdown("### 1. 样本代码（来自 /scan）"),
        sample_input,
        pn.Row(pull_btn, pull_msg),
        pn.Row(top_n_input, scan_btn, spinner),
        status,
        pn.layout.Divider(),
        pn.pane.Markdown("### 2. 初筛结果（Top N 相似股）"),
        result_table,
        pn.Row(send_btn, send_msg),
        sizing_mode="stretch_width",
        styles={"padding": "16px"},
    )


# ============================================================
# Tab 4：复盘
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

            result = await reflect_and_save(code)

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

            n = len(result.get("verdicts", []))
            n_rules = result.get("candidates_count", 0)

            if n == 0:
                status.object = (
                    f"**ℹ️ 共 {len(preds)} 份报告，暂无到期可验证的**"
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
# Tab 5：规则库
# ============================================================
def _build_rules_tab() -> pn.Column:
    from pathlib import Path
    from .analysis.rule_book import (
        load_global_rules, load_scenario_rules,
        list_rules, list_pending, invalidate_cache,
        VALID_SCENARIOS, RULES_DIR, SCENARIOS_DIR,
    )

    state = {"pending_blocks": []}

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

    pending_list = pn.Column(
        pn.Spacer(height=0),
        sizing_mode="stretch_width",
    )

    edit_overlay = pn.Column(
        pn.Spacer(height=0),
        visible=False,
        sizing_mode="stretch_width",
    )

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
        edit_overlay.append(
            pn.pane.Markdown(f"### ✏️ 编辑 `{path.relative_to(RULES_DIR.parent)}`")
        )
        edit_overlay.append(pn.Row(save_btn, cancel_btn, msg))
        edit_overlay.append(editor)
        edit_overlay.visible = True

    # ==================== 采纳 ====================
    def _adopt_rule(block_text: str, block_idx: int, target_scope: str):
        import re
        from datetime import datetime
        try:
            rule_id_m = re.search(
                r"\*\*rule_id\*\*：`([^`]+)`", block_text,
            )
            name_m = re.search(
                r"\*\*名称\*\*：(.*?)(?:\n|$)", block_text,
            )
            rule_m = re.search(
                r"\*\*规则\*\*：(.*?)(?=\n-\s*\*\*|\Z)",
                block_text, re.DOTALL,
            )
            reason_m = re.search(
                r"\*\*理由\*\*：(.*?)(?=\n-\s*\*\*|\Z)",
                block_text, re.DOTALL,
            )
            action_m = re.search(
                r"\*\*动作\*\*：(.*?)(?=\n-\s*\*\*|\Z)",
                block_text, re.DOTALL,
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

            target_scope = target_scope.strip().lower()
            if target_scope == "global":
                target_path = RULES_DIR / "global.md"
            elif target_scope in VALID_SCENARIOS:
                SCENARIOS_DIR.mkdir(parents=True, exist_ok=True)
                target_path = SCENARIOS_DIR / f"{target_scope}.md"
            else:
                return False, f"非法目标：{target_scope}"

            new_block = (
                f"\n### {rule_id} {name}\n\n"
                f"- **规则**：{rule_text}\n"
                f"- **理由**：{reason_text}\n"
                f"- **动作**：{action_text}\n"
            )

            current = (target_path.read_text(encoding="utf-8")
                       if target_path.exists() else "")

            update_log_marker = "## 更新日志"
            if update_log_marker in current:
                idx = current.index(update_log_marker)
                new_content = (
                    current[:idx].rstrip()
                    + "\n\n---\n" + new_block
                    + "\n\n---\n\n" + current[idx:]
                )
            else:
                new_content = current.rstrip() + "\n\n---\n" + new_block

            target_path.write_text(new_content, encoding="utf-8")

            pending_path = RULES_DIR / "_pending.md"
            if pending_path.exists():
                pending_text = pending_path.read_text(encoding="utf-8")
                if block_text in pending_text:
                    pending_text = pending_text.replace(block_text, "")
                    pending_path.write_text(pending_text, encoding="utf-8")

            invalidate_cache()
            return True, f"已采纳 `{rule_id}` → `{target_path.name}`"
        except Exception as e:
            logger.exception("adopt failed")
            return False, f"{type(e).__name__}: {e}"

    def _make_pending_card(block_text: str, idx: int, time_str: str):
        import re
        title_m = re.search(
            r"\*\*名称\*\*：(.*?)(?:\n|$)", block_text,
        ) or re.search(r"### 规则 \d+\s*\n", block_text)
        title = (title_m.group(1).strip()
                 if title_m and title_m.re.groups >= 1
                 else f"规则 {idx + 1}")

        preview_md = pn.pane.Markdown(
            f"**{title}**\n\n{block_text[:500]}"
            f"{'...' if len(block_text) > 500 else ''}",
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
        import re
        invalidate_cache()

        g = load_global_rules()
        global_pane.object = g if g else "*(全局规则为空)*"

        s = load_scenario_rules(scenario_select.value)
        scenario_pane.object = (
            s if s else f"*(场景 `{scenario_select.value}` 暂无规则)*"
        )

        pending_path = RULES_DIR / "_pending.md"
        pending_list.clear()
        pending_list.append(pn.Spacer(height=0))
        state["pending_blocks"] = []

        if pending_path.exists():
            text = pending_path.read_text(encoding="utf-8")
            blocks = re.findall(
                r"(### 规则 \d+\n(?:.*?)(?=\n### 规则 |\n## |\Z))",
                text, re.DOTALL,
            )
            time_matches = re.findall(r"## 提议时间：(\S+ \S+)", text)
            latest_time = time_matches[-1] if time_matches else "?"

            if blocks:
                pending_list.append(pn.pane.Markdown(
                    f"**共 {len(blocks)} 条待审核规则**"
                    f"（最新提议：{latest_time}）"
                ))
                for i, b in enumerate(blocks):
                    state["pending_blocks"].append(b)
                    pending_list.append(_make_pending_card(b, i, latest_time))
            else:
                pending_list.append(pn.pane.Markdown("*(暂无待审核规则)*"))
        else:
            pending_list.append(pn.pane.Markdown("*(暂无待审核规则)*"))

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
        logger.info("analysis page session | user=%s | role=%s",
                    username, role)

        # 共享输入控件：只有批量 Tab 会把它放进布局
        shared_batch_input = pn.widgets.TextAreaInput(
            name="股票代码列表（批量分析用）",
            placeholder="每行一个，或用逗号/空格分隔",
            height=140,
            sizing_mode="stretch_width",
            styles={"font-family": "monospace", "font-size": "13px"},
        )

        tabs = pn.Tabs(
            ("📊 分析", _build_analysis_tab()),
            ("📋 批量分析", _build_batch_tab(username, shared_batch_input)),
            ("🎯 相似股扫描", _build_similar_tab(username, shared_batch_input)),
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