# -*- coding: utf-8 -*-
"""
scheduler.py —— 多进程调度器

架构:
  主进程: 持有任务定义 + 全局状态；决定何时触发、是否分叉；空闲时 wait() 挂起
  Worker : 由主进程通过 Pipe 下发任务；执行 condition/params/func/retry；完成后回传

用法:
  python scheduler.py <cfg>            # 无任务名：按时间调度所有任务
  python scheduler.py <cfg> <task>     # 有任务名：立即从 task 开始跑整棵下游树

交互命令:
  status              显示所有任务状态
  run <task>          立即触发任务
  pause [<task>]      暂停（无任务名 = 全部）
  resume <task>       恢复
  update              重新加载配置
  save                保存配置文件 + 状态文件（不退出）
  cancel              保存配置文件 + 状态文件，然后退出
  help                帮助

最新 FIX（本次）:
  - 新增 save 命令：回写 start_at 到 config + 保存 state 文件，不退出
  - cancel 命令：先执行 save 逻辑，再退出
  - 状态保存策略调整：
      * 每个任务完成时（_handle_task_done）继续保存 state（链条推进落盘）
      * 主进程正常关闭时不再自动保存（_shutdown_all 里注释掉）
      * Ctrl+C / 异常退出：不保存
"""
from __future__ import annotations

import asyncio
import ast
import importlib
import inspect
import json
import logging
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from multiprocessing import Process, Pipe
from multiprocessing.connection import Connection, wait
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
def setup_logging(log_file: str = "scheduler.log", is_worker: bool = False):
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("scheduler")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    prefix = f"W{os.getpid()}" if is_worker else "MAIN"
    fmt = logging.Formatter(
        f"%(asctime)s [{prefix}] [%(levelname)s] %(message)s")

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
        raise ValueError(f"非法 interval: {spec!r}")

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
    if raw is None:
        return None, {}, None
    if isinstance(raw, str):
        spec = raw.strip()
        if not spec:
            return None, {}, None
        if ":" not in spec:
            raise ValueError(
                f"[{task_name}] condition 必须形如 '模块:函数' 或 '实例:方法'")
        return spec, {}, None
    if isinstance(raw, dict):
        spec = raw.get("func")
        if not spec:
            raise ValueError(f"[{task_name}] condition 缺少 'func' 字段")
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
# 实例池（Worker 内部使用）
# =========================================================================== #
@dataclass
class InstanceSpec:
    name: str
    class_path: str
    args: Dict[str, Any] = field(default_factory=dict)
    lifetime: str = "call"


class InstancePool:
    def __init__(self, specs: Dict[str, InstanceSpec]):
        self._specs = dict(specs)
        self._permanent: Dict[str, Any] = {}
        self._lock = threading.RLock()

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._specs

    def get_spec(self, name: str) -> Optional[InstanceSpec]:
        with self._lock:
            return self._specs.get(name)

    def get(self, name: str, batch_ctx: Dict[str, Any]) -> Any:
        with self._lock:
            spec = self._specs[name]
            if spec.lifetime == "permanent":
                if name not in self._permanent:
                    self._permanent[name] = self._create(spec)
                return self._permanent[name]
            if spec.lifetime == "batch":
                if name not in batch_ctx:
                    batch_ctx[name] = self._create(spec)
                return batch_ctx[name]
            return self._create(spec)

    def close_one(self, name: str, inst: Any) -> None:
        with self._lock:
            self._close(name, inst)

    def release_batch(self, batch_ctx: Dict[str, Any]) -> None:
        with self._lock:
            if not batch_ctx:
                return
            for name, inst in list(batch_ctx.items()):
                self._close(name, inst)
            batch_ctx.clear()

    def shutdown(self) -> None:
        with self._lock:
            for name in list(self._permanent.keys()):
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
# Task（仅主进程使用）
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

    @property
    def is_event(self) -> bool:
        return self.interval == EVENT

    @property
    def is_once(self) -> bool:
        return self.interval is None


# =========================================================================== #
# Worker 端（子进程）
# =========================================================================== #
def _worker_main(conn: Connection) -> None:
    """子进程主函数。被动响应主进程的 run_task / shutdown。"""
    setup_logging(is_worker=True)
    wlog = log
    wlog.info("Worker 启动 pid=%d", os.getpid())

    # Worker 内部状态：跨任务保留（batch / permanent 实例）
    instances_wire: Dict[str, Any] = {}
    pool: Optional[InstancePool] = None
    batch_ctx: Dict[str, Any] = {}
    async_mgr = _AsyncManager()

    try:
        while True:
            try:
                msg = conn.recv()
            except EOFError:
                wlog.warning("父进程断开，退出")
                break

            mtype = msg.get("type")

            if mtype == "run_task":
                task_def = msg["task"]
                instances_wire = msg.get("instances", {})
                if pool is None:
                    pool = _build_instance_pool(instances_wire)

                status, error = _worker_execute_task(
                    task_def, pool, batch_ctx, async_mgr, wlog)

                try:
                    conn.send({
                        "type": "task_done",
                        "pid": os.getpid(),
                        "task_name": task_def["name"],
                        "status": status,
                        "error": error,
                    })
                except Exception:
                    wlog.exception("发送 task_done 失败")
                    break

            elif mtype == "shutdown":
                try:
                    conn.send({"type": "bye", "pid": os.getpid()})
                except Exception:
                    pass
                break

            else:
                wlog.warning("未知消息类型: %s", mtype)

    finally:
        if pool is not None:
            try:
                pool.release_batch(batch_ctx)
                pool.shutdown()
            except Exception:
                wlog.exception("关闭实例池失败")
        async_mgr.stop()
        try:
            conn.close()
        except Exception:
            pass
        wlog.info("Worker 退出 pid=%d", os.getpid())


