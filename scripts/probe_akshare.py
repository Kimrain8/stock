import akshare as ak
"""探测 akshare 可用接口。"""
print(f"akshare 版本: {ak.__version__}\n")

# ---- 1. 找所有含 notice / disclosure / announcement 的接口 ----
print("=" * 60)
print("公告相关接口")
print("=" * 60)
for name in dir(ak):
    low = name.lower()
    if any(kw in low for kw in ("notice", "disclosure", "announce")):
        print(f"  ak.{name}")

# ---- 2. 找所有含 info / basic / individual 的接口 ----
print("\n" + "=" * 60)
print("基本信息相关接口")
print("=" * 60)
for name in dir(ak):
    low = name.lower()
    if any(kw in low for kw in ("individual_info", "basic_info", "profile")):
        print(f"  ak.{name}")

# ---- 3. 找所有含 stock_zh_a_spot 的接口（实时行情兜底）----
print("\n" + "=" * 60)
print("A 股实时行情相关接口")
print("=" * 60)
for name in dir(ak):
    if "stock_zh_a_spot" in name:
        print(f"  ak.{name}")

# ---- 4. 测试公告接口签名 ----
print("\n" + "=" * 60)
print("测试 stock_notice_report 签名")
print("=" * 60)
try:
    import inspect
    sig = inspect.signature(ak.stock_notice_report)
    print(f"  stock_notice_report{sig}")
except Exception as e:
    print(f"  ✗ 失败: {e}")

# ---- 5. 找所有含 cninfo / 巨潮的接口 ----
print("\n" + "=" * 60)
print("巨潮资讯相关接口")
print("=" * 60)
for name in dir(ak):
    low = name.lower()
    if "cninfo" in low:
        print(f"  ak.{name}")