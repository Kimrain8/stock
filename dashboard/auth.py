"""认证与会话管理 — 基于 MySQLHelper，多用户支持"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from utils.dbManager import MySQLHelper
from .config import settings

logger = logging.getLogger("stock2.auth")


# ============================================================
# 数据库适配层
# ============================================================
def _db() -> MySQLHelper:
    """获取 jhd2 数据库的连接池实例（MySQLHelper 内部缓存）。"""
    return MySQLHelper(db=settings.DB_NAME)


def _esc(value) -> str:
    """把 Python 值转义为 SQL 字面量。

    注意：MySQLHelper 的 qry2df / delete 接口不支持参数绑定，
    必须手动拼字符串。本函数处理引号和反斜杠，足以防御基本的注入。
    仅用于本项目内部信任的输入（token、username 均来自数据库）。
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


# ============================================================
# 用户表访问
# ============================================================
class UserRepository:
    """读取 user 表。你的表字段：ID, username, password, tel, role, is_active, email"""

    def get_by_username(self, username: str) -> Optional[dict]:
        try:
            sql = (
                "SELECT ID, username, password, tel, role, dept, is_active, email "
                f"FROM user WHERE username = {_esc(username)} LIMIT 1"
            )
            df = _db().qry2df(sql=sql)
            if df is None or df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.error("db query user failed | user=%s | err=%s", username, e)
            return None

    def verify_password(self, user: dict, password: str) -> bool:
        """支持明文、sha256、md5 三种存储方式。"""
        stored = str(user.get("password") or "").strip()
        if not stored or not password:
            return False
        # 明文
        if stored == password:
            return True
        # sha256 十六进制
        if stored.lower() == hashlib.sha256(password.encode()).hexdigest():
            return True
        # md5 十六进制
        if stored.lower() == hashlib.md5(password.encode()).hexdigest():
            return True
        return False


# ============================================================
# 会话存储（MySQL 持久化）
# ============================================================
class SessionStore:
    TABLE = "user_sessions"

    # -------- 写入 --------
    def create(self, username: str, role: str = "user", dept: str = "",
           ip: str = "", user_agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=settings.SESSION_TTL_HOURS)
        data = {
            "token": token,
            "username": username,
            "role": role or "user",
            "dept": (dept or "")[:20],          # 与 user.dept 长度一致
            "ip": (ip or "")[:64],
            "user_agent": (user_agent or "")[:255],
            "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        _db().insert_single(self.TABLE, data)
        logger.info("session created | user=%s | role=%s | dept=%s",
                    username, role, dept)
        return token

    # -------- 校验 --------
    def verify(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        try:
            sql = (
                "SELECT username, role, dept, expires_at "
                f"FROM {self.TABLE} WHERE token = {_esc(token)} LIMIT 1"
            )
            df = _db().qry2df(sql=sql)
            if df is None or df.empty:
                return None
            row = df.iloc[0]
            if pd.Timestamp(row["expires_at"]) < pd.Timestamp.now():
                self.destroy(token)
                logger.info("session expired | user=%s", row["username"])
                return None
            return {
                "username": row["username"],
                "role": row["role"],
                "dept": row.get("dept") or "",
            }
        except Exception as e:
            logger.error("session verify failed | err=%s", e)
            return None

    # -------- 删除 --------
    def destroy(self, token: Optional[str]) -> None:
        if not token:
            return
        try:
            _db().delete(self.TABLE, f"token = {_esc(token)}")
            logger.info("session destroyed | token=%s...", token[:8])
        except Exception as e:
            logger.error("session destroy failed | err=%s", e)

    def destroy_user_all(self, username: str) -> None:
        """踢出某用户的所有会话（修改密码/强制下线场景）。"""
        try:
            _db().delete(self.TABLE, f"username = {_esc(username)}")
            logger.info("all sessions destroyed | user=%s", username)
        except Exception as e:
            logger.error("destroy user sessions failed | err=%s", e)

    # -------- 清理 --------
    def purge_expired(self) -> None:
        try:
            _db().delete(self.TABLE, "expires_at < NOW()")
            logger.info("purged expired sessions")
        except Exception as e:
            logger.error("purge failed | err=%s", e)

    # -------- 查询 --------
    def list_active(self, username: Optional[str] = None) -> pd.DataFrame:
        sql = (
            "SELECT token, username, role, ip, user_agent, created_at, expires_at "
            f"FROM {self.TABLE} WHERE expires_at > NOW()"
        )
        if username:
            sql += f" AND username = {_esc(username)}"
        sql += " ORDER BY created_at DESC"
        try:
            return _db().qry2df(sql=sql)
        except Exception as e:
            logger.error("list sessions failed | err=%s", e)
            return pd.DataFrame()


# ============================================================
# 模块级实例
# ============================================================
users = UserRepository()
sessions = SessionStore()


# ============================================================
# 登录校验
# ============================================================
def authenticate(username: str, password: str) -> Optional[dict]:
    user = users.get_by_username(username)
    if not user:
        logger.warning("login failed | user=%s | reason=not_found", username)
        return None
    if not user.get("is_active", 1):
        logger.warning("login failed | user=%s | reason=inactive", username)
        return None
    if not users.verify_password(user, password):
        logger.warning("login failed | user=%s | reason=bad_password", username)
        return None
    logger.info("login success | user=%s | role=%s | dept=%s",
                username, user.get("role"), user.get("dept"))
    return {
        "id": int(user.get("ID")) if user.get("ID") is not None else None,
        "username": user["username"],
        "role": user.get("role") or "user",
        "dept": user.get("dept"),
        "email": user.get("email"),
        "tel": user.get("tel"),
    }