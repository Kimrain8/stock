"""
腾讯证券前复权日线K线数据获取函数
示例接口: https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get
"""

import requests
import pandas as pd
import sys
import json
import re
import time
import random
from typing import Optional
from datetime import datetime, date, timedelta

from IPython import embed
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.dbManager import MySQLHelper
# 用户本机代理（socks5h 支持远程 DNS 解析，避免代理端 DNS 污染）
DEFAULT_PROXY = "socks5h://127.0.0.1:9988"

def get_qq_hfq_kline(
    symbol: str = "sh688502",
    count: int = 320,
    adjust: str = "hfq",
    session: Optional[requests.Session] = None,
    proxy: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    获取腾讯证券日线K线数据, 返回中文字段 DataFrame。
    Parameters
    ----------
    symbol : str
        股票代码，带市场前缀。
        上海: sh + 6位代码 (如 sh688502)
        深圳: sz + 6位代码 (如 sz000001)
        北京: bj + 6位代码
    count : int
        返回最近的交易日数量，默认 320 个交易日。
        注意: 接口单次最多返回约 640 条；更长历史请用 start_date/end_date
        或 fetch_kline_range() 自动分页。
    adjust : str
        复权方式: "qfq" 前复权/ "hfq" 后复权 (默认) / "none" 不复权。
    session : requests.Session, optional
        复用已有会话（批量爬取时传入，可共享代理与连接池）。
    proxy : str, optional
        http/https 代理地址，如 "socks5h://127.0.0.1:9988"。
        仅当未传 session 时生效；传了 session 则代理以 session 配置为准。
    start_date, end_date : str, optional
        按日期范围拉取 (YYYY-MM-DD) ;与 count 二选一，同时传时以日期范围为准。

    Returns
    -------
    pandas.DataFrame
        按日期升序排列，列名如下：
        - 交易日期
        - 开盘价 (元)
        - 收盘价 (元)
        - 最高价 (元)
        - 最低价 (元)
        - 成交量 (股)
        - 除权除息备注 (dict)
        - 换手率 (%)
        - 成交额 (万元)
        - 分红方案 (仅除权除息日有值，否则为空字符串)
    """

    url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
    if adjust not in ("qfq", "hfq", "none"):
        raise ValueError("adjust 仅支持 'qfq' / 'hfq' / 'none'")
    adjust_param = "qfq" if adjust == "qfq" else ("hfq" if adjust == "hfq" else "")
    # JSONP 回调变量名：腾讯前端按复权方式命名（qfq/hfq/不复权）。
    # 实测服务端不校验该值与 param 是否匹配，仅原样回显作回调名；此处按惯例配对。
    var_name = {"qfq": "kline_dayqfq", "hfq": "kline_dayhfq", "none": "kline_day"}[adjust]
    start = start_date or ""
    end = end_date or ""
    params = {
        "_var": var_name,
        "param": f"{symbol},day,{start},{end},{count},{adjust_param}",
        # 防缓存随机数（与原API一致）：JSONP请求常用，避免代理/浏览器命中旧缓存
        "r": f"{random.random():.16f}",
    }

    if session is None:
        session = requests.Session()
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})

    resp = session.get(url, params=params, timeout=10)
    resp.raise_for_status()
    text = resp.text

    # 去掉 JSONP 包装: 变量名={...};  （变量名随复权方式变化，用通用正则剥离）
    json_str = re.sub(r"^[A-Za-z0-9_]+\s*=\s*", "", text).rstrip(";")
    payload = json.loads(json_str)

    if payload.get("code") != 0:
        raise RuntimeError(f"API 返回错误: code={payload.get('code')}, msg={payload.get('msg')}")

    # 定位到 K 线数组：前复权 qfqday / 后复权 hfqday / 不复权 day
    kline_key = {"qfq": "qfqday", "hfq": "hfqday", "none": "day"}[adjust]
    stock_data = payload["data"][symbol]
    kline_rows = stock_data.get(kline_key) or []

    columns = ['trade_date','open_price','close_price','high','low','vol','xrxd_mark','hsl','amount','reserve12','reserve13']
    #['trade_date','symbol','qq_code','open_price','close_price','high','low','vol','hsl','amount','xrxd_mark']
    #["交易日期","开盘价","收盘价","最高价","最低价","成交量","除权除息备注","换手率","成交额","预留字段","扩展字段1",  # day(不复权)模式为11列时存在；接口未返回有效值，属占位]
    df = pd.DataFrame(kline_rows, columns=columns[: len(kline_rows[0])] if kline_rows else columns)

    # 数值列类型转换
    numeric_cols = ["open_price", "close_price", "high", "low", "vol", "hsl", "amount"]
    #["开盘价", "收盘价", "最高价", "最低价", "成交量", "换手率", "成交额"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 交易日期转为日期类型
    if "trade_date" in df.columns:
        df["trade_date"] = pd.to_datetime(df["trade_date"])

    # 从备注对象中抽出分红方案文本，方便查看
    df['xrxd_mark'] = df["xrxd_mark"].apply(lambda x: json.dumps(x, ensure_ascii=False))
    df['qq_code']=symbol
    df['symbol']=re.search(r'\d+', symbol).group()
    df['xrxd_mark'] = df['xrxd_mark'].replace({"{}":None})
    df['reserve12'] = df['reserve12'].replace({"":None})
    # def _extract_fh(obj):
    #     if isinstance(obj, dict) and obj:
    #         return obj.get("FHcontent", "")
    #     return ""
    # if "除权除息备注" in df.columns:
    #     df["分红方案"] = df["除权除息备注"].apply(_extract_fh)

    # 按日期升序
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df

def fetch_kline_range(
    symbol: str,
    start_date: str,
    end_date: str,
    adjust: str = "hfq",
    session: Optional[requests.Session] = None,
    proxy: Optional[str] = None,
    page_size: int = 600,
) -> pd.DataFrame:
    """
    不使用。按日期范围分页拉取完整日线数据。
    实测该接口的 start 参数无效——接口行为固定为「从 end 往前取 count 条」，
    因此本函数用「按 count 从 end 向历史方向翻页」实现，并在客户端
    严格裁剪出 [start_date, end_date] 区间，保证返回数据不越界。

    Parameters
    ----------
    symbol : str
        带市场前缀的股票代码（sh688502 / sz000001 / bj430047）。
    start_date, end_date : str
        起止日期，格式 YYYY-MM-DD。
    adjust : str
        "qfq" 前复权(默认) / "hfq" 后复权 / "none" 不复权。
    session : requests.Session, optional
        复用已有会话。
    proxy : str, optional
        代理地址，如 "socks5h://127.0.0.1:9988"。
    page_size : int
        每页条数，默认 600（接口单次上限约 640 条，留余量防截断边界错位）。

    Returns
    -------
    pandas.DataFrame
        与 get_qfq_daily_kline 同结构，严格落在 [start_date, end_date] 内
        （升序、无重复）。
    """
    d0 = pd.to_datetime(start_date)
    d1 = pd.to_datetime(end_date)
    if d0 > d1:
        raise ValueError("start_date 不能晚于 end_date")

    frames = []
    cursor_end = d1  # 从结束日向历史方向翻页
    while True:
        seg = get_qq_hfq_kline(
            symbol,
            count=page_size,
            adjust=adjust,
            session=session,
            proxy=proxy,
            end_date=cursor_end.strftime("%Y-%m-%d"),
        )
        if seg.empty:
            break
        frames.append(seg)
        earliest = seg["交易日期"].min()
        if earliest <= d0:
            break  # 已翻到起始日之前（或已到上市日，数据取尽）
        cursor_end = earliest - pd.Timedelta(days=1)

    if not frames:
        return pd.DataFrame()

    result = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset="交易日期", keep="first")
        .sort_values("交易日期")
        .reset_index(drop=True)
    )
    # 客户端严格裁剪区间（接口不尊重 start，靠这步保证不越界）
    result = result[(result["交易日期"] >= d0) & (result["交易日期"] <= d1)].reset_index(drop=True)
    return result

def fetch_qq_kline_db(df=None):
    """根据df中的qq_code 获取后复权日K线数据然后插入到qq_hfq_kline"""
    db = MySQLHelper(db='jhd')
    if df is None:
        sql = f"""select qq_code from qq_hsj AS qh 
            where qh.trade_date = (select max(qh2.trade_date) from qq_hsj as qh2 )
            AND   not EXISTS (
            SELECT 1
            FROM qq_hfq_kline AS qhk
            WHERE qhk.symbol = qh.symbol and qhk.trade_date = qh.trade_date
            )
            order by RAND()"""
        df = db.qry2df(sql=sql)
    for i in range(len(df)):
        df_rst = get_qq_hfq_kline(symbol=df.loc[i,'qq_code'])
        if len(df_rst)>=1:
            db.bulkInsUpd(df=df_rst,table='qq_hfq_kline')

def fetch_qq_one_kline_db(qqcode,end_date=datetime.today().strftime("%Y-%m-%d"),cnt=320,adjust='hfq'):
    """主要是当count 大于320,需要拆分,获取一只股票的的后复权K线数据, 存储到qq_hfq_kline
    如果cnt 为空那么就补充从2025-01-02开始到当前的复权数据
    """
    if cnt is None:
        db = MySQLHelper(db='jhd')
        symcode=qqcode[2:]
        sql = (f"select min(qhk.trade_date) as min_trade_date,max(qhk.trade_date) as max_trade_date,"
                f"ceil(DATEDIFF(min(qhk.trade_date) ,cast('2025-01-02' as date))*5/7)-1 as cnt1,"
                f"ceil(DATEDIFF(CURDATE(), max(qhk.trade_date))*5/7) -1 as cnt2 "
                f"from qq_hfq_kline AS qhk where qhk.symbol = '{symcode}' ")
        dfl = db.qry2df(sql=sql)
        if dfl.loc[0,'cnt1'] >= 1:
            df1 = get_qq_hfq_kline(symbol=qqcode,end_date=dfl.loc[0,'min_trade_date'].strftime('%Y-%m-%d'),count=dfl.loc[0,'cnt1'],adjust=adjust)
            if len(df1)>=1:
                db.bulkInsUpd(df=df1,table='qq_hfq_kline')
        if dfl.loc[0,'cnt2'] >= 1:
            df2 = get_qq_hfq_kline(symbol=qqcode,count=dfl.loc[0,'cnt2'],adjust=adjust)
            if len(df2)>=1:
                db.bulkInsUpd(df=df2,table='qq_hfq_kline')
        if dfl.loc[0,'cnt1'] < 1 and dfl.loc[0,'cnt2']<1:
            print(f"{qqcode}已经包含全部数据")
    else:
        while cnt > 0:
            if cnt > 320: 
                df = get_qq_hfq_kline(symbol=qqcode,end_date=end_date,count=320,adjust=adjust)
                cnt = cnt - 320
            else:
                df = get_qq_hfq_kline(symbol=qqcode,end_date=end_date,count=cnt,adjust=adjust)
                cnt = 0
            if len(df) == 0: 
                break
            else:
                db.bulkInsUpd(df=df,table='qq_hfq_kline')
                end_date = df['trade_date'].min().strftime('%Y-%m-%d')

def build_session(proxy: Optional[str] = DEFAULT_PROXY) -> requests.Session:
    """创建带 socks5 代理、伪装浏览器头的会话"""
    session = requests.Session()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    session.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Referer": "https://gu.qq.com/",
        "Accept": "*/*",
    })
    return session

def _fetch_one(
    symbol: str,
    session: requests.Session,
    count: int,
    adjust: str,
    start_date: Optional[str],
    end_date: Optional[str],
    max_retries: int,
    backoff_base: float,
):
    """
    抓取单只股票的日线数据，失败按指数退避重试。
    两种取数模式：
    - 指定 start_date/end_date: 按日期区间自动分页拉取该区间全部日线
    - 未指定: 取最近 count 条
    Returns
    -------
    (symbol, df_or_None, error_or_None)
    """
    for attempt in range(1, max_retries + 1):
        try:
            if start_date and end_date:
                df = fetch_kline_range(
                    symbol, start_date, end_date, adjust=adjust, session=session
                )
            else:
                df = get_qq_hfq_kline(symbol, count=count, adjust=adjust, session=session)
            if df is None or df.empty:
                raise ValueError("接口返回空数据")
            df.insert(0, "股票代码", symbol)  # 标记来源股票
            return symbol, df, None
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries:
                return symbol, None, exc
            # 指数退避 + 随机抖动，避免集中重试触发限流
            time.sleep(backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.5))
    return symbol, None, RuntimeError("unreachable")

def batch_fetch_kline(
    codes_df: pd.DataFrame,
    code_col: str = "qqcode",
    count: int = 320,
    adjust: str = "qfq",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    proxy: Optional[str] = DEFAULT_PROXY,
    workers: int = 4,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    save_path: Optional[str] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    批量爬取多只股票的日线K线数据。

    Parameters
    ----------
    codes_df : pd.DataFrame
        股票代码清单，其中一列存放带市场前缀的代码（sh688502 / sz000001 / bj430047）。
    code_col : str
        codes_df 中存放股票代码的列名，默认 "qqcode"。
    count : int
        仅在不指定日期区间时生效：每只股票返回的最近交易日数量。
    adjust : str
        复权方式: "qfq"(默认) / "hfq" / "none"。
    start_date, end_date : str, optional
        指定后改为按日期区间批量爬取（YYYY-MM-DD）：每只股票自动分页拉取
        该区间内全部日线，替代 count 模式（count 只能取最近 N 条，
        无法覆盖历史任意区间，这正是时间范围参数的核心用途）。
    proxy : str | None
        代理地址，默认 socks5h://127.0.0.1:9988；传 None 则直连。
    workers : int
        并发线程数（大量爬取建议 4~8，过大易被限流）。
    max_retries : int
        单只股票失败后的最大尝试次数（含首次）。
    backoff_base : float
        重试退避基数（秒），第 n 次重试等待 backoff_base * 2^(n-1) 秒。
    save_path : str | None
        若提供，合并结果会写入该 CSV 路径（带 BOM，Excel 打开不乱码）。
    verbose : bool
        是否打印进度。

    Returns
    -------
    pd.DataFrame
        长表：股票代码 + get_qfq_daily_kline 的全部列，按 股票代码/交易日期 排序。
        某只股票全部重试失败时该行记录为 失败原因 列。
    """
    if code_col not in codes_df.columns:
        raise KeyError(f"DataFrame 中找不到列 '{code_col}'，可用列: {codes_df.columns.tolist()}")

    symbols = codes_df[code_col].dropna().astype(str).str.strip().unique().tolist()
    if not symbols:
        raise ValueError(f"列 '{code_col}' 中没有有效的股票代码")

    use_range = bool(start_date and end_date)
    session = build_session(proxy)
    if verbose:
        mode_desc = (f"区间 {start_date} ~ {end_date}" if use_range
                     else f"最近 {count} 条")
        print(f"共 {len(symbols)} 只股票 | 并发 {workers} | 模式: {mode_desc} | "
              f"复权={adjust} | 代理={'已启用 ' + proxy if proxy else '直连'}")

    success, failed = 0, []
    frames = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _fetch_one, sym, session, count, adjust,
                start_date, end_date, max_retries, backoff_base,
            ): sym
            for sym in symbols
        }
        for i, fut in enumerate(as_completed(futures), 1):
            symbol, df, err = fut.result()
            if df is not None:
                success += 1
                frames.append(df)
                if verbose:
                    print(f"[{i}/{len(symbols)}] ✓ {symbol}  共 {len(df)} 条 | "
                          f"{df['交易日期'].min().date()} ~ {df['交易日期'].max().date()}")
            else:
                failed.append((symbol, str(err)))
                if verbose:
                    print(f"[{i}/{len(symbols)}] ✗ {symbol}  失败: {err}")

    if verbose:
        print(f"完成: 成功 {success}/{len(symbols)}，失败 {len(failed)}，"
              f"耗时 {time.time() - start:.1f}s")

    if frames:
        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values(["股票代码", "交易日期"]).reset_index(drop=True)
    else:
        result = pd.DataFrame()

    if failed:
        fail_df = pd.DataFrame(failed, columns=["股票代码", "失败原因"])
        if result.empty:
            result = fail_df
        else:
            # 失败清单单独附在结果末尾的附加表中，用空行分隔便于识别
            result = pd.concat(
                [result, pd.DataFrame([{}]), fail_df], ignore_index=True
            )

    if save_path and not result.empty:
        result.to_csv(save_path, index=False, encoding="utf-8-sig")
        if verbose:
            print(f"已保存: {save_path}")

    return result


