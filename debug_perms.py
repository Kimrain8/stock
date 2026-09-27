"""权限调试脚本 — 无需启动服务器,直接模拟判定"""
import logging
from dashboard.permissions import engine
from dashboard.auth import sessions

logging.basicConfig(level=logging.DEBUG)

# ---- 场景 1:测试某个用户对某路径的权限 ----
def test_permission(username, role, dept, path, resource_type):
    print(f"\n{'='*60}")
    print(f"user={username} role={role} dept={dept}")
    print(f"path={path} type={resource_type}")
    print('-'*60)
    allowed, reason = engine.check(username, role, dept, path, resource_type)
    print(f"结果:{'✅ ALLOW' if allowed else '❌ DENY'}")
    print(f"原因:{reason}")
    return allowed

# 测试 jennita
test_permission("jennita", "user", "research", "/api/ws", "ws")
test_permission("jennita", "user", "research", "/api/docs", "api")
test_permission("jennita", "user", "research", "/api/data/001", "api")
test_permission("jennita", "user", "research", "/explorer", "web")
test_permission("kim", "admin", None, "/api/docs", "api")
test_permission("kim", "admin", None, "/anything/xxx", "api")

# ---- 场景 2:查看某用户加载到的所有规则 ----
print(f"\n{'='*60}")
print("jennita 的个人规则:")
for r in engine._load_rules("user", "jennita"):
    print(f"  p={r.priority} action={r.action:5s} type={r.resource_type:4s} "
          f"resource={r.resource}")

print(f"\nresearch 部门规则:")
for r in engine._load_rules("dept", "research"):
    print(f"  p={r.priority} action={r.action:5s} type={r.resource_type:4s} "
          f"resource={r.resource}")