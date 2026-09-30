"""探测三个接口的真实列名，并寻找质押接口的正确用法。"""
import akshare as ak
import inspect


def show_df(name, df, rows=2):
    print(f"\n{'='*60}\n{name}\n{'='*60}")
    if df is None or df.empty:
        print("  (空)")
        return
    print(f"  形状: {df.shape}")
    print(f"  列名: {list(df.columns)}")
    print(f"  前 {rows} 行:")
    print(df.head(rows).to_string())


# ---- R-G06 解禁 ----
print("\n【R-G06 解禁 - stock_restricted_release_queue_em】")
try:
    df = ak.stock_restricted_release_queue_em(symbol="688508")
    show_df("stock_restricted_release_queue_em(symbol=688508)", df, rows=3)
except Exception as e:
    print(f"  失败: {e}")


# ---- R-G07 股东户数 ----
print("\n【R-G07 股东户数 - stock_zh_a_gdhs_detail_em】")
try:
    df = ak.stock_zh_a_gdhs_detail_em(symbol="688508")
    show_df("stock_zh_a_gdhs_detail_em(symbol=688508)", df, rows=5)
except Exception as e:
    print(f"  失败: {e}")


# ---- R-G08 质押：探测所有可能的接口 ----
print("\n【R-G08 质押 - 探测所有可能接口】")

pledge_candidates = [
    "stock_gpzy_pledge_ratio_em",
    "stock_gpzy_pledge_ratio_detail_em",
    "stock_gpzy_individual_pledge_ratio_detail_em",
    "stock_gpzy_profile_em",
    "stock_gpzy_industry_data_em",
]

for name in pledge_candidates:
    fn = getattr(ak, name, None)
    if fn is None:
        print(f"\n❌ {name}: 不存在")
        continue
    print(f"\n✅ {name}{inspect.signature(fn)}")
    # 根据签名尝试调用
    sig = inspect.signature(fn)
    kwargs = {}
    if "symbol" in sig.parameters:
        kwargs["symbol"] = "688508"
    if "date" in sig.parameters:
        kwargs["date"] = "20240930"
    if "stock" in sig.parameters:
        kwargs["stock"] = "688508"
    try:
        df = fn(**kwargs)
        if df is not None and not df.empty:
            print(f"   ✅ 调用成功 | 形状={df.shape}")
            print(f"   列名: {list(df.columns)[:10]}")
            print(f"   前 2 行:\n{df.head(2).to_string()}")
        else:
            print(f"   ⚠️ 返回空 | kwargs={kwargs}")
    except Exception as e:
        print(f"   ❌ 调用失败: {type(e).__name__}: {e}")