# -*- coding: utf-8 -*-
"""
腾讯 mstats 板块成分股接口 xg_plate_stocks.fcgi 的 sign 生成。

sign 算法（已通过已知请求实测验证）：
    sign = md5( 所有非空 GET 参数按插入序拼成 "a=b&c=d&..." + "&key=7ad247390dafce0cf9911de0f2083eba" ).lower()

页面源码关键逻辑（index.d7a7bead.js 内）：
    signForSNP:function(T){
        const k=[];
        for(const C in T) C && k.push(`${C}=${T[C]}`);
        k.push("key=7ad247390dafce0cf9911de0f2083eba");
        return md5(k.join("&")).toLowerCase()
    },
    // loadList 中：
    R = signForSNP({exchange:"12", plate_code:x.type.replace(/pt/,""),
         sort_type:1, source:"zxg", stocks_type:3,
         time:new Date().getTime(), user_type:4});

本模块提供两种取 sign 的方式：
  A) MStatsSigner.get_sign(params, force=False)
     仿照 get_enckey 的范式：用 Playwright 打开 mstats 页面，在页面上下文执行内嵌
     的 md5+key 脚本生成 sign，带 50 秒本地缓存。这是用户要求的"访问页面、跑页面脚本"方式。
  B) sign_for_snp(params) 纯 Python 实现（同一算法），无需浏览器，作为对照。

用法：
    from mstats_sign import MStatsSigner, sign_for_snp

    # A) 浏览器方式（仿 get_enckey）
    # 注意：dict 键的顺序决定 sign 字符串顺序，须与页面一致：
    #      exchange, plate_code, sort_type, source, stocks_type, time, user_type
    #      "time" 项留空即可，页面会用当前时间戳就地替换。
    s = MStatsSigner()
    r = s.get_sign({"exchange": "12", "plate_code": "01801124", "sort_type": "1",
                    "source": "zxg", "stocks_type": "3", "time": "",
                    "user_type": "4"})
    print(r["time"], r["sign"])   # 二者需配套使用

    # B) 纯 Python 方式
    print(sign_for_snp({"exchange":"12","plate_code":"01801124","sort_type":"1",
                        "source":"zxg","stocks_type":"3","time":1792147223264,
                        "user_type":"4"}))
    # -> 73f3cb7516ec62d462a3322f3f6f81aa
"""

import time
import hashlib
import json
import urllib.request
import urllib.parse

from playwright.sync_api import sync_playwright

