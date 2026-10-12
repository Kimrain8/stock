# -*- coding: utf-8 -*-
"""
scheduler.py —— 生产级轻量调度器（单线程版）
设计核心:
  - 一条依赖链 = 一个 worker 线程；链内串行，链间并行
  - 主线程只负责"时间调度"：睡眠 → 唤醒 → 提交到期任务
  - worker 完成一个任务后立即找下游；能延续就继续，分叉就开新线程
  - 资源最省：没有任务时没有任何线程在跑；分支不重叠时不创建多余线程

功能:
  - 同步 + 异步 (async def) 任务
  - 每任务独立 interval + start_at (支持 Y/M/D/hh/mm/ss 和 M_-1/Y_-1)
  - 依赖分组: 外层 OR, 组内 AND
  - 事件驱动 (interval=0): 依赖成功后立即执行
  - 失败 -> 下游自动跳过
  - retry + retry_backoff (立即/退避重试)
  - 实例池 (call / batch / permanent 生命周期)
  - 配置热重载 (update 命令)
  - 续跑模式 (continue): 从上次失败处恢复
  - 单实例锁 (防重复启动)
  - 循环依赖检测 (启动时)
  - 通知钩子 (on_success/on_fail/on_retry/on_recover/on_skip)
  - 状态持久化 (<cfg>.state.json)
  - 交互命令: status/tasks/run/pause/resume/update/save/cancel/help
  - params 支持动态表达式:
      "params": {"k": "v"}                          静态字典
      "params": "module.func(a=1, b='x')"           模块函数，返回 dict
      "params": "Class.method(a=1)"                 类方法，返回 dict
      "params": "instance.method(a=1)"              实例方法，返回 dict
    condition 内的 params 也支持同样语法

本轮 FIX:
  - continue 模式：不再覆盖 last_status，改为 next_run=now 让主循环重新评估
  - 事件任务 (interval=0) 支持 start_at：
      * start_at=null  -> 纯等 deps
      * start_at 有值  -> 先等时间到，到点后再检查 deps
      * 无 deps        -> 配置错误，日志 ERROR，永不触发
  - 主循环不再无条件跳过事件任务

本次 FIX:
  - _update_task 修复 interval 变更为 None / EVENT 时 next_run 未重算的问题：
      * interval 变化时无条件重算 next_run（新规则立即生效）
      * validate 0→1 恢复时仅补 None 的 next_run，不打乱已跑过任务的节奏
  - print_status / tasks 增加显示 func，便于排查"内存中实际调用的函数"

最新 FIX（本次）:
  - 状态只保存在内存：删除 _finalize / stop() 里自动写 state 文件
  - 新增 save 命令：保存 config（start_at 回写）+ state 文件
  - cancel 命令：保存 config + state，再退出
  - 其它情况（Ctrl+C、关窗、异常退出）都不写状态文件

用法:
  python scheduler.py <配置文件>
  python scheduler.py continue <配置文件>
"""
from __future__ import annotations

import asyncio
import ast
import importlib
import inspect
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("scheduler")

_GLOBAL_SCHEDULER: Optional["Scheduler"] = None
EVENT = "__EVENT__"


def get_scheduler() -> "Scheduler":
    if _GLOBAL_SCHEDULER is None:
        raise RuntimeError("调度器尚未启动")
    return _GLOBAL_SCHEDULER


# =========================================================================== #
# 日志
# =========================================================================== #
def setup_logging(log_file: str = "scheduler.log"):
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("scheduler")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


