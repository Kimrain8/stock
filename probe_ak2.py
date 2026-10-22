# -*- coding: utf-8 -*-
"""
腾讯股票「成交明细」（逐笔成交）数据获取 —— gu.qq.com/sz003006/gp/detail 专用页。

对应截图表格列：成交时间 / 成交价格(元) / 价格变动(元) / 成交量(手) / 成交金额(元) / 性质。

接口：
    https://stock.gtimg.cn/data/index.php?appn=detail&action=data&c=sz003006&p=0

要点：
  - 编码 GBK，返回 JS 变量：v_detail_data_sz003006=[页码, "seq/时间/价格/涨跌/量(手)/额(元)/BSM|..."]
  - 分页：p=0 从集合竞价 09:25:00(seq0) 开始，每页约 70 条；p 越大越接近日内最新，最后一页=最新逐笔。
  - 方向：B=买盘、S=卖盘、M=中性盘（与截图"性质"列一致）。
  - 注意接口返回为时间正序（旧->新）；截图页是倒序展示（新->旧），代码里可 reverse。

用法：
    from trade_detail import get_trade_detail

    df = get_trade_detail("sz003006")                 # 取第0页（开盘->约09:33）
    df = get_trade_detail("sz003006", page=5)        # 第5页
    df = get_trade_detail_all("sz003006")            # 自动翻页取全量（休市后一次跑完）
"""

import re
import time
import random
import urllib.request
import urllib.parse

# ----------------------------------------------------------------------------
# 配置：动态 UA、SOCKS5 代理
# ----------------------------------------------------------------------------
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# SOCKS5 代理：设环境变量 HTTPS_PROXY / HTTP_PROXY 即自动生效；也可在此显式指定
#   例：SOCKS5_PROXY = "socks5h://127.0.0.1:1080"
SOCKS5_PROXY = None

API = "https://stock.gtimg.cn/data/index.php"
REFERER = "https://gu.qq.com/"

_NATURE = {"B": "买盘", "S": "卖盘", "M": "中性盘"}

# 输出 DataFrame 列结构
COLUMNS = ["trade_time", "price", "price_change", "volume_lots", "amount_yuan", "nature"]


def normalize_code(code: str) -> str:
    """sz003006/003006 归一化为带前缀代码。"""
    code = code.strip().lower()
    if code.startswith(("sh", "sz")):
        return code
    if code.isdigit() and len(code) == 6:
        return ("sh" if code[0] in "69" else "sz") + code
    raise ValueError("无法识别的股票代码: %r" % code)


def _build_opener(proxy: str = None):
    """按需挂载 SOCKS5/HTTP 代理。需 pip install pysocks（socks5 时）。"""
    proxy_url = proxy or SOCKS5_PROXY
    handlers = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    return urllib.request.build_opener(*handlers)


def _fetch_raw(code: str, page: int, proxy: str = None, retries: int = 3):
    """拉取单页原始文本（GBK），带重试与动态 UA。"""
    params = {"appn": "detail", "action": "data", "c": code, "p": page}
    url = API + "?" + urllib.parse.urlencode(params)
    opener = _build_opener(proxy)
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": random.choice(_UA_POOL),
                "Referer": REFERER,
                "Accept": "*/*",
            })
            raw = opener.open(req, timeout=20).read()
            return raw.decode("gbk", "ignore")
        except Exception as e:
            last_err = e
            time.sleep(0.6 * (i + 1))  # 退避
    raise RuntimeError("成交明细请求失败(p=%d): %s" % (page, last_err))


def _parse(text: str):
    """把 v_detail_data_xxx=[0,"seq/time/price/chg/vol/amount/BS|..."] 解析为 dict 列表。"""
    m = re.search(r'=\[\d+,"?(.*?)"?\]\s*;?\s*$', text.strip())
    if not m or not m.group(1):
        return []
    rows = []
    for rec in m.group(1).split("|"):
        f = rec.split("/")
        if len(f) < 7:
            continue
        try:
            rows.append({
                "trade_time": f[1],                 # HH:MM:SS
                "price": float(f[2]),               # 成交价
                "price_change": float(f[3]),         # 价格变动
                "volume_lots": int(f[4]),           # 成交量(手)
                "amount_yuan": int(f[5]),           # 成交金额(元)
                "nature": _NATURE.get(f[6], f[6]),   # 买盘/卖盘/中性盘
            })
        except (ValueError, IndexError):
            continue
    return rows


def get_trade_detail(code: str, page: int = 0, reverse: bool = True,
                     proxy: str = None, as_dataframe: bool = True):
    """
    获取单页成交明细。

    :param code:        股票代码，如 "003006" / "sz003006"
    :param page:        页码，0=开盘集合竞价起，越大越新；最后一页为最新逐笔
    :param reverse:     True 时按"新->旧"倒序（与页面展示一致）
    :param proxy:       SOCKS5/HTTP 代理，如 "socks5h://127.0.0.1:1080"
    :param as_dataframe: True 返回 pandas.DataFrame（未装 pandas 降级为 list[dict]）
    """
    sym = normalize_code(code)
    text = _fetch_raw(sym, page, proxy=proxy)
    print(text)
    rows = _parse(text)
    if reverse:
        rows = rows[::-1]

    if as_dataframe:
        try:
            import pandas as pd
            return pd.DataFrame(rows, columns=COLUMNS)
        except ImportError:
            pass
    return rows


def get_trade_detail_all(code: str, proxy: str = None, max_pages: int = 60,
                         as_dataframe: bool = True):
    """
    自动翻页取全天成交明细（休市后一次跑完；盘中调用仅返回到当前）。
    以"某页解析为空/无新增"为终止条件，最多翻 max_pages 页。
    """
    sym = normalize_code(code)
    seen, all_rows = set(), []
    for p in range(max_pages):
        text = _fetch_raw(sym, p, proxy=proxy)
        # print(text)
        rows = _parse(text)
        new = 0
        for r in rows:
            key = (r["trade_time"], r["price"], r["amount_yuan"])
            if key not in seen:
                seen.add(key)
                all_rows.append(r)
                new += 1
        if new == 0:        # 本页无新数据，说明已到最后一页
            break
        time.sleep(0.15)     # 轻限速，避免触发频控
    all_rows.sort(key=lambda x: x["trade_time"])   # 时间正序
    if as_dataframe:
        try:
            import pandas as pd
            return pd.DataFrame(all_rows, columns=COLUMNS)
        except ImportError:
            pass
    return all_rows


if __name__ == "__main__":
    df = get_trade_detail("sz003006", page=0) #000027（深圳能源）
    print("=== 成交明细（p=0，页面倒序展示）===")
    print(df.to_string(index=False) if hasattr(df, "to_string") else df)