# 静态密钥：从页面 JS 逆向得到（signForSNP 末尾 push 的 key）
_SIGN_KEY = "7ad247390dafce0cf9911de0f2083eba"
# 用于取 sign 的页面（板块成分股页，板块 pt01801124 = 食品加工）
PLATE_PAGE = (
    "https://stockapp.finance.qq.com/mstats/#mod=list"
    "&id=pt01801124&typename=%E9%A3%9F%E5%93%81%E5%8A%A0%E5%B7%A5&sign=web"
)
# 页面上下文内执行的脚本：复刻页面 signForSNP 的逻辑（含完整 md5 实现）
# 页面并未把 signForSNP 暴露到 window，故需在页面内等价复刻。
_SIGN_JS = r"""
(function () {
  // 标准 MD5（与页面引入的 md5 库一致的算法，输出小写 32 位 hex）
  function md5(str) {
    var i, len, x, a, b, c, d, aa, bb, cc, dd,
        S = [7,12,17,22,7,12,17,22,7,12,17,22,7,12,17,22,
             5,9,14,20,5,9,14,20,5,9,14,20,5,9,14,20,
             4,11,16,23,4,11,16,23,4,11,16,23,4,11,16,23,
             6,10,15,21,6,10,15,21,6,10,15,21,6,10,15,21],
        K = [0xd76aa478,0xe8c7b756,0x242070db,0xc1bdceee,0xf57c0faf,0x4787c62a,
             0xa8304613,0xfd469501,0x698098d8,0x8b44f7af,0xffff5bb1,0x895cd7be,
             0x6b901122,0xfd987193,0xa679438e,0x49b40821,0xf61e2562,0xc040b340,
             0x265e5a51,0xe9b6c7aa,0xd62f105d,0x02441453,0xd8a1e681,0xe7d3fbc8,
             0x21e1cde6,0xc33707d6,0xf4d50d87,0x455a14ed,0xa9e3e905,0xfcefa3f8,
             0x676f02d9,0x8d2a4c8a,0xfffa3942,0x8771f681,0x6d9d6122,0xfde5380c,
             0xa4beea44,0x4bdecfa9,0xf6bb4b60,0xbebfbc70,0x289b7ec6,0xeaa127fa,
             0xd4ef3085,0x04881d05,0xd9d4d039,0xe6db99e5,0x1fa27cf8,0xc4ac5665,
             0xf4292244,0x432aff97,0xab9423a7,0xfc93a039,0x655b59c3,0x8f0ccc92,
             0xffeff47d,0x85845dd1,0x6fa87e4f,0xfe2ce6e0,0xa3014314,0x4e0811a1,
             0xf7537e82,0xbd3af235,0x2ad7d2bb,0xeb86d391];

    // UTF-8 字节数组
    var bytes = [], ch, j;
    for (j = 0; j < str.length; j++) {
      ch = str.charCodeAt(j);
      if (ch < 128) bytes.push(ch);
      else if (ch < 2048) { bytes.push(0xC0 | (ch >> 6), 0x80 | (ch & 63)); }
      else if (ch < 55296 || ch >= 57344) { bytes.push(0xE0 | (ch >> 12), 0x80 | ((ch >> 6) & 63), 0x80 | (ch & 63)); }
      else { j++; ch = 0x10000 + (((ch & 1023) << 10) | (str.charCodeAt(j) & 1023));
             bytes.push(0xF0 | (ch >> 18), 0x80 | ((ch >> 12) & 63), 0x80 | ((ch >> 6) & 63), 0x80 | (ch & 63)); }
    }
    len = bytes.length;

    // 填充并构造 16 字块（little-endian 32bit）
    x = [];
    for (j = 0; j < Math.ceil((len + 9) / 64) * 16; j++) x[j] = 0;
    for (j = 0; j < len; j++) x[j >> 2] |= (bytes[j] & 255) << ((j % 4) * 8);
    x[len >> 2] |= 0x80 << ((len % 4) * 8);
    x[((len + 8) >> 6) * 16 + 14] = (len * 8) & 0xFFFFFFFF;

    function rol(n, s) { return (n << s) | (n >>> (32 - s)); }

    a = 0x67452301; b = 0xefcdab89; c = 0x98badcfe; d = 0x10325476;
    for (i = 0; i < x.length; i += 16) {
      aa = a; bb = b; cc = c; dd = d;
      for (j = 0; j < 64; j++) {
        var f, g;
        if (j < 16)       { f = (b & c) | (~b & d);       g = j; }
        else if (j < 32)  { f = (d & b) | (~d & c);       g = (5 * j + 1) % 16; }
        else if (j < 48)  { f = b ^ c ^ d;                g = (3 * j + 5) % 16; }
        else              { f = c ^ (b | ~d);             g = (7 * j) % 16; }
        f = (f + a + K[j] + x[i + g]) | 0;
        a = d; d = c; c = b;
        b = (b + rol(f, S[j])) | 0;
      }
      a = (a + aa) | 0; b = (b + bb) | 0; c = (c + cc) | 0; d = (d + dd) | 0;
    }

    function hex(n) { // 小端序输出
      var s = "", i;
      for (i = 0; i < 4; i++) { s += ("0" + ((n >>> (i * 8)) & 255).toString(16)).slice(-2); }
      return s;
    }
    return hex(a) + hex(b) + hex(c) + hex(d);
  }

  // 复刻页面 signForSNP：按传入"有序键值对数组"的插入序拼接，末尾补 key，取 md5 小写。
  // 页面源码的对象顺序为 exchange,plate_code,sort_type,source,stocks_type,time,user_type，
  // time 必须位于 user_type 之前，否则 md5 串不同、服务器校验失败。
  // 传入的 pairs 中 key=="time" 的项由页面 Date.now() 就地替换为新鲜值，并一并返回该 time。
  window.__genPlateSign = function (pairs) {
    var k = [], t = null;
    for (var i = 0; i < pairs.length; i++) {
      var key = pairs[i][0], val = pairs[i][1];
      if (key === "time") { t = Date.now(); val = String(t); }
      if (key) k.push(key + "=" + val);
    }
    k.push("key=" + "7ad247390dafce0cf9911de0f2083eba");
    return { time: t, sign: md5(k.join("&")).toLowerCase() };
  };
  return true;
})();
"""
###############获取api具体内容，另一个py文件中
API_URL = "https://bisheng.tenpay.com/fcgi-bin/xg_plate_stocks.fcgi"
QT_URL = "https://qt.gtimg.cn/"
REFERER = "https://stockapp.finance.qq.com/mstats/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
# 页面源码中板块成分股接口的 canonical 参数顺序（决定 sign 字符串顺序，不可乱序）
CANONICAL_ORDER = [
    "exchange", "plate_code", "sort_type", "source", "stocks_type", "time", "user_type",
]