# =========================================================================== #
# 时间 / 日历
# =========================================================================== #
def _days_in_month(y: int, m: int) -> int:
    if m == 2:
        return 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28
    return (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[m - 1]


def _add_months(dt: datetime, n: int) -> datetime:
    total = (dt.year * 12 + (dt.month - 1)) + n
    y = total // 12
    m = total % 12 + 1
    d = min(dt.day, _days_in_month(y, m))
    return dt.replace(year=y, month=m, day=d)


def add_interval(dt: datetime, spec: str) -> Optional[datetime]:
    if spec is None or spec == "":
        return None
    try:
        unit, n_str = spec.split("_", 1)
        n = int(n_str)
    except Exception:
        raise ValueError(f"非法 interval: {spec!r} (示例: ss_5, / mm_2 / D_1 / M_-1 / Y_-1)")

    if n == -1:
        if unit == "M":
            nxt = _add_months(dt.replace(day=1), 1)
            return nxt.replace(day=_days_in_month(nxt.year, nxt.month))
        if unit == "Y":
            return dt.replace(year=dt.year + 1, month=12, day=31)
        raise ValueError(f"负数间隔只支持 M_-1 / Y_-1: {spec!r}")
    if n < -1:
        raise ValueError(f"不支持小于 -1 的负数间隔: {spec!r}")
    if n == 0:
        raise ValueError(f"间隔数量必须 != 0: {spec!r}")

    if unit == "ss":
        return dt + timedelta(seconds=n)
    if unit == "mm":
        return dt + timedelta(minutes=n)
    if unit == "hh":
        return dt + timedelta(hours=n)
    if unit == "D":
        return dt + timedelta(days=n)
    if unit == "M":
        return _add_months(dt, n)
    if unit == "Y":
        y = dt.year + n
        d = min(dt.day, _days_in_month(y, dt.month))
        return dt.replace(year=y, day=d)
    raise ValueError(f"未知 interval 单位: {unit!r}")


def advance_to_after(base: datetime, interval: str, after: datetime) -> datetime:
    if base > after:
        return base
    try:
        unit, n_str = interval.split("_", 1)
        n = int(n_str)
    except Exception:
        raise ValueError(f"非法 interval: {interval!r}")

    sec = None
    if n > 0:
        if unit == "ss":
            sec = n
        elif unit == "mm":
            sec = n * 60
        elif unit == "hh":
            sec = n * 3600
        elif unit == "D":
            sec = n * 86400
    if sec is not None:
        gap = (after - base).total_seconds()
        steps = int(gap // sec) + 1
        return base + timedelta(seconds=steps * sec)

    cur = base
    for _ in range(100000):
        cur = add_interval(cur, interval)
        if cur is None or cur > after:
            return cur
    return cur


def parse_start_at(s) -> datetime:
    now = datetime.now()
    if s is None or s == "":
        return now
    if isinstance(s, datetime):
        return s
    if isinstance(s, bool):
        raise ValueError(f"start_at 不能是 bool: {s!r}")
    if isinstance(s, (int, float)):
        return now + timedelta(seconds=float(s))

    s = str(s).strip()
    if ":" in s and "-" not in s and "T" not in s:
        parts = [int(p) for p in s.split(":")]
        while len(parts) < 3:
            parts.append(0)
        h, m, sec = parts[:3]
        t = now.replace(hour=h, minute=m, second=sec, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t
    return datetime.fromisoformat(s)


def _normalize_interval(raw) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"interval 不能是 bool: {raw!r}")
    if isinstance(raw, (int, float)):
        n = int(raw)
        if n < 0:
            raise ValueError(f"interval 数字必须 >= 0: {raw!r}")
        return EVENT if n == 0 else f"ss_{n}"
    s = str(raw).strip()
    if s == "":
        return None
    if s == "0":
        return EVENT
    add_interval(datetime.now(), s)
    return s


def _parse_deps(raw) -> List[List[str]]:
    if not raw:
        return []
    groups: List[List[str]] = []
    for item in raw:
        if isinstance(item, (list, tuple)):
            groups.append([str(x) for x in item])
        else:
            groups.append([str(item)])
    return groups


def _parse_backoff(raw) -> Optional[Any]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError("retry_backoff 不能是 bool")
    if isinstance(raw, (int, float)):
        if raw < 0:
            raise ValueError(f"retry_backoff 数字必须 >= 0: {raw!r}")
        return float(raw)
    if isinstance(raw, list):
        return [float(x) for x in raw]
    raise ValueError(f"retry_backoff 非法: {raw!r}")


# =========================================================================== #
# params 表达式解析
# =========================================================================== #
def _parse_params_expr(expr: str) -> Tuple[List[str], Dict[str, Any]]:
    """
    解析形如 "module.func(a=1, b='x')" / "Class.method(a=1)" / "instance.method(a=1)"
    的表达式。返回 (path_list, kwargs)。
    path_list 例：
      "module.func(...)"            -> ["module", "func"]
      "pkg.mod.Class.method(...)"   -> ["pkg", "mod", "Class", "method"]
      "instance.method(...)"        -> ["instance", "method"]
    约束：
      - 只接受关键字参数
      - 参数值必须是字面量（字符串/数字/布尔/None/列表/字典）
    """
    if not isinstance(expr, str):
        raise ValueError(f"params 表达式必须是字符串: {expr!r}")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"params 表达式语法错误: {expr!r} ({e})")
    if not isinstance(tree.body, ast.Call):
        raise ValueError(f"params 表达式必须是函数调用: {expr!r}")

    call = tree.body
    if call.args:
        raise ValueError(f"params 表达式不支持位置参数: {expr!r}")

    path: List[str] = []
    node = call.func
    while isinstance(node, ast.Attribute):
        path.insert(0, node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        raise ValueError(f"params 表达式不支持的调用形式: {expr!r}")
    path.insert(0, node.id)

    if len(path) < 2:
        raise ValueError(
            f"params 表达式至少需要 '模块.函数(...)' / '类.方法(...)' / "
            f"'实例.方法(...)' 形式: {expr!r}")

    kwargs: Dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            raise ValueError(f"params 表达式不支持 **kwargs 展开: {expr!r}")
        try:
            kwargs[kw.arg] = ast.literal_eval(kw.value)
        except Exception:
            raise ValueError(
                f"params 表达式的参数值必须是字面量: "
                f"{kw.arg}={ast.unparse(kw.value)}")

    return path, kwargs


def _parse_params_field(raw, task_name: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    统一解析 params 字段：
      - None / 缺省      -> ({}, None)
      - dict            -> (dict, None)
      - str (函数调用)   -> ({}, expr)   # 运行时求值
    """
    if raw is None:
        return {}, None
    if isinstance(raw, str):
        expr = raw.strip()
        if not expr:
            return {}, None
        _parse_params_expr(expr)
        return {}, expr
    if isinstance(raw, dict):
        return dict(raw), None
    raise ValueError(f"[{task_name}] params 类型非法: {type(raw).__name__}")


def _parse_condition(raw, task_name: str):
    """
    解析 condition 字段，返回 (spec, params_dict, params_expr)。

    支持:
      - None / 缺省                -> (None, {}, None)
      - "模块:函数" / "实例:方法"   -> (spec, {}, None)     （字符串简写）
      - {"func": "...", "params": {...}}        -> (spec, dict, None)
      - {"func": "...", "params": "expr(...)"}  -> (spec, {}, expr)
    """
    if raw is None:
        return None, {}, None
    if isinstance(raw, str):
        spec = raw.strip()
        if not spec:
            return None, {}, None
        if ":" not in spec:
            raise ValueError(
                f"[{task_name}] condition 必须形如 '模块:函数' 或 '实例:方法'，"
                f"收到 {spec!r}")
        return spec, {}, None
    if isinstance(raw, dict):
        spec = raw.get("func")
        if not spec:
            raise ValueError(f"[{task_name}] condition 缺少 'func' 字段: {raw!r}")
        spec = str(spec).strip()
        if ":" not in spec:
            raise ValueError(
                f"[{task_name}] condition.func 必须形如 '模块:函数' 或 '实例:方法'")
        raw_params = raw.get("params") or {}
        params, params_expr = _parse_params_field(
            raw_params, f"{task_name}.condition")
        return spec, params, params_expr
    raise ValueError(f"[{task_name}] condition 类型非法: {raw!r}")


# =========================================================================== #
# 类 / 函数加载
# =========================================================================== #
def _load_class(path: str) -> type:
    if ":" in path:
        mod_name, cls_name = path.split(":", 1)
    else:
        mod_name, cls_name = path.rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    cls = getattr(mod, cls_name)
    if not isinstance(cls, type):
        raise TypeError(f"{path} 不是类")
    return cls


def _load_module_attr(path: str) -> Any:
    if ":" in path:
        mod_name, attr_path = path.split(":", 1)
    else:
        mod_name, attr_path = path.rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    obj: Any = mod
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return obj


# =========================================================================== #
# JSON 序列化：控制换行层级
# =========================================================================== #
def _json_dumps_compact(obj: Any, indent: int = 2, max_depth: int = 3) -> str:
    IND = " " * indent

    def _dump(o: Any, level: int) -> str:
        pad = IND * level
        pad_inner = IND * (level + 1)
        if isinstance(o, dict):
            if not o:
                return "{}"
            if level >= max_depth:
                return json.dumps(o, ensure_ascii=False,
                                  separators=(", ", ": "))
            parts = []
            for k, v in o.items():
                k_s = json.dumps(k, ensure_ascii=False)
                parts.append(f"{pad_inner}{k_s}: {_dump(v, level + 1)}")
            return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
        if isinstance(o, list):
            if not o:
                return "[]"
            if level >= max_depth:
                return json.dumps(o, ensure_ascii=False,
                                  separators=(", ", ": "))
            parts = []
            for v in o:
                parts.append(f"{pad_inner}{_dump(v, level + 1)}")
            return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
        return json.dumps(o, ensure_ascii=False)

    return _dump(obj, 0)


# =========================================================================== #
# 实例池
# =========================================================================== #
@dataclass
class InstanceSpec:
    name: str
    class_path: str
    args: Dict[str, Any] = field(default_factory=dict)
    lifetime: str = "call"


class InstancePool:
    def __init__(self):
        self._specs: Dict[str, InstanceSpec] = {}
        self._permanent: Dict[str, Any] = {}

    def update_specs(self, specs: Dict[str, InstanceSpec]) -> None:
        for name, new_spec in specs.items():
            old = self._specs.get(name)
            if old is None:
                continue
            if (old.class_path != new_spec.class_path
                    or old.args != new_spec.args
                    or old.lifetime != new_spec.lifetime):
                self._destroy_permanent(name)
                log.info("实例 [%s] 配置变更，销毁旧实例", name)
        for name in list(self._permanent.keys()):
            if name not in specs:
                self._destroy_permanent(name)
        self._specs = dict(specs)

    def has(self, name: str) -> bool:
        return name in self._specs

    def get_spec(self, name: str) -> Optional[InstanceSpec]:
        return self._specs.get(name)

    def get(self, name: str, batch_ctx: Dict[str, Any]) -> Any:
        spec = self._specs[name]
        if spec.lifetime == "permanent":
            if name not in self._permanent:
                self._permanent[name] = self._create(spec)
                log.info("创建 permanent 实例 [%s] = %s", name, spec.class_path)
            return self._permanent[name]
        if spec.lifetime == "batch":
            if name not in batch_ctx:
                batch_ctx[name] = self._create(spec)
                log.info("创建 batch 实例 [%s] = %s", name, spec.class_path)
            return batch_ctx[name]
        return self._create(spec)

    def close_one(self, name: str, inst: Any) -> None:
        self._close(name, inst)

    def release_batch(self, batch_ctx: Dict[str, Any]) -> None:
        if not batch_ctx:
            return
        for name, inst in list(batch_ctx.items()):
            self._close(name, inst)
            log.info("释放 batch 实例 [%s]", name)
        batch_ctx.clear()

    def shutdown(self) -> None:
        for name in list(self._permanent.keys()):
            self._destroy_permanent(name)

    def _destroy_permanent(self, name: str) -> None:
        inst = self._permanent.pop(name, None)
        if inst is not None:
            self._close(name, inst)

    def _create(self, spec: InstanceSpec) -> Any:
        cls = _load_class(spec.class_path)
        return cls(**spec.args)

    def _close(self, name: str, inst: Any) -> None:
        for method in ("close", "disconnect", "shutdown", "stop"):
            fn = getattr(inst, method, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    log.exception("关闭实例 [%s] 出错", name)
                return
        exit_fn = getattr(inst, "__exit__", None)
        if callable(exit_fn):
            try:
                exit_fn(None, None, None)
            except Exception:
                log.exception("__exit__ [%s] 出错", name)


# =========================================================================== #
# 任务
# =========================================================================== #
@dataclass
class Task:
    name: str
    func_spec: str
    interval: Optional[str]
    next_run: Optional[datetime]
    params: Dict[str, Any] = field(default_factory=dict)
    params_expr: Optional[str] = None
    deps_groups: List[List[str]] = field(default_factory=list)
    condition_spec: Optional[str] = None
    condition_params: Dict[str, Any] = field(default_factory=dict)
    condition_params_expr: Optional[str] = None
    validate: int = 1
    timeout: Optional[float] = None
    retry: int = 0
    retry_backoff: Any = None
    start_at_raw: Any = None

    last_status: str = "pending"
    prev_status: str = "pending"
    last_run: Optional[datetime] = None
    last_error: Optional[str] = None
    run_count: int = 0
    fail_count: int = 0
    running: bool = False
    pending_remove: bool = False
    retries_done: int = 0

    @property
    def is_event(self) -> bool:
        return self.interval == EVENT

    @property
    def is_once(self) -> bool:
        return self.interval is None


# =========================================================================== #
# 调度器
# =========================================================================== #
class Scheduler:
    def __init__(self, name: str = "scheduler"):
        self.name = name
        self._tasks: Dict[str, Task] = {}
        self._cv = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self._config_path: Optional[str] = None
        self._state_file: Optional[str] = None
        self._lock_path: Optional[str] = None
        self._lock_fd = None
        self._reload_flag = False
        self._state: Dict[str, dict] = {}

        self._notify: Dict[str, str] = {}
        self._hook_pool = ThreadPoolExecutor(max_workers=4,
                                             thread_name_prefix="hook")

        self._instances = InstancePool()

        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_thread: Optional[threading.Thread] = None
        self._async_lock = threading.Lock()

    # ================================================================== #
    # 单实例锁
    # ================================================================== #
    def _try_lock(self) -> bool:
        if not self._lock_path:
            return True
        try:
            self._lock_fd = open(self._lock_path, "w")
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_fd.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            return True
        except (OSError, IOError):
            if self._lock_fd:
                try:
                    self._lock_fd.close()
                except Exception:
                    pass
                self._lock_fd = None
            return False

    def _release_lock(self):
        if self._lock_fd:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._lock_fd.close()
            except Exception:
                pass
            self._lock_fd = None

    # ================================================================== #
    # 配置加载
    # ================================================================== #
    def load_config(self, path: str, continue_mode: bool = False) -> None:
        self._config_path = os.path.abspath(path)
        base, _ = os.path.splitext(self._config_path)
        self._state_file = base + ".state.json"
        self._lock_path = base + ".lock"

        if not self._try_lock():
            raise RuntimeError(
                f"另一个调度器实例正在使用 {self._config_path}（锁: {self._lock_path}）")

        if continue_mode:
            self._load_state()

        self._reload_config()

        if continue_mode:
            self._apply_state_to_tasks()

    def request_reload(self) -> None:
        with self._cv:
            self._reload_flag = True
            self._cv.notify_all()

    def _load_state(self) -> None:
        if not self._state_file or not os.path.exists(self._state_file):
            log.info("续跑模式: 未找到状态文件 %s，按正常流程启动", self._state_file)
            return
        try:
            with open(self._state_file, encoding="utf-8") as f:
                data = json.load(f)
            self._state = data.get("tasks", {})
            log.info("续跑模式: 读取 %s（%d 个任务记录，保存于 %s）",
                     self._state_file, len(self._state), data.get("saved_at"))
        except Exception:
            log.exception("状态文件损坏，备份并忽略: %s", self._state_file)
            try:
                os.replace(self._state_file, self._state_file + ".corrupt")
            except Exception:
                pass
            self._state = {}

    # >>> FIX 1: _apply_state_to_tasks 不改 last_status，只改 next_run
    def _apply_state_to_tasks(self) -> None:
        """
        把上次运行状态应用到当前任务。

        规则：
          - failed  任务：next_run = now，让主循环立即重跑
          - skipped 任务：next_run = now，让主循环重新评估依赖
          - ok      任务：保持原节奏（next_run 由 config 的 start_at 计算）
          - pending 任务：保持原计划

        **不修改 last_status**：
          - 保留 failed / skipped 让 _deps_blocked_reason 正确阻塞下游
          - 避免 _save_state() 把历史状态覆盖成 pending
        """
        if not self._state:
            return
        now = datetime.now()
        actions: List[str] = []
        for name, t in self._tasks.items():
            st = self._state.get(name)
            if not st:
                continue

            prev = st.get("last_status", "pending")
            t.prev_status = prev
            t.last_status = prev                 # ← 保留原状态
            t.run_count = st.get("run_count", 0)
            t.fail_count = st.get("fail_count", 0)
            t.last_error = st.get("last_error")
            lr = st.get("last_run")
            if lr:
                try:
                    t.last_run = datetime.fromisoformat(lr)
                except Exception:
                    pass

            if prev == "failed":
                t.next_run = now
                actions.append(f"{name}(failed→立即重跑)")
            elif prev == "skipped":
                t.next_run = now
                actions.append(f"{name}(skipped→重新评估依赖)")

        if actions:
            log.info("续跑模式: %d 个任务被重新激活: %s",
                     len(actions), "; ".join(actions))
        else:
            log.info("续跑模式: 无失败/跳过任务，按原计划运行")

    def _save_state(self) -> None:
        if not self._state_file:
            return
        with self._cv:
            snapshot = {
                name: {
                    "last_status": t.last_status,
                    "prev_status": t.prev_status,
                    "last_run": t.last_run.isoformat() if t.last_run else None,
                    "last_error": t.last_error,
                    "run_count": t.run_count,
                    "fail_count": t.fail_count,
                }
                for name, t in self._tasks.items()
            }
        try:
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"tasks": snapshot, "saved_at": datetime.now().isoformat()},
                    f, ensure_ascii=False, indent=2,
                )
            os.replace(tmp, self._state_file)
        except Exception:
            log.exception("保存状态失败")

    def _reload_config(self) -> None:
        if not self._config_path:
            return
        log.info("读取配置文件: %s", self._config_path) 
        try:
            with open(self._config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            log.exception("读取配置失败: %s", self._config_path)
            return
        for t in cfg.get("tasks", []):
            log.info("  读到任务: name=%r, func=%r", t.get("name"), t.get("func"))

        self._notify = {k: v for k, v in (cfg.get("notify") or {}).items() if v}

        new_specs: Dict[str, InstanceSpec] = {}
        for iname, spec in cfg.get("instances", {}).items():
            try:
                new_specs[iname] = InstanceSpec(
                    name=iname,
                    class_path=spec["class"],
                    args=dict(spec.get("args", {})),
                    lifetime=spec.get("lifetime", "call"),
                )
            except Exception:
                log.exception("解析实例 [%s] 失败", iname)
        self._instances.update_specs(new_specs)

        specs = {t["name"]: t for t in cfg.get("tasks", [])}
        with self._cv:
            old_names = set(self._tasks.keys())
            new_names = set(specs.keys())

            for name in old_names - new_names:
                t = self._tasks[name]
                if t.running:
                    log.warning("[%s] 正在运行，标记 pending_remove", name)
                    t.pending_remove = True
                else:
                    del self._tasks[name]
                    log.info("[%s] 已移除", name)

            for name, spec in specs.items():
                if name in self._tasks:
                    self._update_task(self._tasks[name], spec)
                else:
                    try:
                        self._tasks[name] = self._build_task(spec)
                        t = self._tasks[name]
                        nr = (t.next_run.strftime("%Y-%m-%d %H:%M:%S")
                              if t.next_run else "无")
                        pmark = "动态" if t.params_expr else "静态"
                        cnd = t.condition_spec or "-"
                        cpm = "动态" if t.condition_params_expr else "静态"
                        log.info("[%s] 新增 validate=%s interval=%s retry=%d "
                                 "params=%s condition=%s cond.params=%s next=%s",
                                 name, t.validate, t.interval or "(仅一次)",
                                 t.retry, pmark, cnd, cpm, nr)
                    except Exception:
                        log.exception("[%s] 创建失败", name)

            self._cv.notify_all()

        self._check_dependency_cycles()

    # >>> FIX 2: _build_task 事件任务支持 start_at
    def _build_task(self, spec: Dict[str, Any]) -> Task:
        name = spec["name"]
        func_spec = spec["func"]
        if ":" not in func_spec:
            raise ValueError(f"[{name}] func 必须形如 'module:function' 或 'instance:method'")

        interval = _normalize_interval(spec.get("interval"))
        deps_groups = _parse_deps(spec.get("deps", []))
        start_raw = spec.get("start_at")
        retry = int(spec.get("retry", 0))
        if retry < 0:
            raise ValueError(f"[{name}] retry 必须 >= 0")
        retry_backoff = _parse_backoff(spec.get("retry_backoff"))

        params, params_expr = _parse_params_field(spec.get("params"), name)
        condition_spec, condition_params, condition_params_expr = _parse_condition(
            spec.get("condition"), name)

        if interval == EVENT:
            # 事件驱动任务：
            #   - 无 deps         -> 配置错误，永不触发
            #   - 有 start_at     -> 先等 start_at，到点后检查 deps
            #   - start_at=null   -> 纯等 deps
            if not deps_groups:
                log.error("[%s] interval=0（事件驱动）必须有 deps，"
                          "此任务永不触发，请修复配置", name)
                next_run = None
            elif start_raw is not None and start_raw != "":
                next_run = parse_start_at(start_raw)
            else:
                next_run = None
        elif interval is None:
            next_run = parse_start_at(start_raw)
        else:
            start = parse_start_at(start_raw)
            next_run = advance_to_after(start, interval, datetime.now())

        return Task(
            name=name,
            func_spec=func_spec,
            interval=interval,
            next_run=next_run,
            params=params,
            params_expr=params_expr,
            deps_groups=deps_groups,
            condition_spec=condition_spec,
            condition_params=condition_params,
            condition_params_expr=condition_params_expr,
            validate=int(spec.get("validate", 1)),
            timeout=spec.get("timeout"),
            retry=retry,
            retry_backoff=retry_backoff,
            start_at_raw=start_raw,
        )

    # >>> FIX 3: _update_task 事件任务支持 start_at 变更
    # >>> FIX NEW: interval 变更时无条件重算 next_run；validate 0→1 时只补 None 的 next_run
    def _update_task(self, t: Task, spec: Dict[str, Any]) -> None:
        note = "（运行中）" if t.running else ""

        new_func_spec = spec["func"]
        if new_func_spec != t.func_spec:
            t.func_spec = new_func_spec
            log.info("[%s] func 更新为 %s %s", t.name, new_func_spec, note)

        new_params, new_params_expr = _parse_params_field(
            spec.get("params"), t.name)
        if new_params != t.params or new_params_expr != t.params_expr:
            t.params = new_params
            t.params_expr = new_params_expr
            mark = "动态" if new_params_expr else "静态"
            log.info("[%s] params 更新（%s）%s", t.name, mark, note)

        new_deps = _parse_deps(spec.get("deps", []))
        if new_deps != t.deps_groups:
            t.deps_groups = new_deps
            log.info("[%s] 依赖更新 %s", t.name, note)

        new_cond_spec, new_cond_params, new_cond_params_expr = _parse_condition(
            spec.get("condition"), t.name)
        if new_cond_spec != t.condition_spec:
            t.condition_spec = new_cond_spec
            log.info("[%s] condition 更新为 %s %s",
                     t.name, new_cond_spec, note)
        if (new_cond_params != t.condition_params
                or new_cond_params_expr != t.condition_params_expr):
            t.condition_params = new_cond_params
            t.condition_params_expr = new_cond_params_expr
            mark = "动态" if new_cond_params_expr else "静态"
            log.info("[%s] condition.params 更新（%s）%s", t.name, mark, note)

        new_validate = int(spec.get("validate", 1))
        was = t.validate
        t.validate = new_validate
        if new_validate != was:
            log.info("[%s] validate 更新为 %s %s", t.name, new_validate, note)

        t.timeout = spec.get("timeout")

        new_retry = int(spec.get("retry", 0))
        if new_retry < 0:
            raise ValueError(f"[{t.name}] retry 必须 >= 0")
        if new_retry != t.retry:
            t.retry = new_retry
            log.info("[%s] retry 更新为 %d %s", t.name, new_retry, note)

        new_backoff = _parse_backoff(spec.get("retry_backoff"))
        if new_backoff != t.retry_backoff:
            t.retry_backoff = new_backoff
            log.info("[%s] retry_backoff 更新为 %r %s", t.name, new_backoff, note)

        new_interval = _normalize_interval(spec.get("interval"))
        interval_changed = new_interval != t.interval
        if interval_changed:
            t.interval = new_interval
            log.info("[%s] interval 更新为 %r %s", t.name, new_interval, note)

        new_start_raw = spec.get("start_at")
        start_changed = str(new_start_raw) != str(t.start_at_raw)

        def _calc_event_next_run():
            """事件任务重算 next_run：有 start_at 用时间，否则 None"""
            if t.start_at_raw is not None and t.start_at_raw != "":
                return parse_start_at(t.start_at_raw)
            return None

        def _calc_by_start_and_interval():
            """按 start_at + interval 重算 next_run（通用）"""
            start = (parse_start_at(t.start_at_raw)
                     if t.start_at_raw is not None and t.start_at_raw != ""
                     else datetime.now())
            return advance_to_after(start, t.interval, datetime.now())

        if start_changed:
            t.start_at_raw = new_start_raw
            if t.interval == EVENT:
                t.next_run = _calc_event_next_run()
            elif t.interval is None:
                t.next_run = parse_start_at(new_start_raw)
            else:
                t.next_run = advance_to_after(parse_start_at(new_start_raw),
                                              t.interval, datetime.now())
            log.info("[%s] start_at 更新 next=%s %s", t.name,
                     t.next_run.strftime("%Y-%m-%d %H:%M:%S") if t.next_run else "None",
                     note)
        elif interval_changed:
            # >>> FIX NEW: interval 变了 → 必须重算 next_run，让新规则立即生效
            if t.interval == EVENT:
                t.next_run = _calc_event_next_run()
            elif t.interval is None:
                # 变成一次性任务：按 start_at 计算
                t.next_run = parse_start_at(t.start_at_raw)
            else:
                # 变成周期任务：从 start_at 网格重算
                t.next_run = _calc_by_start_and_interval()
            log.info("[%s] interval 变更后重算 next=%s %s", t.name,
                     t.next_run.strftime("%Y-%m-%d %H:%M:%S") if t.next_run else "None",
                     note)
        elif (t.validate == 1 and was == 0):
            # >>> FIX NEW: 从暂停恢复：只补"漏掉的"任务，不重跑历史
            if t.interval == EVENT:
                if t.next_run is None:
                    t.next_run = _calc_event_next_run()
            elif t.interval is None:
                # 一次性任务，恢复也不重跑
                pass
            elif t.next_run is None:
                # 周期任务之前是 None（可能从未跑过），按 start_at 重算
                t.next_run = _calc_by_start_and_interval()

    # ================================================================== #
    # 循环依赖检测
    # ================================================================== #
    def _check_dependency_cycles(self) -> bool:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self._tasks}
        has_cycle = [False]

        def dfs(name, path):
            color[name] = GRAY
            path.append(name)
            for group in self._tasks[name].deps_groups:
                for dep in group:
                    if dep not in self._tasks:
                        continue
                    if color[dep] == GRAY:
                        cycle_start = path.index(dep)
                        cycle = path[cycle_start:] + [dep]
                        log.error("检测到循环依赖: %s", " -> ".join(cycle))
                        has_cycle[0] = True
                        for n in cycle[:-1]:
                            self._tasks[n].validate = 0
                            log.error("  [%s] 因循环依赖被禁用，请修复配置", n)
                    elif color[dep] == WHITE:
                        dfs(dep, path)
            path.pop()
            color[name] = BLACK

        for n in list(self._tasks):
            if color[n] == WHITE:
                dfs(n, [])

        return has_cycle[0]

    # ================================================================== #
    # 动态参数
    # ================================================================== #
    def update_params(self, name: str, **kwargs) -> None:
        with self._cv:
            if name in self._tasks:
                self._tasks[name].params.update(kwargs)
                self._cv.notify_all()
                log.info("动态更新 [%s] 参数: %s", name, kwargs)

    def set_params(self, name: str, params: Dict[str, Any]) -> None:
        with self._cv:
            if name in self._tasks:
                self._tasks[name].params = dict(params)
                self._tasks[name].params_expr = None
                self._cv.notify_all()

    def set_enabled(self, name: str, enabled: bool) -> bool:
        with self._cv:
            if name not in self._tasks:
                return False
            self._tasks[name].validate = 1 if enabled else 0
            self._cv.notify_all()
        log.info("[%s] %s", name, "启用" if enabled else "暂停")
        return True

    def run_now(self, name: str) -> bool:
        t = self._tasks.get(name)
        if not t:
            return False
        threading.Thread(target=self._run_task, args=(t, {}),
                         daemon=True, name=f"manual-{name}").start()
        return True

    # ================================================================== #
    # 异步事件循环
    # ================================================================== #
    def _ensure_async_loop(self) -> asyncio.AbstractEventLoop:
        with self._async_lock:
            if self._async_loop is not None and self._async_loop.is_running():
                return self._async_loop
            loop = asyncio.new_event_loop()

            def runner():
                asyncio.set_event_loop(loop)
                log.info("asyncio 事件循环启动")
                loop.run_forever()
                try:
                    pending = asyncio.all_tasks(loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                loop.close()
                log.info("asyncio 事件循环关闭")

            self._async_loop = loop
            self._async_thread = threading.Thread(
                target=runner, name=f"{self.name}-async", daemon=True)
            self._async_thread.start()
            return loop

    def _stop_async_loop(self):
        with self._async_lock:
            loop = self._async_loop
            self._async_loop = None
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._async_thread:
            self._async_thread.join(timeout=3)

    # ================================================================== #
    # 通知钩子
    # ================================================================== #
    def _fire_hook(self, event: str, task: Task, extra: Optional[dict] = None):
        spec = self._notify.get(event)
        if not spec:
            return

        def _run():
            try:
                fn = _load_module_attr(spec)
                kwargs = {
                    "task_name": task.name,
                    "status": task.last_status,
                    "error": task.last_error,
                    "when": datetime.now().isoformat(),
                    "run_count": task.run_count,
                    "fail_count": task.fail_count,
                }
                if extra:
                    kwargs.update(extra)
                sig = inspect.signature(fn)
                accepts_kwargs = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values())
                if accepts_kwargs:
                    accepted = kwargs
                else:
                    accepted = {k: v for k, v in kwargs.items()
                                if k in sig.parameters}
                if inspect.iscoroutinefunction(fn):
                    loop = self._ensure_async_loop()
                    fut = asyncio.run_coroutine_threadsafe(fn(**accepted), loop)
                    fut.result(timeout=30)
                else:
                    fn(**accepted)
            except Exception:
                log.exception("[hook:%s] 执行失败 spec=%s", event, spec)

        try:
            self._hook_pool.submit(_run)
        except RuntimeError:
            log.warning("[hook:%s] 池已关闭，忽略", event)

    # ================================================================== #
    # 启停
    # ================================================================== #
    def start(self) -> "Scheduler":
        if self._thread and self._thread.is_alive():
            return self
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name=self.name,
                                        daemon=True)
        self._thread.start()
        return self

    # >>> FIX 最新: stop() 不再自动写状态文件
    #                只有 save / cancel 命令会写
    def stop(self, timeout: float = 3) -> None:
        with self._cv:
            running = [t.name for t in self._tasks.values() if t.running]
            if running:
                log.warning("正在运行的任务: %s（最多等 %.0fs）",
                            ", ".join(running), timeout)
            self._stop = True
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout)
        self._stop_async_loop()
        self._instances.shutdown()
        # >>> FIX 最新: 删除自动保存状态（由 save / cancel 命令显式触发）
        # try:
        #     self._save_state()
        # except Exception:
        #     log.exception("退出时保存状态失败")
        try:
            self._hook_pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._hook_pool.shutdown(wait=False)
        self._release_lock()
        log.info("调度器已停止")

    def save_start_at_to_config(self) -> int:
        """回写 next_run 到 start_at（细节略，见上轮）"""
        if not self._config_path:
            return 0
        try:
            with open(self._config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            log.exception("读取配置文件失败，无法回写 start_at")
            return 0
        updated = 0
        with self._cv:
            for spec in cfg.get("tasks", []):
                name = spec.get("name")
                if not name:
                    continue
                t = self._tasks.get(name)
                if t is None:
                    continue
                orig = spec.get("start_at")
                if orig is None or orig == "":
                    continue
                if t.next_run is None:
                    continue
                new_start = t.next_run.strftime("%Y-%m-%d %H:%M:%S")
                if str(orig) != new_start:
                    spec["start_at"] = new_start
                    updated += 1
                    log.info("[%s] start_at 回写: %s -> %s",
                             name, orig, new_start)
        if updated == 0:
            log.info("无需回写 start_at")
            return 0
        try:
            tmp = self._config_path + ".tmp"
            text = _json_dumps_compact(cfg, indent=2, max_depth=3)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.write("\n")
            os.replace(tmp, self._config_path)
            log.info("已回写 %d 个任务的 start_at 到 %s",
                     updated, self._config_path)
        except Exception:
            log.exception("写回配置文件失败")
            return 0
        return updated

    # ================================================================== #
    # 依赖检查
    # ================================================================== #
    def _deps_blocked_reason(self, task: Task) -> Optional[str]:
        if not task.deps_groups:
            return None
        reasons: List[str] = []
        for group in task.deps_groups:
            group_ok = True
            group_why: List[str] = []
            for dep_name in group:
                dep = self._tasks.get(dep_name)
                if dep is None:
                    group_ok = False
                    group_why.append(f"{dep_name}(未注册)")
                    break
                if not dep.validate:
                    group_ok = False
                    group_why.append(f"{dep_name}(已禁用)")
                    break
                if dep.running:
                    group_ok = False
                    group_why.append(f"{dep_name}(运行中)")
                    break
                if dep.last_status != "ok":
                    group_ok = False
                    group_why.append(f"{dep_name}({dep.last_status})")
                    break
            if group_ok:
                return None
            reasons.append("(" + "+".join(group_why) + ")")
        return " or ".join(reasons)

    # ================================================================== #
    # 拓扑排序
    # ================================================================== #
    def _topo_sort(self, tasks: List[Task]) -> List[Task]:
        by_name = {t.name: t for t in tasks}
        visited: set = set()
        in_stack: set = set()
        result: List[Task] = []

        def visit(t: Task):
            if t.name in visited:
                return
            if t.name in in_stack:
                log.warning("运行时检测到循环依赖，跳过: %s", t.name)
                return
            in_stack.add(t.name)
            for group in t.deps_groups:
                for dep_name in group:
                    dep = by_name.get(dep_name)
                    if dep is not None:
                        visit(dep)
            in_stack.discard(t.name)
            visited.add(t.name)
            result.append(t)

        for t in tasks:
            visit(t)
        return result

    # ================================================================== #
    # 主循环
    # ================================================================== #
    # >>> FIX 4: 主循环不再无条件跳过事件任务；只有 next_run=None 才跳过
    def _loop(self):
        log.info("调度线程启动")
        while True:
            do_reload = False
            with self._cv:
                if self._stop:
                    break
                if self._reload_flag:
                    self._reload_flag = False
                    do_reload = True
            if do_reload:
                try:
                    self._reload_config()
                except Exception:
                    log.exception("重载配置失败")

            with self._cv:
                if self._stop:
                    break
                now = datetime.now()
                due: List[Task] = []
                earliest: Optional[datetime] = None
                for t in self._tasks.values():
                    if not t.validate or t.running or t.pending_remove:
                        continue
                    # FIX: 删除了 if t.interval == EVENT: continue
                    # 事件任务在 next_run=None 时跳过（纯等 deps）；
                    # 有 start_at 的事件任务 next_run 有值，正常参与调度。
                    if t.next_run is None:
                        continue
                    if t.next_run <= now:
                        due.append(t)
                    elif earliest is None or t.next_run < earliest:
                        earliest = t.next_run

                if not due:
                    if earliest is None:
                        self._cv.wait()
                    else:
                        timeout = max(0.0, (earliest - now).total_seconds())
                        self._cv.wait(timeout=timeout)
                    continue
                due.sort(key=lambda x: x.next_run)

            try:
                self._run_batch(due)
            except Exception:
                log.exception("批次执行出错")

        log.info("调度线程退出")

    def _run_batch(self, due: List[Task]) -> None:
        ordered = self._topo_sort(due)
        queue: List[Task] = list(ordered)
        finished: set = set()
        skipped: set = set()
        batch_ctx: Dict[str, Any] = {}
        max_iter = len(due) * 10 + 50
        it = 0

        try:
            while queue:
                it += 1
                if it > max_iter:
                    log.warning("批次迭代超限 (%d)，剩余任务: %s",
                                max_iter, [t.name for t in queue])
                    break

                task = queue.pop(0)
                if task.name in finished:
                    continue
                with self._cv:
                    if not task.validate or task.running or task.pending_remove:
                        continue

                result = self._run_task(task, batch_ctx)

                if task.pending_remove:
                    with self._cv:
                        self._tasks.pop(task.name, None)
                    log.info("[%s] 已运行完毕，按配置删除", task.name)
                    continue

                if result == "retrying":
                    continue

                if result == "final_ok":
                    finished.add(task.name)
                    self._activate_downstream(task, queue, finished, skipped)
                elif result == "final_failed":
                    finished.add(task.name)
                elif result == "final_skipped":
                    skipped.add(task.name)
        finally:
            try:
                self._instances.release_batch(batch_ctx)
            except Exception:
                log.exception("释放 batch 实例失败")

    # >>> FIX 5: _activate_downstream 只触发 next_run=None 的事件任务
    def _activate_downstream(self, upstream: Task, queue: List[Task],
                             finished: set, skipped: set) -> None:
        now = datetime.now()
        in_queue = {q.name for q in queue}

        # 时间调度的下游
        for t in list(self._tasks.values()):
            if t.name == upstream.name:
                continue
            if t.name in finished or t.name in in_queue or t.name in skipped:
                continue
            if not t.validate or t.running or t.pending_remove:
                continue
            is_down = any(upstream.name in group for group in t.deps_groups)
            if not is_down:
                continue
            if self._deps_blocked_reason(t) is None:
                t.next_run = now
                queue.append(t)
                log.info("[%s] 上游 [%s] 成功，激活下游", t.name, upstream.name)

        # 事件驱动 —— 只处理 next_run=None 的（有 start_at 的交给主循环）
        for t in list(self._tasks.values()):
            if not t.is_event:
                continue
            if t.next_run is not None:              # ← 新增：有 start_at 不在这里触发
                continue
            if t.name in finished or t.name in in_queue:
                continue
            if not t.validate or t.running or t.pending_remove:
                continue
            if self._deps_blocked_reason(t) is None:
                queue.append(t)
                log.info("[%s] 事件驱动：依赖满足，触发", t.name)

    # ================================================================== #
    # 函数解析
    # ================================================================== #
    def _resolve_task_func(self, task: Task, batch_ctx: Dict[str, Any]):
        return self._resolve_callable(
            task.func_spec, batch_ctx, ctx=f"task:{task.name}")

    def _resolve_callable(self, spec: str, batch_ctx: Dict[str, Any],
                          ctx: str):
        if ":" not in spec:
            raise ValueError(f"[{ctx}] 非法引用: {spec!r}")
        prefix, name = spec.split(":", 1)

        if self._instances.has(prefix):
            spec_obj = self._instances.get_spec(prefix)
            inst = self._instances.get(prefix, batch_ctx)
            fn = getattr(inst, name, None)
            if fn is None:
                raise AttributeError(f"[{ctx}] 实例 [{prefix}] 没有属性 {name!r}")
            cleanup = (inst, prefix) if spec_obj.lifetime == "call" else None
            return fn, cleanup

        fn = _load_module_attr(spec)
        return fn, None

    def _resolve_params_expr(self, expr: str, batch_ctx: Dict[str, Any],
                             ctx: str) -> Dict[str, Any]:
        path, kwargs = _parse_params_expr(expr)
        head, attrs = path[0], path[1:]
        cleanup = None
        try:
            if self._instances.has(head):
                spec_obj = self._instances.get_spec(head)
                obj = self._instances.get(head, batch_ctx)
                if spec_obj.lifetime == "call":
                    cleanup = (obj, head)
            else:
                try:
                    obj = importlib.import_module(head)
                except ImportError as e:
                    raise ValueError(
                        f"[{ctx}] params 表达式无法解析 {head!r}："
                        f"既不是已注册实例，也不是可导入模块 ({e})")
            for attr in attrs:
                obj = getattr(obj, attr)
            if not callable(obj):
                raise TypeError(
                    f"[{ctx}] params 表达式 {expr!r} 的最终目标不是可调用对象")
            result = obj(**kwargs)
            if not isinstance(result, dict):
                raise TypeError(
                    f"[{ctx}] params 表达式 {expr!r} 必须返回 dict，"
                    f"实际返回 {type(result).__name__}")
            return result
        finally:
            if cleanup is not None:
                inst, iname = cleanup
                try:
                    self._instances.close_one(iname, inst)
                except Exception:
                    log.exception("[%s] 关闭 params call 实例 [%s] 失败", ctx, iname)

    def _resolve_actual_params(self,
                               params_dict: Dict[str, Any],
                               params_expr: Optional[str],
                               batch_ctx: Dict[str, Any],
                               ctx: str) -> Dict[str, Any]:
        if params_expr:
            return self._resolve_params_expr(params_expr, batch_ctx, ctx)
        return dict(params_dict)

    # ================================================================== #
    # condition 检查
    # ================================================================== #
    def _check_condition(self, task: Task, batch_ctx: Dict[str, Any]) -> bool:
        cleanup = None
        try:
            try:
                fn, cleanup = self._resolve_callable(
                    task.condition_spec, batch_ctx,
                    ctx=f"task:{task.name}.condition")
            except Exception:
                log.exception("[%s] condition 解析失败: %s",
                              task.name, task.condition_spec)
                return False

            try:
                actual_params = self._resolve_actual_params(
                    task.condition_params, task.condition_params_expr,
                    batch_ctx, ctx=f"task:{task.name}.condition.params")
            except Exception:
                log.exception("[%s] condition.params 解析失败", task.name)
                return False

            try:
                raw = self._execute(task, fn, actual_params)
            except Exception:
                log.exception("[%s] condition 执行失败: %s",
                              task.name, task.condition_spec)
                return False

            return bool(raw)
        finally:
            if cleanup is not None:
                inst, iname = cleanup
                try:
                    self._instances.close_one(iname, inst)
                except Exception:
                    log.exception("关闭 condition call 实例 [%s] 失败", iname)

    # ================================================================== #
    # 执行单个任务
    # ================================================================== #
    def _run_task(self, task: Task, batch_ctx: Dict[str, Any]) -> str:
        original_scheduled = task.next_run
        task.running = True
        task.last_run = datetime.now()
        cleanup = None

        try:
            # 1. 依赖
            blocked = self._deps_blocked_reason(task)
            if blocked:
                log.warning("[%s] 跳过执行，依赖未满足: %s", task.name, blocked)
                return self._finalize(task, "skipped", original_scheduled)

            # 2. condition
            if task.condition_spec:
                if not self._check_condition(task, batch_ctx):
                    log.info("[%s] condition 未通过，跳过执行: %s",
                             task.name, task.condition_spec)
                    return self._finalize(task, "skipped", original_scheduled)

            # 3. func
            try:
                func, cleanup = self._resolve_task_func(task, batch_ctx)
            except Exception as e:
                log.exception("[%s] 解析 func 失败: %s", task.name, task.func_spec)
                return self._on_failure(task, e, original_scheduled)

            # 4. params
            try:
                actual_params = self._resolve_actual_params(
                    task.params, task.params_expr,
                    batch_ctx, ctx=f"task:{task.name}.params")
            except Exception as e:
                log.exception("[%s] params 解析失败: %r",
                              task.name, task.params_expr)
                return self._on_failure(task, e, original_scheduled)

            # 5. 执行
            attempt = task.retries_done + 1
            total = task.retry + 1
            log.info("[%s] 开始执行 func=%s params=%s (第 %d/%d 次)",
                     task.name, task.func_spec, actual_params, attempt, total)

            try:
                self._execute(task, func, actual_params)
            except Exception as e:
                log.exception("[%s] 执行失败", task.name)
                return self._on_failure(task, e, original_scheduled)

            return self._finalize(task, "ok", original_scheduled)
        finally:
            if cleanup is not None:
                inst, iname = cleanup
                try:
                    self._instances.close_one(iname, inst)
                except Exception:
                    log.exception("关闭 call 实例 [%s] 失败", iname)
            task.running = False
            with self._cv:
                self._cv.notify_all()

    def _on_failure(self, task: Task, exc: Exception,
                    original_scheduled: Optional[datetime]) -> str:
        task.last_error = repr(exc)
        if task.retries_done < task.retry:
            task.retries_done += 1
            backoff = self._compute_backoff(task)
            task.last_status = "retrying"
            task.next_run = datetime.now() + timedelta(seconds=backoff)
            log.warning("[%s] 重试 %d/%d，%.1fs 后",
                        task.name, task.retries_done, task.retry, backoff)
            self._fire_hook("on_retry", task, extra={
                "retry_index": task.retries_done,
                "next_try_in": backoff,
            })
            return "retrying"
        else:
            task.retries_done = 0
            return self._finalize(task, "failed", original_scheduled)

    def _compute_backoff(self, task: Task) -> float:
        b = task.retry_backoff
        if b is None:
            return 0.0
        if isinstance(b, (int, float)):
            return float(b) * (2 ** (task.retries_done - 1))
        idx = min(task.retries_done - 1, len(b) - 1)
        return float(b[idx])

    def _finalize(self, task: Task, status: str,
                  scheduled_time: Optional[datetime]) -> str:
        prev = task.prev_status
        task.last_status = status
        task.prev_status = status

        if status == "ok":
            task.run_count += 1
            task.last_error = None
            if prev == "failed":
                log.info("[%s] 从失败中恢复", task.name)
                self._fire_hook("on_recover", task)
            else:
                self._fire_hook("on_success", task)
            log.info("[%s] 执行成功", task.name)
        elif status == "failed":
            task.fail_count += 1
            self._fire_hook("on_fail", task)
            log.error("[%s] 最终失败: %s", task.name, task.last_error)
        elif status == "skipped":
            self._fire_hook("on_skip", task)

        # 推进 next_run
        # 事件任务 / 一次性任务：执行后回到 None（等下次 deps 触发 / 永不执行）
        # 时间任务：推进到下一个网格点
        if task.interval == EVENT or task.interval is None:
            task.next_run = None
        else:
            now = datetime.now()
            base = scheduled_time or now
            task.next_run = advance_to_after(base, task.interval, now)

        # >>> FIX 最新: 不再自动写状态文件；state 保持在内存中
        #                由 save / cancel 命令显式保存
        # self._save_state()
        return f"final_{status}"

    def _execute(self, task: Task, func: Callable,
                 params: Dict[str, Any]) -> Any:
        if inspect.iscoroutinefunction(func):
            loop = self._ensure_async_loop()
            coro = func(**params)
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                if task.timeout:
                    return future.result(timeout=task.timeout)
                return future.result()
            except asyncio.TimeoutError:
                future.cancel()
                raise TimeoutError(f"异步任务 {task.name} 超时 {task.timeout}s")
        else:
            return func(**params)

    # ================================================================== #
    # 状态
    # ================================================================== #
    def status(self) -> Dict[str, dict]:
        now = datetime.now()
        with self._cv:
            return {
                name: {
                    "validate": t.validate,
                    "func": t.func_spec,
                    "condition": t.condition_spec,
                    "condition_params": dict(t.condition_params),
                    "condition_params_expr": t.condition_params_expr,
                    "interval": (t.interval if t.interval else
                                 ("事件驱动" if t.interval == EVENT else "(仅一次)")),
                    "retry": t.retry,
                    "retry_backoff": t.retry_backoff,
                    "next_run": (t.next_run.strftime("%Y-%m-%d %H:%M:%S")
                                 if t.next_run else "无"),
                    "in_seconds": (round((t.next_run - now).total_seconds(), 1)
                                   if t.next_run else None),
                    "running": t.running,
                    "last_status": t.last_status,
                    "last_error": t.last_error,
                    "ok": t.run_count,
                    "fail": t.fail_count,
                    "deps_groups": [list(g) for g in t.deps_groups],
                    "params": dict(t.params),
                    "params_expr": t.params_expr,
                }
                for name, t in self._tasks.items()
            }

    # >>> FIX NEW: print_status 增加显示 func
    def print_status(self) -> None:
        print("-" * 195)
        print(f"{'任务':<16}{'有效':<5}{'运行':<5}{'状态':<12}{'func':<28}"
              f"{'间隔':<10}{'重试':<5}{'下次运行':<21}{'剩余(s)':<10}"
              f"{'成功':<6}{'失败':<6}{'params':<10}{'依赖':<22}参数")
        print("-" * 195)
        for name, s in self.status().items():
            deps_str = " or ".join("(" + "+".join(g) + ")" for g in s["deps_groups"]) or "-"
            if len(deps_str) > 20:
                deps_str = deps_str[:17] + "..."
            fn = s["func"] or "-"
            if len(fn) > 26:
                fn = fn[:23] + "..."
            pmark = "动态" if s["params_expr"] else "静态"
            print(f"{name:<16}{s['validate']:<5}"
                  f"{'★' if s['running'] else '':<5}"
                  f"{s['last_status']:<12}{fn:<28}"
                  f"{s['interval']:<10}{s['retry']:<5}"
                  f"{s['next_run']:<21}{str(s['in_seconds']):<10}"
                  f"{s['ok']:<6}{s['fail']:<6}"
                  f"{pmark:<10}{deps_str:<22}{s['params']}")
        print("-" * 195)


# =========================================================================== #
# 主入口
# =========================================================================== #
# >>> FIX 最新: HELP_TEXT 增加 save 命令
HELP_TEXT = """
可用命令:
  status              显示所有任务的详细状态
  tasks               只列出任务名和当前状态
  run <name>          立即手动触发一次指定任务（不影响调度网格）
  pause <name>        暂停指定任务（validate=0）
  resume <name>       恢复指定任务（validate=1）
  update              重新加载配置文件
  save                保存配置文件和状态文件（不退出）
  cancel              保存配置和状态文件，取消所有任务并退出
  help                显示此帮助
  直接回车             无操作
""".strip()


def sch_main():
    global _GLOBAL_SCHEDULER

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    args = sys.argv[1:]
    continue_mode = False
    config_path = None
    if args[0] == "continue":
        continue_mode = True
        if len(args) < 2:
            print("用法: python scheduler.py continue <配置文件路径>")
            sys.exit(1)
        config_path = args[1]
    elif args[0] in ("-h", "--help", "help"):
        print(__doc__)
        sys.exit(0)
    else:
        config_path = args[0]

    _ = os.path.dirname(config_path)
    setup_logging(log_file=os.path.join(_, "scheduler.log"))

    sch = Scheduler()
    _GLOBAL_SCHEDULER = sch

    try:
        sch.load_config(config_path, continue_mode=continue_mode)
    except Exception:
        log.exception("首次加载配置失败")
        sys.exit(1)

    sch.start()

    # >>> FIX 最新: 抽出一个保存动作，save / cancel 命令共用
    def _do_save():
        """保存 config（start_at 回写）+ state 文件"""
        try:
            n = sch.save_start_at_to_config()
            if n > 0:
                print(f"已回写 {n} 个任务的 start_at 到配置文件")
            else:
                print("无需回写 start_at")
        except Exception:
            log.exception("回写 start_at 失败")
        try:
            sch._save_state()
            print(f"已保存状态文件: {sch._state_file}")
        except Exception:
            log.exception("保存状态失败")

    print()
    print("=" * 70)
    print(f"调度器已启动 | 配置: {config_path}")
    print(f"状态文件: {sch._state_file}")
    print(f"锁文件:   {sch._lock_path}")
    print(f"模式:     {'续跑 (continue)' if continue_mode else '正常'}")
    print("-" * 70)
    print(HELP_TEXT)
    print("=" * 70)
    print()

    try:
        while True:
            try:
                line = input("scheduler> ").strip()
            except EOFError:
                print("\n输入已关闭，退出...")
                break

            if line == "":
                continue
            if line == "update":
                sch.request_reload()
                print("已通知调度器重载配置")
            # >>> FIX 最新: 新增 save 命令：保存 config + state，不退出
            elif line == "save":
                print("正在保存...")
                _do_save()
            elif line == "cancel":
                print("正在取消所有任务和调度计划...")
                # >>> FIX 最新: cancel 也保存 config + state
                _do_save()
                break
            elif line == "status":
                sch.print_status()
            elif line == "tasks":
                # >>> FIX NEW: tasks 命令增加显示 func
                for name, s in sch.status().items():
                    flag = "★" if s["running"] else " "
                    print(f"  {flag} {name:<20} {s['func']:<30} "
                          f"{s['last_status']:<10} next={s['next_run']}")
            elif line == "help":
                print(HELP_TEXT)
            elif line.startswith("run "):
                name = line[4:].strip()
                if sch.run_now(name):
                    print(f"已手动触发 {name}")
                else:
                    print(f"任务不存在: {name}")
            elif line.startswith("pause "):
                name = line[6:].strip()
                if sch.set_enabled(name, False):
                    print(f"已暂停 {name}")
                else:
                    print(f"任务不存在: {name}")
            elif line.startswith("resume "):
                name = line[7:].strip()
                if sch.set_enabled(name, True):
                    print(f"已恢复 {name}")
                else:
                    print(f"任务不存在: {name}")
            else:
                print("无效命令。输入 help 查看帮助")
    except KeyboardInterrupt:
        print("\n收到中断信号，退出（不保存状态文件）...")
    finally:
        # >>> FIX 最新: stop() 内部不再保存 state 文件
        sch.stop(timeout=3)
        print("调度器已退出")


if __name__ == "__main__":
    sch_main()