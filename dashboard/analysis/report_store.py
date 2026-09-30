import logging
import re
from datetime import datetime
from pathlib import Path
"""报告存储 — 保存/读取分析报告。

目录结构：
    reports/
    ├── 688508.SH_芯朋微_20260929.md
    └── lessons_learned/
        └── 688508.SH_芯朋微_reflections.md
"""

logger = logging.getLogger("stock2.report_store")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = BASE_DIR / "reports"
LESSONS_DIR = REPORTS_DIR / "lessons_learned"


def _ensure_dirs():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LESSONS_DIR.mkdir(parents=True, exist_ok=True)


def _sanitize(name: str) -> str:
    """去掉文件名非法字符。"""
    return re.sub(r'[\\/:*?"<>|]', "_", str(name)).strip()


def report_filename(ts_code: str, name: str, dt: datetime | None = None) -> str:
    """生成报告文件名：股票代码_名称_日期.md"""
    dt = dt or datetime.now()
    code = _sanitize(ts_code)
    nm = _sanitize(name) or "unknown"
    return f"{code}_{nm}_{dt.strftime('%Y%m%d')}.md"


def save_report(ts_code: str, name: str, content: str,
                dt: datetime | None = None) -> Path:
    """保存报告。返回保存的路径。"""
    _ensure_dirs()
    filename = report_filename(ts_code, name, dt)
    path = REPORTS_DIR / filename
    path.write_text(content, encoding="utf-8")
    logger.info("report saved | %s | %d bytes", path.name, len(content))
    return path


def load_report(path: Path) -> str:
    """读取报告。"""
    return path.read_text(encoding="utf-8")


def list_reports(ts_code: str, limit: int = 20) -> list[Path]:
    """列出某只股票的报告，最新的在前。"""
    _ensure_dirs()
    code = _sanitize(ts_code)
    files = sorted(
        REPORTS_DIR.glob(f"{code}_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[:limit]


def load_recent_reports_text(ts_code: str, limit: int = 5) -> str:
    """读取最近 N 份报告，拼成一段文本供 LLM 使用。"""
    files = list_reports(ts_code, limit)
    if not files:
        return ""

    parts = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            parts.append(f"### 报告：{f.name}\n\n{text}")
        except Exception as e:
            logger.warning("read report failed | %s | %s", f.name, e)

    return "\n\n---\n\n".join(parts) if parts else ""


def latest_report_exists(ts_code: str, dt: datetime | None = None) -> bool:
    """检查今天是否已有报告。"""
    dt = dt or datetime.now()
    code = _sanitize(ts_code)
    pattern = f"{code}_*_{dt.strftime('%Y%m%d')}.md"
    return bool(list(REPORTS_DIR.glob(pattern)))


def load_latest_report_content(ts_code: str) -> str:
    """读取最新一份报告的完整内容。"""
    files = list_reports(ts_code, 1)
    if not files:
        return ""
    try:
        return files[0].read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("read latest report failed | %s", e)
        return ""