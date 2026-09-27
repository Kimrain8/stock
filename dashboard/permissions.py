"""基于数据库的权限引擎。

优先级规则：
    1. 权限必须有效
    2. 如果是admin角色，那么直接拥有所有权限。不再检查权限表
    3. 个人角色(user) 规则无条件优先于部门(dept) 规则
    4. 同一类型内，priority 数值越小越优先
    5. priority 相同时，deny 优先（安全保守）
    6. 无规则 → 默认策略（无权限）
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from utils.dbManager import MySQLHelper
from .config import settings

logger = logging.getLogger("stock2.perms")


# ============================================================
# 数据库适配
# ============================================================
def _db() -> MySQLHelper:
    return MySQLHelper(db=settings.DB_NAME)


def _esc(v) -> str:
    """SQL 字面量转义（MySQLHelper 不支持参数化，只能手动）"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


# ============================================================
# 单条权限规则
# ============================================================
@dataclass
class PermissionRule:
    id: int
    subject_type: str     # user / dept
    subject: str          # 用户名或部门
    resource: str         # 原始正则
    resource_type: str    # api / ws / web / all
    action: str           # allow / deny
    priority: int
    pattern: re.Pattern   # 编译好的正则

    def matches(self, path: str, resource_type: str) -> bool:
        # resource_type 过滤
        if self.resource_type not in ("all", "", None, resource_type):
            return False
        # 正则匹配（re.match 从头匹配，不要求结尾）
        return bool(self.pattern.match(path))


# ============================================================
# 缓存
# ============================================================
class _PermissionCache:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self._store: dict[str, tuple[list, float]] = {}

    def get(self, key: str) -> Optional[list]:
        entry = self._store.get(key)
        if not entry:
            return None
        rules, ts = entry
        if time.time() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return rules

    def put(self, key: str, rules: list) -> None:
        self._store[key] = (rules, time.time())

    def invalidate(self, key: Optional[str] = None) -> None:
        if key:
            self._store.pop(key, None)
        else:
            self._store.clear()


# ============================================================
# 权限引擎
# ============================================================
class PermissionEngine:
    def __init__(self, default_allow: bool = False, cache_ttl: int = 60):
        self.default_allow = default_allow
        self.cache = _PermissionCache(ttl=cache_ttl)

    # ---------- 从数据库读取 ----------
    def _load_rules(self, subject_type: str, subject: str) -> list[PermissionRule]:
        key = f"{subject_type}:{subject}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        sql = (
            "SELECT id, subject_type, subject, resource, resource_type, "
            "action, priority "
            "FROM permissions "
            f"WHERE subject_type = {_esc(subject_type)} "
            f"AND subject = {_esc(subject)} "
            "AND is_active = 1 "
            "ORDER BY priority ASC, (action = 'deny') DESC"
        )
        try:
            df = _db().qry2df(sql=sql)
        except Exception as e:
            logger.error("load permissions failed | %s | err=%s", key, e)
            return []

        rules: list[PermissionRule] = []
        if df is None or df.empty:
            self.cache.put(key, rules)
            return rules

        for _, row in df.iterrows():
            try:
                pattern = re.compile(str(row["resource"]))
            except re.error as e:
                logger.warning("invalid regex | id=%s | pattern=%s | err=%s",
                               row["id"], row["resource"], e)
                continue
            rules.append(PermissionRule(
                id=int(row["id"]),
                subject_type=str(row["subject_type"]),
                subject=str(row["subject"]),
                resource=str(row["resource"]),
                resource_type=str(row["resource_type"] or "all"),
                action=str(row["action"]),
                priority=int(row["priority"]),
                pattern=pattern,
            ))
        self.cache.put(key, rules)
        logger.debug("loaded rules | key=%s | count=%d", key, len(rules))
        return rules

    # ---------- 匹配 ----------
    def _pick(self, rules: list[PermissionRule],
              path: str, resource_type: str) -> Optional[PermissionRule]:
        """返回第一条匹配的规则（rules 已按 priority/deny 排序）。"""
        for r in rules:
            if r.matches(path, resource_type):
                return r
        return None

    # ---------- 对外主入口 ----------
    def check(self, username: str, role: Optional[str], dept: Optional[str],
              path: str, resource_type: str) -> tuple[bool, str]:
        """返回 (是否允许, 判定原因)。

        reason 取值：
            admin                                  管理员短路
            user:<subject> p=<priority> id=<id>     个人规则
            dept:<subject> p=<priority> id=<id>     部门规则
            default                                 默认策略
        """
        # 0. admin 直接放行
        if (role or "").lower() == "admin":
            logger.debug("perm check | user=%s | role=admin | path=%s | type=%s | ALLOW | admin",
                         username, path, resource_type)
            return True, "admin"

        # 1. 个人规则
        if username:
            matched = self._pick(
                self._load_rules("user", username), path, resource_type,
            )
            if matched:
                allowed = matched.action == "allow"
                reason = f"user:{matched.subject} p={matched.priority} id={matched.id}"
                logger.debug("perm check | user=%s | path=%s | type=%s | %s | %s",
                             username, path, resource_type,
                             "ALLOW" if allowed else "DENY", reason)
                return allowed, reason

        # 2. 部门规则
        if dept:
            matched = self._pick(
                self._load_rules("dept", dept), path, resource_type,
            )
            if matched:
                allowed = matched.action == "allow"
                reason = f"dept:{matched.subject} p={matched.priority} id={matched.id}"
                logger.debug("perm check | user=%s | dept=%s | path=%s | type=%s | %s | %s",
                             username, dept, path, resource_type,
                             "ALLOW" if allowed else "DENY", reason)
                return allowed, reason

        # 3. 默认
        logger.debug("perm check | user=%s | path=%s | type=%s | DEFAULT | %s",
                     username, path, resource_type,
                     "ALLOW" if self.default_allow else "DENY")
        return self.default_allow, "default"

    # ---------- 缓存管理 ----------
    def invalidate(self, subject_type: Optional[str] = None,
                   subject: Optional[str] = None) -> None:
        if subject_type and subject:
            self.cache.invalidate(f"{subject_type}:{subject}")
        else:
            self.cache.invalidate()


# ============================================================
# 模块级实例
# ============================================================
engine = PermissionEngine(
    default_allow=settings.DEFAULT_PERMISSION_ALLOW,
    cache_ttl=settings.PERMISSION_CACHE_TTL,
)


# ============================================================
# 资源类型判断
# ============================================================
def detect_resource_type(scope_type: str, path: str) -> str:
    """根据 ASGI scope 类型和路径判断资源类型。"""
    if scope_type == "websocket":
        return "ws"
    # REST 路径
    if path.startswith(settings.REST_API_PREFIX):
        if path == settings.WS_API_PATH or path.startswith(settings.WS_API_PATH + "/"):
            return "ws"
        return "api"
    # 其他均为 web 页面
    return "web"