def _build_instance_pool(wire: Dict[str, Any]) -> InstancePool:
    specs: Dict[str, InstanceSpec] = {}
    for name, s in (wire or {}).items():
        specs[name] = InstanceSpec(
            name=name,
            class_path=s["class"],
            args=dict(s.get("args", {})),
            lifetime=s.get("lifetime", "call"),
        )
    return InstancePool(specs)


def _worker_execute_task(task_def: Dict[str, Any],
                         pool: InstancePool,
                         batch_ctx: Dict[str, Any],
                         async_mgr: "_AsyncManager",
                         wlog: logging.Logger) -> Tuple[str, Optional[str]]:
    """执行一个任务（含 retry）。返回 (status, error)。"""
    name = task_def["name"]
    retry = int(task_def.get("retry", 0))
    retry_backoff = task_def.get("retry_backoff")
    condition_spec = task_def.get("condition_spec")
    condition_params = dict(task_def.get("condition_params", {}))
    condition_params_expr = task_def.get("condition_params_expr")
    params = dict(task_def.get("params", {}))
    params_expr = task_def.get("params_expr")
    func_spec = task_def["func_spec"]

    # ---- 1. condition 检查 ----
    if condition_spec:
        try:
            ok = _execute_condition(
                condition_spec, condition_params, condition_params_expr,
                pool, batch_ctx, async_mgr, name, wlog)
        except Exception as e:
            wlog.exception("[%s] condition 异常", name)
            return "skipped", f"condition 异常: {e!r}"
        if not ok:
            wlog.info("[%s] condition 未通过，跳过", name)
            return "skipped", None

    # ---- 2. 解析 func ----
    try:
        func, cleanup = _resolve_callable(func_spec, pool, batch_ctx, name, wlog)
    except Exception as e:
        wlog.exception("[%s] 解析 func 失败", name)
        return "failed", repr(e)

    try:
        # ---- 3. retry 循环 ----
        attempt = 0
        last_error: Optional[str] = None
        while True:
            attempt += 1
            try:
                actual_params = _resolve_actual_params(
                    params, params_expr, pool, batch_ctx, name, wlog)
                wlog.info("[%s] 执行 func=%s params=%s (第 %d/%d 次)",
                          name, func_spec, actual_params, attempt, retry + 1)
                _execute_func(func, actual_params, task_def, async_mgr)
                wlog.info("[%s] 执行成功", name)
                return "ok", None
            except Exception as e:
                last_error = repr(e)
                wlog.exception("[%s] 执行失败", name)
                if attempt > retry:
                    return "failed", last_error
                # 计算 backoff
                wait_s = _compute_backoff(retry_backoff, attempt)
                if wait_s > 0:
                    wlog.info("[%s] 等待 %.1fs 后重试", name, wait_s)
                    time.sleep(wait_s)
                wlog.warning("[%s] 重试 %d/%d", name, attempt, retry)
    finally:
        if cleanup is not None:
            inst, iname = cleanup
            try:
                pool.close_one(iname, inst)
            except Exception:
                wlog.exception("关闭 call 实例 [%s] 失败", iname)


def _compute_backoff(raw: Any, attempt: int) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw) * (2 ** (attempt - 1))
    if isinstance(raw, list):
        idx = min(attempt - 1, len(raw) - 1)
        return float(raw[idx])
    return 0.0


def _resolve_callable(spec: str, pool: InstancePool, batch_ctx: Dict[str, Any],
                      ctx: str, wlog: logging.Logger):
    if ":" not in spec:
        raise ValueError(f"[{ctx}] 非法引用: {spec!r}")
    prefix, name = spec.split(":", 1)
    if pool.has(prefix):
        spec_obj = pool.get_spec(prefix)
        inst = pool.get(prefix, batch_ctx)
        fn = getattr(inst, name, None)
        if fn is None:
            raise AttributeError(f"[{ctx}] 实例 [{prefix}] 没有属性 {name!r}")
        cleanup = (inst, prefix) if spec_obj.lifetime == "call" else None
        return fn, cleanup
    fn = _load_module_attr(spec)
    return fn, None


