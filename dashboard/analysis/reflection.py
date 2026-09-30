import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from . import report_store as rs
from .data_fetcher import fetch_quotes
from .agents import _call_llm, _load_prompt, _render

"""回溯验证 — 判定历史报告的准确性并生成反思。

流程：
    1. 读历史报告 → 解析 YAML frontmatter
    2. 判断是否到期可验证
    3. 查实际股价 → 计算实际收益
    4. 判定：方向 + 区间（双维度）
    5. LLM 生成反思
    6. 保存到 lessons_learned/
    7. 提取候选规则 → rules/_pending.md
"""

logger = logging.getLogger("stock2.reflection")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RULES_DIR = BASE_DIR / "rules"
PENDING_RULES = RULES_DIR / "_pending.md"

# 判定阈值
HOLD_BAND = 10.0  # hold 推荐下，波动在 ±10% 内算"方向对"


# ============================================================
# 数据结构
# ============================================================
@dataclass
class Prediction:
    ts_code: str
    name: str
    analysis_date: datetime
    recommendation: str
    confidence: float
    target_period: int
    target_low: float
    target_high: float
    report_path: Path
    report_text: str = ""


@dataclass
class Verdict:
    prediction: Prediction
    start_date: datetime
    start_price: float
    end_date: datetime
    end_price: float
    actual_return: float
    direction_correct: bool
    range_hit: bool
    verdict: str
    note: str = ""


# ============================================================
# 解析 YAML frontmatter
# ============================================================
def parse_frontmatter(text: str) -> tuple[dict, str]:
    """分离 YAML frontmatter 和正文。"""
    if not text.startswith("---"):
        return {}, text

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text

    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        logger.warning("YAML parse failed | %s", e)
        meta = {}

    body = m.group(2)
    return meta, body


def _parse_range(rng) -> tuple[float, float]:
    """解析 '[-10%, 15%]' 或 [-10, 15] 为 (low, high)。"""
    if isinstance(rng, (list, tuple)) and len(rng) >= 2:
        try:
            return float(rng[0]), float(rng[1])
        except (TypeError, ValueError):
            return -10.0, 10.0

    if isinstance(rng, str):
        nums = re.findall(r"-?\d+\.?\d*", rng)
        if len(nums) >= 2:
            return float(nums[0]), float(nums[1])

    return -10.0, 10.0


# ============================================================
# 提取历史预测
# ============================================================
def extract_predictions(ts_code: str, limit: int = 30) -> list[Prediction]:
    """读取某只股票的所有历史报告，提取预测。"""
    files = rs.list_reports(ts_code, limit=limit)
    out: list[Prediction] = []

    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            meta, _ = parse_frontmatter(text)

            if not meta:
                logger.warning("no frontmatter | %s", f.name)
                continue

            rec = str(meta.get("recommendation", "")).lower().strip()
            if rec not in ("buy", "hold", "sell"):
                logger.warning("invalid recommendation | %s | %s", f.name, rec)
                continue

            analysis_date_str = str(meta.get("analysis_date", ""))
            try:
                analysis_date = datetime.strptime(analysis_date_str, "%Y-%m-%d")
            except ValueError:
                m = re.search(r"_(\d{8})\.md$", f.name)
                if not m:
                    continue
                analysis_date = datetime.strptime(m.group(1), "%Y%m%d")

            low, high = _parse_range(meta.get("target_return_range"))

            out.append(Prediction(
                ts_code=str(meta.get("ts_code", ts_code)),
                name=str(meta.get("name", "")),
                analysis_date=analysis_date,
                recommendation=rec,
                confidence=float(meta.get("confidence", 0.5)),
                target_period=int(meta.get("target_period", 20)),
                target_low=low,
                target_high=high,
                report_path=f,
                report_text=text,
            ))
        except Exception as e:
            logger.exception("parse report failed | %s | %s", f.name, e)

    return out


# ============================================================
# 交易日计算
# ============================================================
def _add_trading_days(start: datetime, n: int) -> datetime:
    """从 start 往后 N 个交易日。"""
    bd = pd.bdate_range(start=start, periods=n + 1)
    return bd[-1].to_pydatetime()


