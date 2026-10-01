"""自然语言选股 — 知识库驱动的条件解析与执行。

阶段 1：知识库 + 模板解析 + 单 df 条件标记
阶段 2（预留）：LLM 兜底 + 跨 df 关联 + 动态列推导
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("stock2.nl_filter")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
KNOWLEDGE_DIR = BASE_DIR / "knowledge"


# ============================================================
# 知识库数据结构
# ============================================================
@dataclass
class FieldAlias:
    name: str
    aliases: list
    candidates: list


@dataclass
class ConditionTemplate:
    name: str
    pattern: str
    field_semantic: str
    expr_template: str
    desc_template: str
    compiled: re.Pattern = None


@dataclass
class ParsedCondition:
    raw: str                # 用户原文
    template_name: str      # 匹配的模板名
    column: str             # 实际列名
    expr: str               # pandas 表达式
    description: str        # 用于 reason 列
    error: str = ""


# ============================================================
# 知识库解析
# ============================================================
def _parse_md_sections(text: str) -> list[dict]:
    """通用 Markdown 段落解析：拆出 ## 标题 + 字段键值对。"""
    sections = []
    cur = None
    for line in text.splitlines():
        if line.startswith("## "):
            if cur:
                sections.append(cur)
            cur = {"title": line[3:].strip(), "fields": {}}
        elif cur is not None and line.strip().startswith("- "):
            m = re.match(r"-\s*([^:：]+)[:：]\s*(.*)", line)
            if m:
                cur["fields"][m.group(1).strip()] = m.group(2).strip()
    if cur:
        sections.append(cur)
    return sections


def load_field_aliases() -> dict:
    """加载字段别名表。"""
    path = KNOWLEDGE_DIR / "field_aliases.md"
    if not path.exists():
        logger.warning("field_aliases.md 不存在")
        return {}

    sections = _parse_md_sections(path.read_text(encoding="utf-8"))
    out = {}
    for sec in sections:
        f = sec["fields"]
        aliases = [a.strip() for a in re.split(r"[,，、]", f.get("别名", "")) if a.strip()]
        candidates = [c.strip() for c in re.split(r"[,，、]", f.get("候选列名", "")) if c.strip()]
        out[sec["title"]] = FieldAlias(
            name=sec["title"], aliases=aliases, candidates=candidates,
        )
    logger.info("字段别名加载 | %d 条", len(out))
    return out


def load_templates() -> list:
    """加载条件模板。"""
    path = KNOWLEDGE_DIR / "condition_templates.md"
    if not path.exists():
        logger.warning("condition_templates.md 不存在")
        return []

    sections = _parse_md_sections(path.read_text(encoding="utf-8"))
    out = []
    for sec in sections:
        f = sec["fields"]
        pattern = f.get("模式", "")
        expr = f.get("表达式", "")
        if not pattern or not expr:
            continue
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            logger.warning("非法正则 | %s | %s", sec["title"], e)
            continue
        out.append(ConditionTemplate(
            name=sec["title"],
            pattern=pattern,
            field_semantic=f.get("字段", ""),
            expr_template=expr,
            desc_template=f.get("描述", ""),
            compiled=compiled,
        ))
    # ★ 关键：按模式长度倒序，越具体的模板越优先匹配
    out.sort(key=lambda t: len(t.pattern), reverse=True)

    logger.info("条件模板加载 | %d 条", len(out))
    return out


# ============================================================
# 字段解析
# ============================================================
def _find_column(df: pd.DataFrame, semantic: str, aliases: dict) -> str | None:
    """按语义名在 df 中查找实际列名。

    顺序：
        1. 候选列名（英文/原名）
        2. 别名（可能的中文列名）
    """
    if not semantic:
        return None
    a = aliases.get(semantic)
    if a is None:
        return None
    for c in a.candidates:
        if c in df.columns:
            return c
    for c in a.aliases:
        if c in df.columns:
            return c
    return None


# ============================================================
# 自然语言解析
# ============================================================
def _split_clauses(text: str) -> list:
    """把用户输入拆成多个子句。"""
    if not text:
        return []
    pattern = r"(?:并且|且|而且|以及|同时|and|[,，;；])"
    parts = re.split(pattern, text)
    return [p.strip() for p in parts if p.strip()]


