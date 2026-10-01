"""跨应用共享状态。

用于 /scan、/analysis 与 /workbench 之间传递数据。
按用户名隔离，多用户不冲突。
"""

# {username: {key: value}}
_pending: dict[str, dict] = {}


def set_pending(username: str, key: str, value) -> None:
    """写入待处理数据。"""
    _pending.setdefault(username, {})[key] = value


def pop_pending(username: str, key: str):
    """读取并清空。"""
    user_data = _pending.get(username)
    if not user_data:
        return None
    return user_data.pop(key, None)


def get_pending(username: str, key: str):
    """读取（不清空）。"""
    return _pending.get(username, {}).get(key)


def has_pending(username: str, key: str) -> bool:
    return key in _pending.get(username, {})


# ---- 向后兼容旧 API ----
def set_pending_codes(username: str, codes: str) -> None:
    set_pending(username, "codes", codes)


def get_pending_codes(username: str) -> str:
    return get_pending(username, "codes") or ""


def pop_pending_codes(username: str) -> str:
    return pop_pending(username, "codes") or ""