# ============================================================
# 验证单条
# ============================================================
def verify_one(pred: Prediction) -> Optional[Verdict]:
    """验证单条预测。返回 None 表示未到期或数据不足。"""
    today = datetime.now()

    # 1. 是否到期
    due_date = _add_trading_days(pred.analysis_date, pred.target_period)
    if due_date > today:
        logger.info("not due | %s | due=%s",
                    pred.ts_code, due_date.strftime("%Y-%m-%d"))
        return None

    # 2. 查行情
    try:
        days_needed = (today - pred.analysis_date).days + 30
        quotes = fetch_quotes(pred.ts_code, days=days_needed)
        if quotes.empty:
            logger.warning("no quotes | %s", pred.ts_code)
            return None

        quotes["date"] = pd.to_datetime(quotes["date"])
        quotes = quotes.sort_values("date").reset_index(drop=True)

        # 3. 找起点（分析日当天或之后第一个交易日）
        start_mask = quotes["date"] >= pred.analysis_date
        start_rows = quotes[start_mask]
        if start_rows.empty:
            logger.warning("no start date | %s | %s",
                           pred.ts_code, pred.analysis_date.date())
            return None
        start_row = start_rows.iloc[0]

        # 4. 找终点（due_date 当天或之前最后一个交易日）
        end_mask = quotes["date"] <= due_date
        end_rows = quotes[end_mask]
        if end_rows.empty:
            logger.warning("no end date | %s | %s",
                           pred.ts_code, due_date.date())
            return None
        end_row = end_rows.iloc[-1]

        start_price = float(start_row["close"])
        end_price = float(end_row["close"])
        if start_price <= 0:
            return None

        actual_return = (end_price / start_price - 1) * 100

    except Exception as e:
        logger.exception("verify failed | %s | %s", pred.ts_code, e)
        return None

    # 5. 判定
    rec = pred.recommendation

    if rec == "buy":
        direction_correct = actual_return > 0
    elif rec == "sell":
        direction_correct = actual_return < 0
    else:
        direction_correct = abs(actual_return) <= HOLD_BAND

    range_hit = pred.target_low <= actual_return <= pred.target_high

    if direction_correct and range_hit:
        verdict = "full"
    elif direction_correct or range_hit:
        verdict = "partial"
    else:
        verdict = "wrong"

    logger.info(
        "verify | %s | date=%s | rec=%s | actual=%+.2f%% | "
        "dir=%s | range=%s | verdict=%s",
        pred.ts_code, pred.analysis_date.date(), rec,
        actual_return, direction_correct, range_hit, verdict,
    )

    return Verdict(
        prediction=pred,
        start_date=start_row["date"].to_pydatetime(),
        start_price=start_price,
        end_date=end_row["date"].to_pydatetime(),
        end_price=end_price,
        actual_return=actual_return,
        direction_correct=direction_correct,
        range_hit=range_hit,
        verdict=verdict,
    )


def verify_all(ts_code: str) -> list[Verdict]:
    """验证某只股票的所有到期预测。"""
    preds = extract_predictions(ts_code)
    logger.info("verify_all | %s | total=%d", ts_code, len(preds))

    verdicts = []
    for p in preds:
        v = verify_one(p)
        if v:
            verdicts.append(v)

    return verdicts


# ============================================================
# 生成反思（LLM）
# ============================================================
def _verdict_label(v: str) -> str:
    return {"full": "完全正确", "partial": "部分正确", "wrong": "错误"}.get(v, v)


async def reflect_one(verdict: Verdict) -> str:
    """对一条判定生成反思文本。"""
    p = verdict.prediction
    template = _load_prompt("reflect")

    prompt = _render(
        template,
        report_text=p.report_text[:6000],
        analysis_date=p.analysis_date.strftime("%Y-%m-%d"),
        start_date=verdict.start_date.strftime("%Y-%m-%d"),
        start_price=f"{verdict.start_price:.2f}",
        end_date=verdict.end_date.strftime("%Y-%m-%d"),
        end_price=f"{verdict.end_price:.2f}",
        actual_return=f"{verdict.actual_return:+.2f}",
        target_low=p.target_low,
        target_high=p.target_high,
        recommendation=p.recommendation,
        verdict_label=_verdict_label(verdict.verdict),
    )

    try:
        text = await _call_llm(prompt, temperature=0.3, max_tokens=3000)
        return text.strip()
    except Exception as e:
        logger.exception("reflect failed | %s", p.ts_code)
        return f"（反思生成失败：{type(e).__name__}: {e}）"


