# -*- coding: utf-8 -*-
"""
腾讯 mstats 板块成分股数据获取：sign 生成 -> 请求 -> 解析 -> 名称/行情补全 的完整闭环。

用法：
    from fetch_plate_stocks import fetch_plate_stocks

    data, quote = fetch_plate_stocks("01801124")   # 食品加工板块
    # data  : 接口返回的原始 JSON
    # quote : {time, sign} 本次签名信息（如需可复现）
"""

import json
import urllib.request
import urllib.parse
from mstats_sign import MStatsSigner

API_URL = "https://bisheng.tenpay.com/fcgi-bin/xg_plate_stocks.fcgi"
QT_URL = "https://qt.gtimg.cn/"
REFERER = "https://stockapp.finance.qq.com/mstats/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 页面源码中板块成分股接口的 canonical 参数顺序（决定 sign 字符串顺序，不可乱序）
CANONICAL_ORDER = [
    "exchange", "plate_code", "sort_type", "source", "stocks_type", "time", "user_type",
]


def build_params(plate_code, exchange="12", sort_type="1", source="zxg",
                 stocks_type="3", user_type="4"):
    """构造 canonical 顺序的参数 dict（time 留空，由页面生成 sign 时就地替换）。"""
    return {
        "exchange": exchange,
        "plate_code": plate_code,
        "sort_type": sort_type,
        "source": source,
        "stocks_type": stocks_type,
        "time": "",            # 占位，MStatsSigner 会用当前时间戳替换并返回真实值
        "user_type": user_type,
    }


def _http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read()


def fetch_plate_stocks(plate_code="01801124", signer=None):
    """
    获取指定板块的成分股数据（含 sign 生成的完整流程）。

    :param plate_code: 板块代码（不带 pt 前缀），如 "01801124"（食品加工）
    :param signer:     可注入的 MStatsSigner 实例（便于复用浏览器/缓存）
    :return: (data, quote)  data=接口原始 JSON；quote={time, sign}
    """
    signer = signer or MStatsSigner()
    params = build_params(plate_code)

    # 1) 生成 sign（页面上下文执行脚本；内部按 canonical 顺序，time 就地替换为新鲜值）
    quote = signer.get_sign(params)          # -> {time, sign}

    # 2) 构造请求（time/sign 与签名严格配套）
    query = dict(params)
    query["time"] = quote["time"]
    query["sign"] = quote["sign"]
    url = API_URL + "?" + urllib.parse.urlencode(query)

    # 3) 请求并解析
    body = _http_get(url, headers={"User-Agent": UA, "Referer": REFERER})
    data = json.loads(body.decode("utf-8", "ignore"))

    if data.get("retcode") != "0":
        raise RuntimeError("接口返回失败: retcode=%s retmsg=%s" %
                           (data.get("retcode"), data.get("retmsg")))

    # 4) 用第二个行情接口补全股票名称/价格（页面本身也是这么做的）
    enrich_names(data)

    return data, quote


def enrich_names(data):
    """stocks_list 里的 stock_code 只是代码（如 600530.SH），名称/行情需从 qt.gtimg.cn 取。"""
    stocks = data.get("stocks", {}).get("stocks_list", [])
    if not stocks:
        return
    codes = []
    for s in stocks:
        code = s.get("stock_code", "")
        if code.endswith(".SH"):
            codes.append("sh" + code[:6])
        elif code.endswith(".SZ"):
            codes.append("sz" + code[:6])
    if not codes:
        return
    try:
        raw = _http_get(QT_URL + "?q=" + ",".join(codes),
                        headers={"User-Agent": UA, "Referer": REFERER,
                                 "Accept-Charset": "GBK,utf-8"})
        # 接口返回 gbk 编码
        text = raw.decode("gbk", "ignore")
        fields = {}
        for seg in text.strip().split(";"):
            seg = seg.strip()
            if "=" not in seg:
                continue
            key, val = seg.split("=", 1)
            parts = val.strip("\"").split("~")
            if len(parts) > 5:
                k = key.strip()
                if k.startswith("v_"):        # 去掉 "v_" 前缀，键变为 sh600530 / sz002626
                    k = k[2:]
                fields[k] = (parts[1], parts[3])   # (名称, 现价)
        for s in stocks:
            code = s.get("stock_code", "")
            prefix = ("sh" + code[:6]) if code.endswith(".SH") else ("sz" + code[:6])
            name, price = fields.get(prefix, ("", ""))
            s["name"] = name
            s["price"] = price
    except Exception as e:  # 行情补全失败不影响已拿到的数据
        print("警告：名称/行情补全失败，%s（成分股数据仍有效）" % e)


if __name__ == "__main__":
    data, quote = fetch_plate_stocks("01801733") #01801124
    print("签名信息 time=%s sign=%s" % (quote["time"], quote["sign"]))
    print("接口 retcode=%s retmsg=%s" % (data.get("retcode"), data.get("retmsg")))
    stat = data.get("quote_statis", {})
    print("涨/跌/平: %s / %s / %s" % (stat.get("up_cnt"), stat.get("down_cnt"), stat.get("even_cnt")))
    print("成分股 %d 只：" % len(data["stocks"]["stocks_list"]))
    for s in data["stocks"]["stocks_list"]:
        print("  %s  %s  %s" % (s.get("stock_code"), s.get("name"), s.get("price")))
