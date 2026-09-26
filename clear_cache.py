"""清空权限缓存（管理员运维脚本）"""
from dashboard.permissions import engine

# 清空所有缓存
engine.invalidate()
print("✅ 所有权限缓存已清空")

# 或者只清某个用户：
# engine.invalidate("user", "jennita")
# print("✅ jennita 的缓存已清空")