def _resolve_actual_params(params: Dict[str, Any],
                           params_expr: Optional[str],
                           pool: InstancePool,
                           batch_ctx: Dict[str, Any],
                           ctx: str,
                           wlog: logging.Logger) -> Dict[str, Any]:
    if not params_expr:
        return dict(params)
    path, kwargs = _parse_params_expr(params_expr)
    head, attrs = path[0], path[1:]
    cleanup = None
    try:
        if pool.has(head):
            spec_obj = pool.get_spec(head)
            obj = pool.get(head, batch_ctx)
            if spec_obj.lifetime == "call":
                cleanup = (obj, head)
        else:
            obj = importlib.import_module(head)
        for attr in attrs:
            obj = getattr(obj, attr)
        if not callable(obj):
            raise TypeError(f"[{ctx}] {params_expr!r} 最终目标不可调用")
        result = obj(**kwargs)
        if not isinstance(result, dict):
            raise TypeError(f"[{ctx}] {params_expr!r} 必须返回 dict")
        return result
    finally:
        if cleanup is not None:
            inst, iname = cleanup
            try:
                pool.close_one(iname, inst)
            except Exception:
                pass


def _execute_condition(spec: str,
                       params: Dict[str, Any],
                       params_expr: Optional[str],
                       pool: InstancePool,
                       batch_ctx: Dict[str, Any],
                       async_mgr: "_AsyncManager",
                       ctx: str,
                       wlog: logging.Logger) -> bool:
    fn, cleanup = _resolve_callable(spec, pool, batch_ctx, f"{ctx}.condition", wlog)
    try:
        actual = _resolve_actual_params(params, params_expr, pool, batch_ctx,
                                        f"{ctx}.condition", wlog)
        raw = _execute_func(fn, actual, {"timeout": None, "name": ctx}, async_mgr)
        return bool(raw)
    finally:
        if cleanup is not None:
            inst, iname = cleanup
            try:
                pool.close_one(iname, inst)
            except Exception:
                pass


def _execute_func(func: Callable, params: Dict[str, Any],
                  task_def: Dict[str, Any], async_mgr: "_AsyncManager") -> Any:
    timeout = task_def.get("timeout")
    if inspect.iscoroutinefunction(func):
        loop = async_mgr.ensure_loop()
        coro = func(**params)
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            if timeout:
                return fut.result(timeout=timeout)
            return fut.result()
        except asyncio.TimeoutError:
            fut.cancel()
            raise TimeoutError(f"异步任务超时 {timeout}s")
    return func(**params)


class _AsyncManager:
    """Worker 内部的 asyncio 事件循环管理器。"""
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()

            def runner():
                asyncio.set_event_loop(loop)
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

            self._loop = loop
            self._thread = threading.Thread(target=runner, daemon=True)
            self._thread.start()
            return loop

    def stop(self):
        with self._lock:
            loop = self._loop
            self._loop = None
        if loop and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(timeout=3)