class MStatsSigner:
    """仿 get_enckey 的范式：访问页面 -> 页面上下文跑脚本生成 sign，带 50s 缓存。"""

    # 单例缓存：按 (固定参数指纹) 缓存最近一次结果 {time, sign}
    _CACHE = {"fingerprint": None, "value": None, "time": 0.0}
    _TTL = 50  # 秒，与 get_enckey 一致

    def __init__(self, page_url: str = PLATE_PAGE):
        self.PAGE_URL = page_url

    @staticmethod
    def _fingerprint(params: dict) -> str:
        # 只统计参与签名的"固定"字段（不含 time，time 由页面生成）
        return "&".join(f"{k}={params[k]}" for k in params if k != "time")

    def get_sign(self, params: dict, force: bool = False) -> dict:
        """
        获取接口鉴权签名（= 页面内复刻 signForSNP 的结果）。
        返回 {time, sign}，两者配套使用；带 50 秒本地缓存，避免频繁启动浏览器。

        :param params: 除 sign 外的全部 GET 参数（dict，插入顺序即 sign 拼接顺序），
                       time 键留空（页面用当前时间戳替换），且须按页面顺序放置，
                       例如：
                       {"exchange":"12","plate_code":"01801124","sort_type":"1",
                        "source":"zxg","stocks_type":"3","time":"","user_type":"4"}
        :param force:  强制重新获取（忽略缓存）
        """
        fp = self._fingerprint(params)
        if not force:
            c = self._CACHE
            if c["fingerprint"] == fp and c["value"] and \
                    (time.time() - c["time"]) < self._TTL:
                return c["value"]

        launch_kwargs = {"headless": True}
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(
                    user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/120.0 Safari/537.36"),
                    viewport={"width": 1366, "height": 900},
                )
                page = context.new_page()
                page.goto(self.PAGE_URL, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3000)  # 等待页面脚本就绪
                # 注入复刻 signForSNP 逻辑的脚本，并在页面上下文执行生成 sign。
                # 按 params 的插入顺序构造有序键值对数组（dict 保持插入序），
                # key=="time" 的项由页面就地替换为新鲜 Date.now()。
                page.evaluate(_SIGN_JS)
                pairs = [[k, v] for k, v in params.items()]  # 保持传入顺序
                result = page.evaluate(
                    "window.__genPlateSign(%s)" % __import__("json").dumps(pairs))
            finally:
                browser.close()

        if not result or not result.get("sign"):
            raise RuntimeError("生成 sign 失败：页面未成功执行 __genPlateSign")

        self._CACHE["fingerprint"] = fp
        self._CACHE["value"] = result
        self._CACHE["time"] = time.time()
        return result

def sign_for_snp(params: dict) -> str:
    """
    纯 Python 实现（与页面 signForSNP 完全同逻辑），无需浏览器。
    可直接用于验证 / 或当作轻量实现。
    """
    k = []
    for key in params:
        if key:
            k.append(f"{key}={params[key]}")
    k.append(f"key={_SIGN_KEY}")
    return hashlib.md5("&".join(k).encode("utf-8")).hexdigest().lower()

###############获取api具体内容，另一个py文件中
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
    # 用题目中的已知请求做验证（含指定 time）
    known = sign_for_snp({
        "exchange": "12", "plate_code": "01801124", "sort_type": "1",
        "source": "zxg", "stocks_type": "3", "time": "1792147223264",
        "user_type": "4",
    })
    print("纯 Python 计算 sign:", known)
    assert known == "73f3cb7516ec62d462a3322f3f6f81aa", "算法验证失败！"
    print(">>> 算法验证通过：与题目 URL 中的 sign 一致")

    # 浏览器方式（仿 get_enckey）：time 键留空，页面就地替换；位置须在 user_type 前
    s = MStatsSigner()
    r = s.get_sign({
        "exchange": "12", "plate_code": "01801124", "sort_type": "1",
        "source": "zxg", "stocks_type": "3", "time": "", "user_type": "4",
    })
    print("浏览器方式返回: time=%s  sign=%s" % (r["time"], r["sign"]))
    
    known = sign_for_snp({
        "exchange": "12", "plate_code": "01801124", "sort_type": "1",
        "source": "zxg", "stocks_type": "3", "time": r["time"],
        "user_type": "4",
    })
    print(known,r["sign"])
    ###############获取api具体内容，另一个py文件中
    data, quote = fetch_plate_stocks("01801733") #01801124
    print("签名信息 time=%s sign=%s" % (quote["time"], quote["sign"]))
    print("接口 retcode=%s retmsg=%s" % (data.get("retcode"), data.get("retmsg")))
    stat = data.get("quote_statis", {})
    print("涨/跌/平: %s / %s / %s" % (stat.get("up_cnt"), stat.get("down_cnt"), stat.get("even_cnt")))
    print("成分股 %d 只：" % len(data["stocks"]["stocks_list"]))
    for s in data["stocks"]["stocks_list"]:
        print("  %s  %s  %s" % (s.get("stock_code"), s.get("name"), s.get("price")))
