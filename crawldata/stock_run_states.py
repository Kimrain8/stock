import asyncio
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".run_state.json")

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")

# ===========================================================================
#  二、状态管理（按天维度）
# ===========================================================================
class StateCorruptedError(RuntimeError):
    """状态文件存在但无法读取/解析/结构异常 —— 致命错误，立即终止。"""
class StockState:
    """
    状态文件结构示例：
    {
      "day": "2026-09-20",
      "runs": 2,
      "steps": {
        "Myts.get_trade_calendar": {
          "status": "done",
          "date": "2026-09-20",
          "at": "2026-09-20T09:12:03"
        },
        ...
      },
      "last_error": {...} | null
    }
    """

    def __init__(self, path: str = STATE_FILE):
        self.path = path
        self.data = self._load()
        self._rollover_if_new_day()

    # -------------------- 加载 --------------------
    def _blank(self) -> dict:
        return {"day": _today(), "runs": 0, "steps": {}, "last_error": None}

    def _load(self) -> dict:
        # ① 文件不存在 → 首次运行，初始化
        if not os.path.exists(self.path):
            print(f"[init] 未找到状态文件 {self.path}，进行初始化")
            return self._blank()

        # ② 文件存在 → 必须能读，否则直接报错
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            raise StateCorruptedError(
                f"状态文件无法读取: {self.path} ({e})"
            ) from e

        if not raw.strip():
            raise StateCorruptedError(
                f"状态文件为空: {self.path}。如确需重来，请手动删除该文件。"
            )

        # ③ 必须能解析 JSON
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise StateCorruptedError(
                f"状态文件 JSON 解析失败: {self.path} ({e})。"
                f"如确需重来，请手动删除该文件。"
            ) from e

        # ④ 结构校验
        if (
            not isinstance(data, dict)
            or "day" not in data
            or "runs" not in data
            or "steps" not in data
        ):
            raise StateCorruptedError(
                f"状态文件结构异常（缺少 day/runs/steps 字段）: {self.path}"
            )
        data.setdefault("last_error", None)
        return data

    # -------------------- 换天 --------------------
    def _rollover_if_new_day(self):
        """日期变了 → 昨天状态作废，重置当天视图（正常翻页，不是损坏）。"""
        today = _today()
        if self.data.get("day") != today:
            print(
                f"[rollover] 日期 {self.data.get('day')} -> {today}，重置当天步骤状态"
            )
            self.data = self._blank()
            self.save()

    # -------------------- 保存（原子写入） --------------------
    def save(self):
        d = os.path.dirname(self.path) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".run_state.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # -------------------- 查询 --------------------
    def is_done(self, key: str) -> bool:
        """仅当：状态 done 且完成日期 == 今天，才视为已跳过。"""
        rec = self.data["steps"].get(key)
        return bool(
            rec
            and rec.get("status") == "done"
            and rec.get("date") == _today()
        )

    @property
    def again(self) -> int:
        """当天的第几次运行。"""
        return self.data["runs"]

    @property
    def day(self) -> str:
        return self.data["day"]

    # -------------------- 写入 --------------------
    def new_run(self) -> int:
        self.data["runs"] += 1
        self.save()
        return self.data["runs"]

    def mark_done(self, key: str):
        self.data["steps"][key] = {
            "status": "done",
            "date": _today(),
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        self.save()

    def mark_failed(self, key: str, exc: BaseException):
        rec = {
            "status": "failed",
            "date": _today(),
            "error": f"{type(exc).__name__}: {exc}",
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        self.data["steps"][key] = rec
        self.data["last_error"] = rec
        self.save()

    def reset(self):
        """强制清空为「今天的全新状态」。"""
        self.data = self._blank()
        self.save()


# ===========================================================================
#  三、步骤执行器（同步 / 异步）
# ===========================================================================

def run_step(state: StockState, key: str, fn, *args, **kwargs):
    """同步步骤：今天已完成则跳过；失败记录并抛出。"""
    if state.is_done(key):
        print(f"[skip] {key}")
        return
    print(f"[run ] {key}")
    try:
        fn(*args, **kwargs)
    except Exception as e:
        state.mark_failed(key, e)
        raise
    state.mark_done(key)
    print(f"[ok  ] {key}")


async def run_step_async(state: StockState, key: str, fn, *args, **kwargs):
    """异步步骤：今天已完成则跳过；失败记录并抛出。"""
    if state.is_done(key):
        print(f"[skip] {key}")
        return
    print(f"[run ] {key}")
    try:
        await fn(*args, **kwargs)
    except Exception as e:
        state.mark_failed(key, e)
        raise
    state.mark_done(key)
    print(f"[ok  ] {key}")