# =========================================================================== #
# 主进程
# =========================================================================== #
class Scheduler:
    def __init__(self, name: str = "scheduler"):
        self.name = name
        self._tasks: Dict[str, Task] = {}
        self._instances_wire: Dict[str, Any] = {}
        self._state_file: Optional[str] = None
        self._config_path: Optional[str] = None
        self._reload_flag = False
        self._stop = False

        # Worker 管理
        self._workers: Dict[int, Dict[str, Any]] = {}   # pid -> {process, conn, current_task}
        self._task_owner: Dict[str, int] = {}           # task_name -> worker pid

        # 主循环唤醒
        self._internal_recv, self._internal_send = Pipe(duplex=False)
        self._input_thread: Optional[threading.Thread] = None

        # state 文件写入锁
        self._state_lock = threading.Lock()

        # 全局状态（内存）
        self._state: Dict[str, Dict[str, Any]] = {}

        self._hook_pool: Optional[ThreadPoolExecutor] = None
        self._notify: Dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def load_config(self, path: str) -> None:
        self._config_path = os.path.abspath(path)
        base, _ = os.path.splitext(self._config_path)
        self._state_file = base + ".state.json"

        self._load_state()
        self._reload_config()
        self._save_state()

    def _load_state(self) -> None:
        if not self._state_file or not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, encoding="utf-8") as f:
                data = json.load(f)
            tasks_state = data.get("tasks", {})
            for name, s in tasks_state.items():
                self._state[name] = {
                    "last_status": s.get("last_status", "pending"),
                    "prev_status": s.get("prev_status", "pending"),
                    "last_run": s.get("last_run"),
                    "last_error": s.get("last_error"),
                    "run_count": int(s.get("run_count", 0)),
                    "fail_count": int(s.get("fail_count", 0)),
                    "running": False,   # 上次异常退出，视为非运行
                }
            log.info("读取状态文件 %s（%d 条记录）",
                     self._state_file, len(self._state))
        except Exception:
            log.exception("状态文件损坏，忽略: %s", self._state_file)

    def _save_state(self) -> None:
        if not self._state_file:
            return
        with self._state_lock:
            snapshot = {
                name: {
                    "last_status": s.get("last_status", "pending"),
                    "prev_status": s.get("prev_status", "pending"),
                    "last_run": s.get("last_run"),
                    "last_error": s.get("last_error"),
                    "run_count": s.get("run_count", 0),
                    "fail_count": s.get("fail_count", 0),
                }
                for name, s in self._state.items()
            }
            payload = {"tasks": snapshot,
                       "saved_at": datetime.now().isoformat()}
            # 随机退避重试
            for i in range(4):
                try:
                    tmp = self._state_file + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, self._state_file)
                    return
                except Exception as e:
                    if i < 3:
                        time.sleep(random.uniform(1.0, 3.0))
                    else:
                        log.exception("保存状态失败（已重试 4 次）")

    # >>> FIX 最新: 新增 save_start_at_to_config —— 回写 next_run 到 config 的 start_at
    #                与单线程版保持一致，save / cancel 命令会调用它
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

    # >>> FIX 最新: 新增 _do_save —— save / cancel 共用的保存动作
    def _do_save(self) -> None:
        """保存 config（start_at 回写）+ state 文件"""
        try:
            n = self.save_start_at_to_config()
            if n > 0:
                print(f"已回写 {n} 个任务的 start_at 到配置文件")
            else:
                print("无需回写 start_at")
        except Exception:
            log.exception("回写 start_at 失败")
        try:
            self._save_state()
            print(f"已保存状态文件: {self._state_file}")
        except Exception:
            log.exception("保存状态失败")

    def _reload_config(self) -> None:
        if not self._config_path:
            return
        try:
            with open(self._config_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            log.exception("读取配置失败: %s", self._config_path)
            return

        self._notify = {k: v for k, v in (cfg.get("notify") or {}).items() if v}

        # instances（原样保留，下发给 worker）
        self._instances_wire = {
            name: {
                "class": spec["class"],
                "args": dict(spec.get("args", {})),
                "lifetime": spec.get("lifetime", "call"),
            }
            for name, spec in cfg.get("instances", {}).items()
        }

        # tasks
        new_specs = {t["name"]: t for t in cfg.get("tasks", [])}

        # 移除已消失的
        for name in list(self._tasks.keys()):
            if name not in new_specs:
                del self._tasks[name]
                self._state.pop(name, None)
                log.info("[%s] 已移除", name)

        # 新增 / 更新
        for name, spec in new_specs.items():
            try:
                if name in self._tasks:
                    self._update_task(self._tasks[name], spec)
                else:
                    self._tasks[name] = self._build_task(spec)
                    t = self._tasks[name]
                    if name not in self._state:
                        self._state[name] = self._blank_state()
                    nr = (t.next_run.strftime("%Y-%m-%d %H:%M:%S")
                          if t.next_run else "无")
                    log.info("[%s] 新增 interval=%s next=%s deps=%s",
                             name, t.interval or "(仅一次)", nr,
                             t.deps_groups or "-")
            except Exception:
                log.exception("[%s] 解析失败", name)

        self._check_dependency_cycles()

    def _blank_state(self) -> Dict[str, Any]:
        return {
            "last_status": "pending",
            "prev_status": "pending",
            "last_run": None,
            "last_error": None,
            "run_count": 0,
            "fail_count": 0,
            "running": False,
        }

    def _build_task(self, spec: Dict[str, Any]) -> Task:
        name = spec["name"]
        func_spec = spec["func"]
        if ":" not in func_spec:
            raise ValueError(f"[{name}] func 必须形如 'module:function'")

        interval = _normalize_interval(spec.get("interval"))
        deps_groups = _parse_deps(spec.get("deps", []))
        start_raw = spec.get("start_at")
        retry = int(spec.get("retry", 0))
        if retry < 0:
            raise ValueError(f"[{name}] retry 必须 >= 0")
        retry_backoff = _parse_backoff(spec.get("retry_backoff"))

        params, params_expr = _parse_params_field(spec.get("params"), name)
        cond_spec, cond_params, cond_params_expr = _parse_condition(
            spec.get("condition"), name)

        if interval == EVENT:
            if not deps_groups:
                log.error("[%s] interval=0 必须有 deps，永不触发", name)
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
            name=name, func_spec=func_spec, interval=interval,
            next_run=next_run, params=params, params_expr=params_expr,
            deps_groups=deps_groups,
            condition_spec=cond_spec,
            condition_params=cond_params,
            condition_params_expr=cond_params_expr,
            validate=int(spec.get("validate", 1)),
            timeout=spec.get("timeout"),
            retry=retry, retry_backoff=retry_backoff,
            start_at_raw=start_raw,
        )

    def _update_task(self, t: Task, spec: Dict[str, Any]) -> None:
        # 简化：直接重建（保留状态）
        new_task = self._build_task(spec)
        t.func_spec = new_task.func_spec
        t.interval = new_task.interval
        t.next_run = new_task.next_run
        t.params = new_task.params
        t.params_expr = new_task.params_expr
        t.deps_groups = new_task.deps_groups
        t.condition_spec = new_task.condition_spec
        t.condition_params = new_task.condition_params
        t.condition_params_expr = new_task.condition_params_expr
        t.validate = new_task.validate
        t.timeout = new_task.timeout
        t.retry = new_task.retry
        t.retry_backoff = new_task.retry_backoff
        t.start_at_raw = new_task.start_at_raw

    def _check_dependency_cycles(self) -> None:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self._tasks}

        def dfs(name, path):
            color[name] = GRAY
            path.append(name)
            for group in self._tasks[name].deps_groups:
                for dep in group:
                    if dep not in self._tasks:
                        continue
                    if color[dep] == GRAY:
                        cycle = path[path.index(dep):] + [dep]
                        log.error("检测到循环依赖: %s", " -> ".join(cycle))
                        for n in cycle[:-1]:
                            self._tasks[n].validate = 0
                    elif color[dep] == WHITE:
                        dfs(dep, path)
            path.pop()
            color[name] = BLACK

        for n in list(self._tasks):
            if color[n] == WHITE:
                dfs(n, [])

    # ------------------------------------------------------------------ #
    # 依赖检查
    # ------------------------------------------------------------------ #
    def _deps_blocked_reason(self, task: Task) -> Optional[str]:
        if not task.deps_groups:
            return None
        reasons: List[str] = []
        for group in task.deps_groups:
            group_ok = True
            why: List[str] = []
            for dep_name in group:
                dep = self._tasks.get(dep_name)
                if dep is None:
                    group_ok = False; why.append(f"{dep_name}(未注册)"); break
                if not dep.validate:
                    group_ok = False; why.append(f"{dep_name}(已禁用)"); break
                st = self._state.get(dep_name, {})
                if st.get("running"):
                    group_ok = False; why.append(f"{dep_name}(运行中)"); break
                if st.get("last_status") != "ok":
                    group_ok = False
                    why.append(f"{dep_name}({st.get('last_status', 'pending')})")
                    break
            if group_ok:
                return None
            reasons.append("(" + "+".join(why) + ")")
        return " or ".join(reasons)

    def _is_running(self, name: str) -> bool:
        st = self._state.get(name, {})
        return bool(st.get("running"))

    # ------------------------------------------------------------------ #
    # Worker 管理
    # ------------------------------------------------------------------ #
    def _spawn_worker(self, task: Task) -> None:
        parent_conn, child_conn = Pipe()
        proc = Process(target=_worker_main, args=(child_conn,), daemon=True)
        proc.start()
        child_conn.close()  # 主进程不再需要 child 端

        self._workers[proc.pid] = {
            "process": proc,
            "conn": parent_conn,
            "current_task": task.name,
        }
        self._task_owner[task.name] = proc.pid
        self._state[task.name]["running"] = True

        # 下发任务
        try:
            parent_conn.send({
                "type": "run_task",
                "task": self._task_to_wire(task),
                "instances": self._instances_wire,
            })
        except Exception:
            log.exception("[%s] 下发任务失败", task.name)

        log.info("[%s] 启动 Worker pid=%d", task.name, proc.pid)

    def _task_to_wire(self, task: Task) -> Dict[str, Any]:
        return {
            "name": task.name,
            "func_spec": task.func_spec,
            "params": dict(task.params),
            "params_expr": task.params_expr,
            "condition_spec": task.condition_spec,
            "condition_params": dict(task.condition_params),
            "condition_params_expr": task.condition_params_expr,
            "timeout": task.timeout,
            "retry": task.retry,
            "retry_backoff": task.retry_backoff,
        }

    def _send_to_worker(self, pid: int, msg: Dict[str, Any]) -> bool:
        w = self._workers.get(pid)
        if not w:
            return False
        try:
            w["conn"].send(msg)
            return True
        except Exception:
            log.exception("向 Worker %d 发送失败", pid)
            return False

    def _remove_worker(self, pid: int) -> None:
        w = self._workers.pop(pid, None)
        if w is None:
            return
        try:
            w["conn"].close()
        except Exception:
            pass
        try:
            w["process"].join(timeout=0.5)
        except Exception:
            pass
        # 释放 owner
        task_name = w.get("current_task")
        if task_name and self._task_owner.get(task_name) == pid:
            self._task_owner.pop(task_name, None)

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        log.info("主进程启动 pid=%d", os.getpid())

        # 启动 input 线程
        self._input_thread = threading.Thread(
            target=self._input_loop, name="input", daemon=True)
        self._input_thread.start()

        try:
            while not self._stop:
                # 1. 扫描到期任务
                self._dispatch_due_tasks()

                # 2. 收集连接
                conns: List[Connection] = [w["conn"] for w in self._workers.values()]
                conns.append(self._internal_recv)

                # 3. 计算 timeout
                timeout = self._next_trigger_in()

                # 4. wait
                try:
                    ready = wait(conns, timeout=timeout)
                except Exception:
                    log.exception("wait 出错")
                    time.sleep(0.5)
                    continue

                # 5. 处理就绪连接
                for conn in ready:
                    if conn is self._internal_recv:
                        try:
                            cmd = conn.recv()
                            self._handle_command(cmd)
                        except EOFError:
                            self._stop = True
                    else:
                        try:
                            msg = conn.recv()
                            self._handle_worker_msg(conn, msg)
                        except EOFError:
                            self._handle_worker_died(conn)
                        except Exception:
                            log.exception("处理 worker 消息失败")
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C")
        finally:
            self._shutdown_all()

        log.info("主进程退出")

    def _input_loop(self) -> None:
        while not self._stop:
            try:
                line = input("scheduler> ").strip()
            except EOFError:
                break
            except Exception:
                break
            try:
                self._internal_send.send({"type": "cmd", "line": line})
            except Exception:
                break

    def _next_trigger_in(self) -> Optional[float]:
        now = datetime.now()
        earliest_future: Optional[datetime] = None
        for t in self._tasks.values():
            if not t.validate or t.next_run is None:
                continue
            if t.next_run <= now:
                # 有任务已到期，等 worker 完成/输入命令唤醒
                return None
            if earliest_future is None or t.next_run < earliest_future:
                earliest_future = t.next_run
        if earliest_future is None:
            return None
        return max(0.0, (earliest_future - now).total_seconds())

    def _dispatch_due_tasks(self) -> None:
        """扫描时间任务，deps 满足则启动 Worker"""
        now = datetime.now()
        for name, t in list(self._tasks.items()):
            if not t.validate or t.pending_remove:
                continue
            if t.interval == EVENT or t.next_run is None:
                continue
            if t.next_run > now:
                continue
            if self._is_running(name):
                # 已在跑，推进 next_run
                t.next_run = advance_to_after(t.next_run, t.interval, now)
                continue
            if self._deps_blocked_reason(t) is not None:
                # deps 未满足，不推进，等上游完成唤醒
                continue
            # 触发
            self._spawn_worker(t)
            t.next_run = advance_to_after(t.next_run, t.interval, now)

    # ------------------------------------------------------------------ #
    # 消息处理
    # ------------------------------------------------------------------ #
    def _handle_worker_msg(self, conn: Connection, msg: Dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "task_done":
            pid = msg.get("pid")
            task_name = msg.get("task_name")
            status = msg.get("status")
            error = msg.get("error")
            self._handle_task_done(pid, task_name, status, error)
        elif mtype == "bye":
            pid = msg.get("pid")
            log.info("Worker %d 已退出", pid)
            self._remove_worker(pid)
        else:
            log.warning("未知消息: %s", mtype)

    def _handle_task_done(self, pid: Optional[int], task_name: str,
                          status: str, error: Optional[str]) -> None:
        if task_name not in self._tasks:
            log.warning("[%s] 已完成但任务已移除，忽略", task_name)
            if pid is not None:
                self._remove_worker(pid)
            return

        t = self._tasks[task_name]
        st = self._state.setdefault(task_name, self._blank_state())

        prev = st.get("last_status", "pending")
        st["last_status"] = status
        st["prev_status"] = prev
        st["last_run"] = datetime.now().isoformat()
        st["last_error"] = error
        st["running"] = False

        if status == "ok":
            st["run_count"] = int(st.get("run_count", 0)) + 1
        elif status == "failed":
            st["fail_count"] = int(st.get("fail_count", 0)) + 1

        log.info("[%s] 完成 status=%s", task_name, status)

        # 找 ready 下游（按配置顺序）
        ready: List[Task] = []
        for downstream in self._tasks.values():
            if downstream.name == task_name:
                continue
            if not downstream.validate or downstream.pending_remove:
                continue
            if self._is_running(downstream.name):
                continue
            is_down = any(task_name in group
                          for group in downstream.deps_groups)
            if not is_down:
                continue
            if self._deps_blocked_reason(downstream) is not None:
                continue
            # 事件任务：立即触发；时间任务：交给 _dispatch_due_tasks
            if downstream.is_event:
                ready.append(downstream)

        # 分配：第一个给原 worker，其余开新 worker
        if pid is not None and pid in self._workers:
            if ready:
                first = ready[0]
                # 原 worker 继续
                w = self._workers[pid]
                w["current_task"] = first.name
                self._task_owner[first.name] = pid
                self._state.setdefault(first.name, self._blank_state())
                self._state[first.name]["running"] = True
                ok = self._send_to_worker(pid, {
                    "type": "run_task",
                    "task": self._task_to_wire(first),
                    "instances": self._instances_wire,
                })
                if not ok:
                    # 发送失败，把任务取消标记
                    self._state[first.name]["running"] = False
                    self._remove_worker(pid)
                log.info("[%s] 上游 [%s] 完成，原 Worker 继续",
                         first.name, task_name)
                # 其余开新 worker
                for extra in ready[1:]:
                    self._spawn_worker(extra)
            else:
                # 无 ready 下游，shutdown 原 worker
                self._send_to_worker(pid, {"type": "shutdown"})
        else:
            # 原 worker 不在了（异常退出），所有 ready 都开新 worker
            for r in ready:
                self._spawn_worker(r)

        # >>> FIX 最新: 每个任务完成即保存 state（"前面的一个执行链条执行完毕，也写文件"）
        #                不改动，保留原有行为
        self._save_state()

        # 通知钩子（简单处理：主进程直接调用）
        self._fire_hook(status, task_name, t, error)

    def _handle_worker_died(self, conn: Connection) -> None:
        # 找到对应的 pid
        pid = None
        for p, w in self._workers.items():
            if w["conn"] is conn:
                pid = p
                break
        if pid is None:
            return
        w = self._workers.get(pid)
        if not w:
            return
        task_name = w.get("current_task")
        log.warning("Worker %d 异常退出（任务: %s）", pid, task_name)
        if task_name and task_name in self._tasks:
            st = self._state.setdefault(task_name, self._blank_state())
            st["last_status"] = "failed"
            st["last_error"] = "Worker 进程异常退出"
            st["running"] = False
            st["fail_count"] = int(st.get("fail_count", 0)) + 1
        self._remove_worker(pid)
        self._save_state()

    def _fire_hook(self, status: str, task_name: str,
                   task: Task, error: Optional[str]) -> None:
        """简单地把 hook 送给线程池执行。"""
        if not self._notify:
            return
        event = None
        if status == "ok":
            st = self._state.get(task_name, {})
            if st.get("prev_status") == "failed":
                event = "on_recover"
            else:
                event = "on_success"
        elif status == "failed":
            event = "on_fail"
        elif status == "skipped":
            event = "on_skip"
        if not event:
            return
        spec = self._notify.get(event)
        if not spec:
            return
        if self._hook_pool is None:
            self._hook_pool = ThreadPoolExecutor(max_workers=4,
                                                 thread_name_prefix="hook")

        def _run():
            try:
                fn = _load_module_attr(spec)
                kwargs = {
                    "task_name": task_name, "status": status,
                    "error": error, "when": datetime.now().isoformat(),
                    "run_count": self._state.get(task_name, {}).get("run_count", 0),
                    "fail_count": self._state.get(task_name, {}).get("fail_count", 0),
                }
                sig = inspect.signature(fn)
                accepts_kwargs = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values())
                accepted = kwargs if accepts_kwargs else {
                    k: v for k, v in kwargs.items() if k in sig.parameters}
                fn(**accepted)
            except Exception:
                log.exception("[hook:%s] 执行失败", event)

        try:
            self._hook_pool.submit(_run)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------ #
    # 交互命令
    # ------------------------------------------------------------------ #
    def _handle_command(self, cmd: Dict[str, Any]) -> None:
        line = cmd.get("line", "")
        if line == "":
            return
        if line == "update":
            self._reload_flag = True
            self._reload_config()
            print("配置已重载")
        # >>> FIX 最新: 新增 save 命令 —— 保存 config + state，不退出
        elif line == "save":
            print("正在保存...")
            self._do_save()
        elif line == "cancel":
            # >>> FIX 最新: cancel 也保存 config + state
            print("正在取消所有任务和调度计划...")
            self._do_save()
            print("正在退出...")
            self._stop = True
        elif line == "status":
            self._print_status()
        elif line == "tasks":
            self._print_tasks()
        elif line == "help":
            print(HELP_TEXT)
        elif line.startswith("run "):
            name = line[4:].strip()
            self._cmd_run(name)
        elif line.startswith("pause "):
            name = line[6:].strip()
            self._cmd_pause(name)
        elif line.startswith("resume "):
            name = line[7:].strip()
            self._cmd_resume(name)
        elif line == "pause":
            self._cmd_pause(None)
        else:
            print("无效命令。输入 help 查看帮助")

    def _cmd_run(self, name: str) -> None:
        if name not in self._tasks:
            print(f"任务不存在: {name}")
            return
        if self._is_running(name):
            print(f"任务已在运行: {name}")
            return
        t = self._tasks[name]
        self._spawn_worker(t)
        print(f"已手动触发 {name}")

    def _cmd_pause(self, name: Optional[str]) -> None:
        if name is None:
            for t in self._tasks.values():
                t.validate = 0
            print("已暂停所有任务")
        else:
            if name not in self._tasks:
                print(f"任务不存在: {name}")
                return
            self._tasks[name].validate = 0
            print(f"已暂停 {name}")

    def _cmd_resume(self, name: str) -> None:
        if name not in self._tasks:
            print(f"任务不存在: {name}")
            return
        self._tasks[name].validate = 1
        print(f"已恢复 {name}")

    def _print_status(self) -> None:
        now = datetime.now()
        print("-" * 150)
        print(f"{'任务':<16}{'有效':<5}{'状态':<12}{'间隔':<10}"
              f"{'下次运行':<21}{'剩余(s)':<10}{'成功':<6}{'失败':<6}"
              f"{'依赖':<24}{'参数'}")
        print("-" * 150)
        for name, t in self._tasks.items():
            st = self._state.get(name, {})
            deps = " or ".join("(" + "+".join(g) + ")" for g in t.deps_groups) or "-"
            if len(deps) > 22:
                deps = deps[:19] + "..."
            nr = t.next_run.strftime("%Y-%m-%d %H:%M:%S") if t.next_run else "无"
            in_s = round((t.next_run - now).total_seconds(), 1) if t.next_run else None
            interval = t.interval or ("事件" if t.is_event else "仅一次")
            print(f"{name:<16}{t.validate:<5}{st.get('last_status', 'pending'):<12}"
                  f"{interval:<10}{nr:<21}{str(in_s):<10}"
                  f"{st.get('run_count', 0):<6}{st.get('fail_count', 0):<6}"
                  f"{deps:<24}{t.params}")
        print("-" * 150)

    def _print_tasks(self) -> None:
        for name, t in self._tasks.items():
            st = self._state.get(name, {})
            print(f"  {name:<20} {st.get('last_status', 'pending'):<10} "
                  f"next={t.next_run}")

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _shutdown_all(self) -> None:
        log.info("开始关闭所有 Worker...")
        for pid in list(self._workers.keys()):
            self._send_to_worker(pid, {"type": "shutdown"})
        # 等待 1 秒
        deadline = time.time() + 1.0
        for pid in list(self._workers.keys()):
            w = self._workers.get(pid)
            if not w:
                continue
            remaining = max(0.1, deadline - time.time())
            try:
                w["process"].join(timeout=remaining)
            except Exception:
                pass
        # 强杀剩余的
        for pid, w in list(self._workers.items()):
            if w["process"].is_alive():
                try:
                    w["process"].terminate()
                except Exception:
                    pass
            try:
                w["conn"].close()
            except Exception:
                pass
        self._workers.clear()
        self._task_owner.clear()

        # >>> FIX 最新: 不再自动保存状态；只有 save / cancel 命令或任务完成时保存
        #                这样 Ctrl+C / 异常退出不会写文件
        # try:
        #     self._save_state()
        # except Exception:
        #     log.exception("退出时保存状态失败")

        if self._hook_pool is not None:
            try:
                self._hook_pool.shutdown(wait=False)
            except Exception:
                pass
        log.info("所有 Worker 已关闭")


