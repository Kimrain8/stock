# 临时脚本：scripts/probe_new_apis.py
import akshare as ak
import inspect

for name in ["stock_restricted_release_queue_em",
             "stock_zh_a_gdhs_detail_em",
             "stock_gpzy_pledge_ratio_detail_em"]:
    fn = getattr(ak, name, None)
    if fn is None:
        print(f"❌ {name} 不存在")
        continue
    print(f"✅ {name}{inspect.signature(fn)}")
    try:
        df = fn(symbol="688508")
        print(f"   返回 {df.shape if hasattr(df, 'shape') else '?'}")
    except Exception as e:
        print(f"   调用失败：{type(e).__name__}: {e}")