def parse_description(text: str, df: pd.DataFrame,aliases: dict, templates: list) -> list:
    """解析自然语言描述为条件列表。

    对每个子句，从所有能匹配的模板中选"匹配字符串最长"的那个。
    """
    clauses = _split_clauses(text)
    if not clauses:
        return []

    out = []
    for clause in clauses:
        # 收集所有匹配候选
        candidates = []
        for tmpl in templates:
            m = tmpl.compiled.search(clause)
            if m:
                candidates.append((tmpl, m, len(m.group(0))))

        # 按匹配长度倒序
        candidates.sort(key=lambda x: x[2], reverse=True)

        if not candidates:
            out.append(ParsedCondition(
                raw=clause,
                template_name="",
                column="",
                expr="",
                description="",
                error="未匹配到模板（阶段 1 仅支持已注册模板）",
            ))
            continue

        # 用最优模板
        tmpl, m, _ = candidates[0]

        col = _find_column(df, tmpl.field_semantic, aliases)
        if col is None:
            out.append(ParsedCondition(
                raw=clause,
                template_name=tmpl.name,
                column="",
                expr="",
                description="",
                error=f"df 中找不到「{tmpl.field_semantic}」对应列",
            ))
            continue

        groups = m.groups()
        try:
            if len(groups) == 1:
                expr = tmpl.expr_template.format(col=col, value=groups[0])
                desc = tmpl.desc_template.format(value=groups[0])
            elif len(groups) == 2:
                if "近" in tmpl.name or "创" in tmpl.name or "日均线" in tmpl.name:
                    expr = tmpl.expr_template.format(
                        col=col, n=groups[0], value=groups[1],
                    )
                    desc = tmpl.desc_template.format(
                        n=groups[0], value=groups[1],
                    )
                else:
                    expr = tmpl.expr_template.format(
                        col=col, value1=groups[0], value2=groups[1],
                    )
                    desc = tmpl.desc_template.format(
                        value1=groups[0], value2=groups[1],
                    )
            else:
                expr = tmpl.expr_template.format(col=col)
                desc = tmpl.desc_template
        except KeyError as e:
            out.append(ParsedCondition(
                raw=clause,
                template_name=tmpl.name,
                column=col,
                expr="",
                description="",
                error=f"模板渲染缺参数：{e}",
            ))
            continue

        out.append(ParsedCondition(
            raw=clause,
            template_name=tmpl.name,
            column=col,
            expr=expr,
            description=desc,
        ))

    return out


# ============================================================
# 应用条件
# ============================================================
def apply_conditions(df: pd.DataFrame, conditions: list,
                     prefix: str = "cond") -> tuple:
    """把条件应用到 df，生成标记列。

    返回：
        (df_new, summary)
        df_new 新增列：cond1_pass, cond1_reason, ..., _all_pass
    """
    result = df.copy().reset_index(drop=True)
    n = len(result)

    pass_cols = []
    cond_summaries = []

    for i, cond in enumerate(conditions, 1):
        pass_col = f"{prefix}{i}_pass"
        reason_col = f"{prefix}{i}_reason"

        if cond.error:
            result[pass_col] = False
            result[reason_col] = f"解析失败：{cond.error}"
            cond_summaries.append({
                "name": cond.raw,
                "passed": 0,
                "total": n,
                "error": cond.error,
            })
            pass_cols.append(pass_col)
            continue

        try:
            mask = eval(cond.expr,
                        {"__builtins__": {}},
                        {"df": result, "pd": pd, "np": np})
            if not isinstance(mask, pd.Series):
                mask = pd.Series([bool(mask)] * n)
            mask = mask.fillna(False).astype(bool).reset_index(drop=True)

            result[pass_col] = mask
            result[reason_col] = np.where(
                mask, "", f"不满足：{cond.description}",
            )
            cond_summaries.append({
                "name": cond.raw,
                "passed": int(mask.sum()),
                "total": n,
            })
        except Exception as e:
            result[pass_col] = False
            result[reason_col] = f"执行错误：{type(e).__name__}: {e}"
            cond_summaries.append({
                "name": cond.raw,
                "passed": 0,
                "total": n,
                "error": f"{type(e).__name__}: {e}",
            })

        pass_cols.append(pass_col)

    if pass_cols:
        result["_all_pass"] = result[pass_cols].all(axis=1)
        passed_total = int(result["_all_pass"].sum())
    else:
        result["_all_pass"] = True
        passed_total = n

    summary = {
        "total": n,
        "passed": passed_total,
        "conditions": cond_summaries,
    }
    return result, summary


def filter_passed(df: pd.DataFrame) -> pd.DataFrame:
    """只保留 _all_pass=True 的行。"""
    if "_all_pass" not in df.columns:
        return df
    return df[df["_all_pass"]].drop(columns=["_all_pass"])


# ============================================================
# 引擎（一次加载，多次复用）
# ============================================================
class NLFilterEngine:
    def __init__(self):
        self.aliases = load_field_aliases()
        self.templates = load_templates()

    def reload(self):
        self.aliases = load_field_aliases()
        self.templates = load_templates()

    def parse(self, text: str, df: pd.DataFrame) -> list:
        return parse_description(text, df, self.aliases, self.templates)

    def apply(self, df: pd.DataFrame, conditions: list) -> tuple:
        return apply_conditions(df, conditions)