# =========================================================================== #
# 入口
# =========================================================================== #
# >>> FIX 最新: HELP_TEXT 增加 save 命令；cancel 说明改为"保存并退出"
HELP_TEXT = """
可用命令:
  status              显示所有任务状态
  tasks               列出任务名与状态
  run <task>          立即触发任务（只触发一次，不影响时间调度）
  pause [<task>]      暂停（无任务名 = 全部）
  resume <task>       恢复任务
  update              重新加载配置
  save                保存配置文件 + 状态文件（不退出）
  cancel              保存配置文件 + 状态文件，然后退出
  help                显示帮助
""".strip()


def sch_main():
    global _GLOBAL_SCHEDULER

    if len(sys.argv) < 2:
        print("用法: python scheduler.py <配置文件> [任务名]")
        sys.exit(1)

    cfg_path = sys.argv[1]
    start_task = sys.argv[2] if len(sys.argv) > 2 else None

    base = os.path.dirname(os.path.abspath(cfg_path))
    setup_logging(log_file=os.path.join(base, "scheduler.log"))

    sch = Scheduler()
    _GLOBAL_SCHEDULER = sch

    try:
        sch.load_config(cfg_path)
    except Exception:
        log.exception("加载配置失败")
        sys.exit(1)

    # 校验起始任务
    if start_task and start_task not in sch._tasks:
        print(f"任务不存在: {start_task}")
        sys.exit(1)

    print()
    print("=" * 70)
    print(f"调度器已启动 | 配置: {cfg_path}")
    if start_task:
        print(f"入口任务: {start_task}（立即触发）")
    else:
        print("入口任务: 无（按时间调度所有任务）")
    print(f"状态文件: {sch._state_file}")
    print("-" * 70)
    print(HELP_TEXT)
    print("=" * 70)
    print()

    # 立即触发入口任务
    if start_task:
        t = sch._tasks[start_task]
        if t.interval == EVENT:
            log.warning("[%s] 是事件任务，手动触发一次", start_task)
        sch._spawn_worker(t)

    # 进入主循环
    try:
        sch.run()
    except KeyboardInterrupt:
        pass
    finally:
        print("调度器已退出")


if __name__ == "__main__":
    # Windows spawn 需要保护
    from multiprocessing import freeze_support
    freeze_support()
    sch_main()