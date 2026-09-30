"""探测关键接口的签名 + 实际调用效果。"""
import inspect
import akshare as ak


def show(name, fn):
    print(f"\n{'='*60}")
    print(f"ak.{name}")
    print(f"{'='*60}")
    try:
        sig = inspect.signature(fn)
        print(f"签名: {name}{sig}")
    except Exception as e:
        print(f"签名 ✗: {e}")


def try_call(label, fn, **kwargs):
    print(f"\n  ▶ 调用 {label} ({kwargs})")
    try:
        df = fn(**kwargs)
        if df is None:
            print("    ✗ 返回 None")
            return
        if hasattr(df, "empty") and df.empty:
            print("    ✗ 空 DataFrame")
            return
        if hasattr(df, "shape"):
            print(f"    ✓ 形状={df.shape}")
            print(f"    列: {list(df.columns)[:15]}")
            print(f"    前 2 行:\n{df.head(2).to_string()}")
        else:
            print(f"    ✓ 类型={type(df)}")
            print(f"    内容: {str(df)[:500]}")
    except Exception as e:
        print(f"    ✗ {type(e).__name__}: {e}")


# ============================================================
# 1. 公告接口
# ============================================================
show("stock_individual_notice_report",
     ak.stock_individual_notice_report)
try_call("公告-东财个股",
         ak.stock_individual_notice_report, symbol="688508")

show("stock_zh_a_disclosure_report_cninfo",
     ak.stock_zh_a_disclosure_report_cninfo)

# 巨潮通常需要 symbol="688508" 或 "sh688508"，两种都试
try_call("公告-巨潮(无后缀)",
         ak.stock_zh_a_disclosure_report_cninfo,
         symbol="688508", market="科创板")
try_call("公告-巨潮(带后缀)",
         ak.stock_zh_a_disclosure_report_cninfo,
         symbol="sh688508")


# ============================================================
# 2. 基本信息接口
# ============================================================
show("stock_individual_basic_info_xq",
     ak.stock_individual_basic_info_xq)
try_call("基本信息-雪球",
         ak.stock_individual_basic_info_xq, symbol="SH688508")

show("stock_profile_cninfo", ak.stock_profile_cninfo)
try_call("基本信息-巨潮",
         ak.stock_profile_cninfo, symbol="688508")

show("stock_zh_a_spot", ak.stock_zh_a_spot)
try_call("实时行情-新浪", ak.stock_zh_a_spot)


# ============================================================
# 3. 巨潮股东/高管接口
# ============================================================
show("stock_hold_num_cninfo", ak.stock_hold_num_cninfo)
try_call("股东户数-巨潮",
         ak.stock_hold_num_cninfo, symbol="688508")

show("stock_hold_management_detail_cninfo",
     ak.stock_hold_management_detail_cninfo)

show("stock_dividend_cninfo", ak.stock_dividend_cninfo)
try_call("分红-巨潮",
         ak.stock_dividend_cninfo, symbol="688508")


print("\n" + "=" * 60)
print("探测完成")
print("=" * 60)