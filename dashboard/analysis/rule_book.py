import logging
import re
from pathlib import Path

"""规则库读写 — 从 rules/ 目录加载规则并注入到 LLM prompt。

目录结构：
    rules/
    ├── global.md                  全局规则
    ├── _pending.md                待审核（不注入）
    └── scenarios/
        ├── high_growth.md
        ├── value.md
        ├── cyclical.md
        └── turnaround.md

核心接口：
    load_rules_text(scenario=None)  → 拼好的规则文本（给 LLM）
    list_rules()                    → 结构化列表（给 UI）
    invalidate_cache()              → 清缓存
"""

logger = logging.getLogger("stock2.rule_book")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RULES_DIR = BASE_DIR / "rules"
SCENARIOS_DIR = RULES_DIR / "scenarios"

VALID_SCENARIOS = ("high_growth", "value", "cyclical", "turnaround")

# 简单缓存
_cache: dict[str, str] = {}


# ============================================================
# 读取
# ============================================================
def _read_md(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning("read rule file failed | %s | %s", path.name, e)
        return ""


def load_global_rules() -> str:
    """加载全局规则。"""
    return _read_md(RULES_DIR / "global.md")


def load_scenario_rules(scenario: str) -> str:
    """加载某个场景的规则。"""
    scenario = (scenario or "").strip().lower()
    if scenario not in VALID_SCENARIOS:
        return ""
    return _read_md(SCENARIOS_DIR / f"{scenario}.md")


# ============================================================
# 拼接给 LLM
# ============================================================
def load_rules_text(scenario: str = "") -> str:
    """拼好给 LLM 的规则文本。

    包括：
        - 全局规则（global.md）
        - 场景规则（scenarios/{scenario}.md，若 scenario 合法）
        - 不包含 _pending.md（那是待审核，不参与决策）
    """
    key = f"text::{scenario or '_'}"
    if key in _cache:
        return _cache[key]

    parts = []

    global_text = load_global_rules()
    if global_text:
        parts.append("## 全局规则\n\n" + global_text)

    if scenario and scenario in VALID_SCENARIOS:
        scenario_text = load_scenario_rules(scenario)
        if scenario_text:
            parts.append(
                f"## 场景规则：{scenario}\n\n" + scenario_text
            )
    else:
        # scenario 未知 → 把全部场景规则都塞进来，让 judge 自己选
        all_scen = []
        for s in VALID_SCENARIOS:
            t = load_scenario_rules(s)
            if t:
                all_scen.append(f"### {s}\n\n{t}")
        if all_scen:
            parts.append(
                "## 场景规则（judge 自行判断属于哪个场景后应用）\n\n"
                + "\n\n".join(all_scen)
            )

    text = "\n\n---\n\n".join(parts) if parts else "（规则库为空）"
    _cache[key] = text
    logger.info("rules loaded | scenario=%s | len=%d",
                scenario or "all", len(text))
    return text


# ============================================================
# 列出规则（给 UI）
# ============================================================
RULE_HEAD_RE = re.compile(
    r"^###\s+(R-[A-Z0-9]+)\s+(.+?)$",
    re.MULTILINE,
)


def _parse_rules_in_file(path: Path) -> list[dict]:
    """从 md 文件里提取规则。"""
    text = _read_md(path)
    if not text:
        return []

    out = []
    # 找 "### R-GXX 标题" 位置
    matches = list(RULE_HEAD_RE.finditer(text))
    for i, m in enumerate(matches):
        rule_id = m.group(1).strip()
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()

        # 提取字段
        def _field(name: str) -> str:
            pat = rf"-\s*\*\*{name}\*\*[：:]\s*(.*?)(?=\n-\s*\*\*|\Z)"
            mm = re.search(pat, body, re.DOTALL)
            return mm.group(1).strip() if mm else ""

        out.append({
            "rule_id": rule_id,
            "title": title,
            "rule": _field("规则"),
            "reason": _field("理由"),
            "action": _field("动作"),
            "source": _field("来源"),
            "file": path.name,
        })
    return out


def list_rules() -> list[dict]:
    """列出所有规则（global + scenarios）。"""
    out = []

    g = RULES_DIR / "global.md"
    if g.exists():
        for r in _parse_rules_in_file(g):
            r["scope"] = "global"
            out.append(r)

    if SCENARIOS_DIR.exists():
        for f in sorted(SCENARIOS_DIR.glob("*.md")):
            for r in _parse_rules_in_file(f):
                r["scope"] = f.stem
                out.append(r)

    return out


def list_pending() -> list[dict]:
    """列出待审核规则（_pending.md，粗略解析）。"""
    p = RULES_DIR / "_pending.md"
    if not p.exists():
        return []
    text = _read_md(p)
    if not text:
        return []

    out = []
    blocks = re.split(r"\n## 提议时间：", text)
    for b in blocks[1:]:
        lines = b.strip().splitlines()
        time_str = lines[0].strip() if lines else ""
        out.append({
            "time": time_str,
            "text": "## 提议时间：" + b,
        })
    return out


def invalidate_cache() -> None:
    _cache.clear()
    logger.info("rule cache invalidated")


# ============================================================
# 测试
# ============================================================
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    scenario = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"\n{'='*60}\n规则加载测试 | scenario={scenario or '(all)'}\n{'='*60}\n")
    print(load_rules_text(scenario))

    print(f"\n{'='*60}\n规则列表\n{'='*60}\n")
    for r in list_rules():
        print(f"[{r['rule_id']}] {r['title']}  ({r['scope']})")