# import akquant as aq
# from akquant import Strategy
import numpy as np
import pandas as pd
import akshare as ak


# 1. 准备数据
# 使用 akshare 获取 A 股历史数据 (需安装: pip install akshare)
# df = ak.stock_zh_a_daily(symbol="sh600000", start_date="20250212", end_date="20260212")



# stock_sse_summary_df = ak.stock_sse_summary()
# print(stock_sse_summary_df)



# stock_zh_a_hist_df = ak.stock_zh_a_hist(symbol="000026", period="daily", start_date="20260301", end_date='20260528', adjust="")
# # print(stock_zh_a_hist_df)
# stock_zh_a_hist_df.to_excel("./outputs/download/000026.xlsx")

# stock_individual_basic_info_xq_df = ak.stock_individual_basic_info_xq(symbol="SH601127") #SH601127  000026
# print(stock_individual_basic_info_xq_df)

# stock_bid_ask_em_df = ak.stock_bid_ask_em(symbol="000026")
# print(stock_bid_ask_em_df)


stock_zh_a_hist_df = ak.stock_zh_a_hist(
    symbol="600734",
    period="daily",
    start_date="20050501",
    end_date="20050520",
    adjust="hfq"
)

stock_zh_a_hist_df.to_excel("./outputs/download/600734.xlsx")
