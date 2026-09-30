
import re
from pathlib import Path
"""把 _pending.md 里的某条规则搬到 global.md（半自动）。"""

RULES_DIR = Path("rules")
PENDING = RULES_DIR / "_pending.md"
GLOBAL = RULES_DIR / "global.md"


def list_pending() -> list[dict]:
    """列出 _pending.md 里的所有候选规则。"""
    text = PENDING.read_text(encoding="utf-8")
    blocks = re.findall(
        r"### 规则 \d+\n(.*?)(?=\n### 规则 |\n## |\Z)",
        text, re.DOTALL,
    )
    rules = []
    for b in blocks:
        info = {}
        for key, pat in [
            ("场景", r"\*\*场景\*\*：`([^`]+)`"),
            ("规则", r"\*\*规则\*\*：(.*?)\n- \*\*"),
            ("理由", r"\*\*理由\*\*：(.*?)\n- \*\*"),
            ("rule_id", r"\*\*rule_id\*\*：`([^`]+)`"),
            ("名称", r"\*\*名称\*\*：(.*?)\n- \*\*"),
            ("触发条件", r"\*\*触发条件\*\*：(.*?)\n- \*\*"),
            ("动作", r"\*\*动作\*\*：(.*?)\n- \*\*"),
        ]:
            m = re.search(pat, b, re.DOTALL)
            info[key] = m.group(1).strip() if m else ""
        rules.append(info)
    return rules


if __name__ == "__main__":
    for i, r in enumerate(list_pending(), 1):
        print(f"\n=== {i}. {r.get('名称', 'N/A')} ===")
        print(f"场景：{r.get('场景')}")
        print(f"规则：{r.get('规则')}")
        print(f"理由：{r.get('理由')}")