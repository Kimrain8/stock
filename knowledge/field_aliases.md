# 字段别名表

> 用于把用户自然语言中的词映射到 DataFrame 的实际列名。
>
> 格式：
> ```
> ## 语义名
> - 别名：别名1, 别名2, ...
> - 候选列名：列名1, 列名2, ...
> ```

## 收盘价
- 别名：收盘价, 股价, 价格, 当前价, 最新价, 现价
- 候选列名：close, close_price, 收盘价

## 开盘价
- 别名：开盘价, 今开
- 候选列名：open, open_price, 开盘价

## 最高价
- 别名：最高价, 高点
- 候选列名：high, high_price, 最高价

## 最低价
- 别名：最低价, 低点
- 候选列名：low, low_price, 最低价

## 昨收价
- 别名：昨收, 昨收价, 前收盘, preclose
- 候选列名：pre_close, preclose, 昨收价

## 成交量
- 别名：成交量, 量, 成交手数, 成交量手数
- 候选列名：volume, vol, 成交量

## 成交额
- 别名：成交额, 成交金额
- 候选列名：amount, amt, 成交额

## 换手率
- 别名：换手率, 换手, turnover_rate
- 候选列名：turnover, turnover_rate, 换手率

## 涨跌幅
- 别名：涨跌幅, 涨幅, 跌幅, 涨跌
- 候选列名：pct_chg, change_pct, 涨跌幅

## 涨跌额
- 别名：涨跌额
- 候选列名：change_amount, change, 涨跌额

## 市盈率
- 别名：市盈率, PE, pe
- 候选列名：pe, pe_ttm, 市盈率

## 市净率
- 别名：市净率, PB, pb
- 候选列名：pb, pb_mrq, 市净率

## 流通市值
- 别名：流通市值
- 候选列名：float_mv, circ_mv, 流通市值

## 总市值
- 别名：总市值, 市值
- 候选列名：total_mv, market_cap, 总市值

## 股票代码
- 别名：股票代码, 代码, ts_code, symbol
- 候选列名：ts_code, code, symbol, 股票代码

## 股票名称
- 别名：股票名称, 名称, 简称
- 候选列名：name, stock_name, 股票名称

## 交易日期
- 别名：交易日期, 日期, trade_date
- 候选列名：trade_date, date, 交易日期, 日期

## 流通股本
- 别名：流通股, 流通股本
- 候选列名：float_share, outstanding_share, 流通股本