if __name__ == "__main__":
    fetch_qq_one_kline_db(qqcode='sh600737',cnt=None,adjust='hfq')
    
    
    # if len(sys.argv)==1:
    #     print("调用格式: python crawl_qq_fq.py sh688502 count hfq")
    # elif len(sys.argv)==2:
    #     fetch_qq_one_kline_db(sys.argv[1])
    # elif len(sys.argv)==3:
    #     fetch_qq_one_kline_db(sys.argv[1],cnt=sys.argv[2])
    # elif len(sys.argv)==4:
    #         fetch_qq_one_kline_db(sys.argv[1],cnt=sys.argv[2],adjust=sys.argv[3])
            
            
    # ---- 示例：输入 DataFrame，列名为 qqcode ----
    # sample = pd.DataFrame({
    #     "qqcode": ["sh688502", "sz300750", "sh600519"],
    #     "备注":   ["茂莱光学", "宁德时代", "贵州茅台"],
    # })
    # # 模式A：按时间范围批量爬取（每只自动分页拉全区间，历史任意区间都能拿）
    # out = batch_fetch_kline(
    #     sample,
    #     start_date="2024-01-01",
    #     end_date="2024-12-31",
    #     workers=3,
    #     proxy=None,          # 无代理环境直连；有代理时默认自动使用 socks5h://127.0.0.1:9988
    #     save_path="kline_batch_demo.csv",
    # )
    # print("\n合并结果形状:", out.shape)
    # print(out.head(5).to_string(index=False))