# ============================================================
# 保存反思
# ============================================================
def _reflection_path(ts_code: str, name: str) -> Path:
    code = rs._sanitize(ts_code)
    nm = rs._sanitize(name) or "unknown"
    rs.LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    return rs.LESSONS_DIR / f"{code}_{nm}_reflections.md"


def append_reflection(ts_code: str, name: str,
                      verdict: Verdict, reflection_text: str) -> Path:
    """追加一条反思到汇总文件。同一分析日只写一次。"""
    path = _reflection_path(ts_code, name)

    # ---- 去重：检查该分析日是否已复盘 ----
    analysis_date_str = verdict.prediction.analysis_date.strftime("%Y-%m-%d")
    marker = f"## {analysis_date_str} 分析"

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if marker in existing:
            logger.info("reflection skipped (already exists) | %s | %s",
                        path.name, analysis_date_str)
            return path
    # ----------------------------------------

    header = (
        f"\n\n---\n\n"
        f"## {analysis_date_str} 分析 "
        f"→ {verdict.end_date.strftime('%Y-%m-%d')} 验证\n\n"
    )
    meta = (
        f"**预测**：`{verdict.prediction.recommendation}` · "
        f"{verdict.prediction.target_period} 交易日 · "
        f"区间 [{verdict.prediction.target_low}%, "
        f"{verdict.prediction.target_high}%]  \n"
        f"**实际**：{verdict.actual_return:+.2f}% "
        f"（{verdict.start_price:.2f} → {verdict.end_price:.2f}）  \n"
        f"**判定**：{_verdict_label(verdict.verdict)}  \n\n"
    )

    if not path.exists():
        title = f"# {ts_code} {name} 复盘汇总\n"
        path.write_text(title + header + meta + reflection_text, encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as f:
            f.write(header + meta + reflection_text)

    logger.info("reflection appended | %s", path.name)
    return path


# ============================================================
# 候选规则提取
# ============================================================
def extract_candidate_rules(reflection_text: str) -> list[dict]:
    """从反思文本里提取所有 ```yaml 代码块的候选规则。

    兼容：
        - 多个 yaml 代码块
        - 每块顶层是 dict 或 list
        - 字段名多变（rule_id/name/description/scenario/...）
    """
    blocks = re.findall(
        r"```yaml\s*\n(.*?)\n```", reflection_text, re.DOTALL,
    )
    if not blocks:
        logger.info("no yaml block found in reflection")
        return []

    VALID_SCENARIOS = {
        "global", "high_growth", "value", "cyclical", "turnaround",
    }

    def _pick(d: dict, *keys: str, default: str = "") -> str:
        for k in keys:
            v = d.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
        return default

    def _normalize(r: dict) -> Optional[dict]:
        """把 LLM 输出的任意 dict 规范成统一结构。"""
        if not isinstance(r, dict):
            return None

        # scenario：显式字段 → 否则回落 global
        scenario = _pick(r, "scenario", "scenario_name")
        if scenario not in VALID_SCENARIOS:
            scenario = "global"

        # rule 文本：按重要性依次尝试
        rule_text = _pick(
            r, "rule", "description", "action", "name", "rule_desc",
        )
        if not rule_text:
            return None

        # reason：理由
        reason = _pick(
            r, "reason", "rationale", "trigger", "applicable_to",
        )

        # 保留额外字段
        extra = {}
        for k in ("rule_id", "name", "trigger", "action",
                  "rationale", "applicable_to", "priority"):
            v = r.get(k)
            if v is not None and str(v).strip():
                extra[k] = str(v).strip()

        return {
            "scenario": scenario,
            "rule": rule_text,
            "reason": reason,
            "_extra": extra,
        }

    out: list[dict] = []
    for bi, block in enumerate(blocks):
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as e:
            logger.warning("yaml parse failed | block=%d | %s", bi, e)
            continue

        if data is None:
            continue

        # 顶层 dict 且有 "rules" 键 → 展开
        if isinstance(data, dict) and "rules" in data:
            items = data["rules"]
            if isinstance(items, list):
                for it in items:
                    norm = _normalize(it)
                    if norm:
                        out.append(norm)
                continue

        # 顶层 list → 遍历
        if isinstance(data, list):
            for it in data:
                norm = _normalize(it)
                if norm:
                    out.append(norm)
            continue

        # 顶层单个 dict → 当作一条
        if isinstance(data, dict):
            norm = _normalize(data)
            if norm:
                out.append(norm)
            continue

    logger.info("candidate rules extracted | blocks=%d | rules=%d",
                len(blocks), len(out))
    return out


def append_candidate_rules(rules: list[dict],
                           source: str,
                           verdict: Verdict) -> None:
    """把候选规则追加到 rules/_pending.md。"""
    if not rules:
        return

    RULES_DIR.mkdir(parents=True, exist_ok=True)

    if not PENDING_RULES.exists():
        PENDING_RULES.write_text(
            "# 待审核规则\n\n"
            "> 本文件由系统自动追加，由人工审核后手动迁移到 "
            "`rules/global.md` 或 `rules/scenarios/*.md`。\n\n",
            encoding="utf-8",
        )
    # ---- 去重：检查是否已有同一报告的提议 ----
    existing = PENDING_RULES.read_text(encoding="utf-8")
    marker = f"- **来源**：{source}"
    if marker in existing:
        logger.info("candidate rules skipped (already pending) | source=%s",
                    source)
        return
    
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    analysis_date = verdict.prediction.analysis_date.strftime("%Y-%m-%d")

    lines = [f"\n## 提议时间：{date_str}\n"]
    lines.append(f"- **来源**：{source}（{verdict.prediction.ts_code} "
                 f"{analysis_date} 分析）")
    lines.append(f"- **判定**：{_verdict_label(verdict.verdict)}，"
                 f"实际 {verdict.actual_return:+.2f}%")
    lines.append("")

    for i, r in enumerate(rules, 1):
        lines.append(f"### 规则 {i}")
        lines.append(f"- **场景**：`{r['scenario']}`")
        lines.append(f"- **规则**：{r['rule']}")
        lines.append(f"- **理由**：{r['reason']}")

        # 附加上 LLM 给的额外字段
        extra = r.get("_extra", {})
        if extra.get("rule_id"):
            lines.append(f"- **rule_id**：`{extra['rule_id']}`")
        if extra.get("name"):
            lines.append(f"- **名称**：{extra['name']}")
        if extra.get("trigger"):
            lines.append(f"- **触发条件**：{extra['trigger']}")
        if extra.get("action"):
            lines.append(f"- **动作**：{extra['action']}")

        lines.append(f"- **状态**：⬜ 待审核")
        lines.append("")

    with PENDING_RULES.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("candidate rules appended | count=%d", len(rules))


# ============================================================
# 完整流程
# ============================================================
async def reflect_and_save(ts_code: str) -> dict:
    """对某只股票的所有到期预测做复盘。

    返回：
        {
            "ts_code": str,
            "name": str,
            "verdicts": [Verdict, ...],
            "reflection_path": Path | None,
            "candidates_count": int,
        }
    """
    preds = extract_predictions(ts_code)
    if not preds:
        return {
            "ts_code": ts_code,
            "name": "",
            "verdicts": [],
            "reflection_path": None,
            "candidates_count": 0,
        }

    name = preds[0].name if preds else ""

    # 逐个验证
    verdicts = []
    for p in preds:
        v = verify_one(p)
        if v:
            verdicts.append(v)

    if not verdicts:
        logger.info("no verdicts | %s", ts_code)
        return {
            "ts_code": ts_code,
            "name": name,
            "verdicts": [],
            "reflection_path": None,
            "candidates_count": 0,
        }

    # 对每条生成反思
    total_candidates = 0
    reflection_path = None

    for v in verdicts:
        reflection = await reflect_one(v)
        path = append_reflection(ts_code, name, v, reflection)
        reflection_path = path

        candidates = extract_candidate_rules(reflection)
        if candidates:
            source = (f"{ts_code} {name} "
                      f"{v.prediction.analysis_date.strftime('%Y-%m-%d')}")
            append_candidate_rules(candidates, source, v)
            total_candidates += len(candidates)

    return {
        "ts_code": ts_code,
        "name": name,
        "verdicts": verdicts,
        "reflection_path": reflection_path,
        "candidates_count": total_candidates,
    }