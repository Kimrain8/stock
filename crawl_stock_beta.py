import json
import random
import subprocess
import os
import pandas as pd
from datetime import datetime,date,timedelta
import numpy as np
import re
import hashlib, time
import requests
from requests.exceptions import RequestException, ProxyError, Timeout

import akshare as ak
import tushare as ts

import asyncio #运行异步函数需要 asyncio.run() 来执行
from playwright.async_api import async_playwright,TimeoutError as PlaywrightTimeoutError #使用异步方式连接到已启动的chrome浏览器
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator #将HTML内容解析成markdown格式
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig,CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter #配合 DefaultMarkdownGenerator 使用, 过滤掉不需要的内容
# from crawl4ai.content_extractor import ContentExtractor #有问题, 暂时不使用
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy #将网页内容提取成json格式
from types import SimpleNamespace #创建一个简单的对象, 方便存储和访问属性
from urllib.parse import quote, urlencode, urlparse, parse_qs

from IPython import embed
from utils.dbManager import MySQLHelper
from mstats_sign import MStatsSigner

# region 打开chrome浏览器, 而且启用远程调试模式
def open_chrome_with_remote_debugging():
    CHROME_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    # 如果你的chrome在别的位置, 改成实际路径

    args = [
        CHROME_PATH,
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
        "--user-data-dir=D:/pyprj/stock2/profile",
        # 可选, 禁止默认弹出欢迎页
        "--no-first-run",
        "--no-default-browser-check",
    ]

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # 启动chrome, 不阻塞python主程序
    proc = subprocess.Popen(
        args,
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,  # windows, 不弹出chrome黑色控制台窗口
    )

    print(f"Chrome已启动, pid={proc.pid}, debug port:9222")
# 后续你可以用 selenium / pycdp 连接 localhost:9222
# 关闭浏览器：proc.terminate()
# embed()  # 进入交互式调试环境, 方便你手动操作浏览器
# proc.terminate()
# endregion

# region 连接到已启动的chrome浏览器, 使用默认方式解析为markdown
# 使用async_playwright连接到已打开网页进行抓取,解析成markdown
async def fetch_web_content1():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.pages[0]        # 当前活动页

        # 2. 获取页面 HTML 和 URL（不刷新、不跳转）
        html = await page.content()
        url = page.url
        title = await page.title()

        # 3. 创建 Markdown 生成器
        generator = DefaultMarkdownGenerator()

        # 4. 生成 Markdown（同步调用, 不要 await）
        #    在 0.9.2 中, 参数名为 input_html
        markdown_result = generator.generate_markdown(
            input_html=html,
            base_url=url
        )

        # 5. 提取文本（MarkdownGenerationResult 对象有 raw_markdown 属性）
        if hasattr(markdown_result, 'raw_markdown'):
            markdown_text = markdown_result.raw_markdown
        else:
            # 如果是字符串直接使用
            markdown_text = markdown_result

    print(f"当前页面: {url}")
    print("\nMarkdown 内容 (前500字符):")
    print(markdown_text[:500])
    
    return SimpleNamespace(
        url=url,
        title=title,
        inner_html=html,
        markdown=markdown_text
    )
# endregion
# region 根据CSS选择器提取网页内容函数
async def get_element_by_js(page, selector: str, index: int = 0, inner_text: str | None = None):
    """
    使用 CSS 选择器在当前页面 DOM 中获取指定元素的信息。
    :param page: playwright page 对象
    :param selector: css选择器
    :param index: 取第几个元素, 默认0第一个；传-1取最后一个
    :param inner_text: 筛选包含该字符串的元素, None则不做文本过滤
    :return: dict | None
    """
    return await page.evaluate(
        """
        ([selector, index, innerText]) => {
            const elements = Array.from(document.querySelectorAll(selector));
            if (!elements.length) return null;

            let targetList = elements;
            if (innerText !== null && innerText !== undefined) {
                targetList = elements.filter(el => el.textContent.includes(innerText));
            }
            if (!targetList.length) return null;

            let element;
            if (index === -1) {
                element = targetList[targetList.length - 1];
            } else {
                element = targetList[index] ?? null;
            }
            if (!element) return null;

            return {
                outerHTML: element.outerHTML,
                innerHTML: element.innerHTML,
                text: element.textContent ?? "",
                attributes: Object.fromEntries(
                    Array.from(element.attributes, attribute => [
                        attribute.name,
                        attribute.value
                    ])
                )
            };
        }
        """,
        [selector, index, inner_text]  # ✅把所有参数打包成一个数组, 只传1个args, 彻底避免参数数量报错
    )
# endregion
# region 连接到浏览器, 然后等待网页加载完成后, page.evaluate获取网页元素
async def safe_page_content(page, retries=3, sleep_ms=800): #可以不用
    """安全获取page.content, 处理页面正在导航报错"""
    for _ in range(retries):
        try:
            # 等待页面状态稳定
            await page.wait_for_load_state("networkidle", timeout=10000)
            return await page.content()
        except Exception as e:
            err_msg = str(e).lower()
            if "page is navigating" in err_msg or "navigating and changing" in err_msg:
                await asyncio.sleep(sleep_ms / 1000)
                continue
            raise
    raise RuntimeError("多次重试依然无法获取页面HTML, 页面持续导航变更")
async def fetch_web_content2():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        for i in browser.contexts:
            print(f"Context: {i}, Pages: {len(i.pages)}")
            for j in i.pages:
                print(f"  Page: {j.url}")
        context = browser.contexts[0]
        page = context.pages[0]        # 当前活动页
        await page.wait_for_load_state("networkidle", timeout=10000)
        # await page.wait_for_selector("div.min-w-0.truncate", state="visible", timeout=10000)
        # check_element_exists = "() => document.querySelector('.my-element') !== null"
        # page.wait_for_function(check_element_exists)
        
        # 2. 获取页面 HTML 和 URL（不刷新、不跳转）
        html = await page.content() #await safe_page_content(page)
        url = page.url
        title = await page.title()
        print(url)
        
        
        #根据js判断是否存在指定的元素：
        """
        svg_js = r"
            ()=>{
            const div = document.querySelector('div.min-w-0.truncate');
            if(!div) return null;
            const svg = div.querySelector('svg.size-18\\ !text-g-send-msg-btn-text');
            return svg ? svg.outerHTML : null;
            }"
        target_svg_html = await page.evaluate(svg_js)
        """

        # 同时查找你的目标svg, 如果有就返回元素的outhtml, 否则返回null
        svg_find_js = r"""
        ()=>{
          const svg = document.querySelector('svg.ruyi-icon');
          return svg ? svg.outerHTML : null;
        }
        """
        target_svg_html = await page.evaluate(svg_find_js)

    # ✅使用js获取指定的元素, 不再调用Crawl4AI.arun raw://
        extract_js = """
        () => {
            const items = [];
            document.querySelectorAll("div.item").forEach(el => {
                const h2 = el.querySelector("h2");
                const a = el.querySelector("a");
                items.push({
                    title: h2?.innerText ?? "",
                    link: a?.getAttribute("href") ?? ""
                })
            });
            return items;
        }
        """
        data = await page.evaluate(extract_js)
        # 拿markdown如果需要, 也可以在playwright做, 这里简单占位
        markdown_text = DefaultMarkdownGenerator().generate_markdown(target_svg_html,page.url)
    return SimpleNamespace(
        url=url,
        data=data,
        inner_html=html,
        markdown=markdown_text,
        target_svg=target_svg_html
    )
# endregion

#region 直接使用requests调用腾讯数据接口, 可能会被反爬, 暂时不用

def get_qq_getRank_requests(board_type="hy2",offset=0,count=20,):
    # import time
    # import requests
    # url = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
    # params = {
    #     "_appver": "11.17.0",
    #     "board_code": "aStock",
    #     "sort_type": "price",
    #     "direct": "down",
    #     "offset": 0,
    #     "count": 20,
    #     "_t": int(time.time() * 1000)
    # }
    # headers = {
    #     "Accept": "*/*",
    #     "Accept-Language": "zh-CN,zh;q=0.9",
    #     "Origin": "https://stockapp.finance.qq.com",
    #     "Referer": "https://stockapp.finance.qq.com/",
    #     "Sec-Fetch-Dest": "empty",
    #     "Sec-Fetch-Mode": "cors",
    #     "Sec-Fetch-Site": "same-site",
    #     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    #     "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
    #     "sec-ch-ua-mobile": "?0",
    #     "sec-ch-ua-platform": '"Windows"'
    # }
    # try:
    #     resp = requests.get(url, params=params, headers=headers, timeout=10)
    #     resp.raise_for_status()
    #     return resp.json()
    # except Exception as e:
    #     print(f"直接http调用失败:{e}")
    #     return None
    
    """获取腾讯 申万二级行业数据"""
    # https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank?board_type=hy2&sort_type=price&direct=down&offset=0&count=20
    base_url = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"
    params = {
        "_appver": "11.17.0",
        "board_type": board_type,
        "sort_type": "price",
        "direct": "down",
        "offset": offset,
        "count": count,
        "_t": int(time.time() * 1000) #int(asyncio.get_event_loop().time() * 1000)
    }
    url = f"{base_url}?{urlencode(params)}"
    headers = {
        "Referer": "https://stockapp.finance.qq.com", #page.evaluate("()=>document.referrer")
        "Origin": "https://stockapp.finance.qq.com", #page.evaluate("() => window.origin")
        "User-Agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36' #await page.evaluate("()=>navigator.userAgent")
    }
    # resp = await page.request.get(url, headers=headers)
    resp = requests.get(base_url, params=params, headers=headers, timeout=10)
    if resp.ok:
        return resp.json()
    return {}

#endregion
#region 获取腾讯网页中财经接口数据 getBoardRankList, 并且查找指定的svg元素
async def get_qq_getBoardRankList1(page, offset: int = 0, count: int = 20):
    """
    使用当前page浏览器上下文主动调用 getBoardRankList, 使用page.evaluate(js) 获取数据
    :param page: playwright page对象
    :param offset: 偏移, curl示例 offset=40
    :param count: 返回条数
    :return: dict | None
    """

    js_code = f"""
    (async () => {{
        const url = new URL("https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList");
        url.searchParams.set("_appver", "11.17.0");
        url.searchParams.set("board_code", "aStock");
        url.searchParams.set("sort_type", "price");
        url.searchParams.set("direct", "down");
        url.searchParams.set("offset", {offset});
        url.searchParams.set("count", {count});
        url.searchParams.set("_t", Date.now());

        try {{
            const resp = await fetch(url.toString(), {{
                method: "GET",
                credentials: "include"
            }});
            if (!resp.ok) return null;
            return await resp.json();
        }} catch(e) {{
            console.error(e);
            return null;
        }}
    }})()
    """
    result = await page.evaluate(js_code)
    return result
async def fetch_web_content3():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        for i in context.pages:
            i.url.split("#")[0]   # 去掉hash部分
            if i.url.split("#")[0] == "https://stockapp.finance.qq.com/mstats/":
                page = i
                break
        if page is None:
            raise RuntimeError("未找到目标页面, 请确保已打开 https://stockapp.finance.qq.com/mstats/")
        # page = context.pages[0]  # 假设第一个页面是目标页面
        page_url = page.url
        page_title = await page.title()

        # 主动调用接口, offset=40 count=20, 对应你的curl示例
        api_data = await get_qq_getBoardRankList1(page, offset=1, count=20)
        # DOM 查找目标 svg；page.evaluate 中的参数会安全传入 JavaScript。
        target_svg = await get_element_by_js(
                page,
                "div.title-caption.fl.top-title",
        )

    return SimpleNamespace(
        url=page_url,
        title=page_title,
        api_data=api_data,
        target_svg=target_svg
    )
async def fetch_web_content4(qq):
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        for i in context.pages:
            i.url.split("#")[0]   # 去掉参数部分
            if i.url.split("#")[0] == "https://stockapp.finance.qq.com/mstats/":
                page = i
                break
        if page is None:
            raise RuntimeError("未找到目标页面, 请确保已打开 https://stockapp.finance.qq.com/mstats/")
        page_url = page.url
        page_title = await page.title()

        # 主动调用接口, offset=40 count=20, 对应你的curl示例
        api_data = await qq.get_qq_getBoardRankList(page, offset=20, count=20)
        # DOM 查找目标 svg；page.evaluate 中的参数会安全传入 JavaScript。
        target_svg = await get_element_by_js(
                page,
                "div.title-caption.fl.top-title",
        )

    return SimpleNamespace(
        url=page_url,
        title=page_title,
        api_data=api_data,
        target_svg=target_svg
    )
# endregion

#region 查询行业对应的个股的情况
async def fetch_xg_plate_stocks_df2(plate_code_ori, plate_name, sort_type=1, stocks_type=3, source="zxg", user_type=4):
    """获取xg_plate_stocks.fcgi(行业对应的股票明细),然后封装成df
    这个其实有点bug:因为是先获取sign和time, 然后由page发出api请求。应该发出了2个API请求"""
    base_url = "https://bisheng.tenpay.com/fcgi-bin/xg_plate_stocks.fcgi"
    captured_sign = None
    captured_time = None
    plate_code_list = re.findall(r'\d+', plate_code_ori)
    plate_code = plate_code_list[0] if plate_code_list else ""

    def on_request(req):
        nonlocal captured_sign, captured_time
        url_str = req.url
        if base_url in url_str:
            parsed = urlparse(url_str)
            qs = parse_qs(parsed.query)
            if "sign" in qs and "time" in qs:
                captured_sign = qs["sign"][0]
                captured_time = qs["time"][0]
                print(f"✅捕获sign={captured_sign}, time={captured_time}")

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = None
        for i in context.pages:
            u = urlparse(i.url)
            frag_qs = parse_qs(u.fragment)
            if (i.url.split("#")[0] == "https://stockapp.finance.qq.com/mstats/" and
                    all(j in frag_qs.keys() for j in ['mod', 'id', 'typename', 'sign'])):
                page = i
                break
        if page is None:
            print("未找到目标页面, 请确保已打开 https://stockapp.finance.qq.com/mstats/")
            page = await browser.new_page()

        page.on("request", on_request)

        web_url = "https://stockapp.finance.qq.com/mstats/"
        params = {
            "mod": "list",
            "id": plate_code_ori,
            "typename": plate_name,
            "sign": 'web'
        }
        await page.goto(f"{web_url}#{urlencode(params)}", timeout=20000)
        await page.wait_for_selector("ul#list-body",timeout=10000)
        # await page.wait_for_load_state("networkidle")

        # try:
        #     await page.evaluate("""()=>{
        #         if(window.__vue__){
        #             window.__vue__.loadList();
        #         }
        #     }""")
        # except Exception as e:
        #     print(f"⚠️执行JS触发加载失败: {e}, 等待页面自动加载")

        wait_max = 12
        elapsed = 0
        while elapsed < wait_max:
            if captured_sign and captured_time:
                break
            await asyncio.sleep(0.5)
            elapsed += 0.5

        page.remove_listener("request", on_request)

        if not captured_sign or not captured_time:
            print(f"⚠️超时, 未捕获到sign, plate_code={plate_code}")
            print(f"当前页面URL：{page.url}")
            return pd.DataFrame()

        req_params = {
            "exchange": "12",
            "plate_code": plate_code,
            "sort_type": sort_type,
            "source": source,
            "stocks_type": stocks_type,
            "time": captured_time,
            "user_type": user_type,
            "sign": captured_sign
        }
        url = f"{base_url}?{urlencode(req_params)}"

        headers = await page.evaluate("""()=>{
            return {
                'User-Agent': navigator.userAgent,
                'Referer': window.location.href,\
                'Origin': 'https://stockapp.finance.qq.com'
            }
            }""")
        resp = await page.request.get(url, headers=headers)
        if resp.ok:
            api_data = await resp.json()
        else:
            api_data = {}

        rank_list = api_data.get("stocks", {}).get("stocks_list", [])
        df = pd.DataFrame(rank_list)
        return df
async def fetch_xg_plate_stocks_df1(plate_code_ori, plate_name, sort_type=1, stocks_type=3, source="zxg", user_type=4):
    """无需捕获sign参数,但是重复连接CDP不稳定"""
    base_url_path = "https://bisheng.tenpay.com/fcgi-bin/xg_plate_stocks.fcgi"

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = None
        for i in context.pages:
            u = urlparse(i.url)
            frag_qs = parse_qs(u.fragment)
            if (i.url.split("#")[0] == "https://stockapp.finance.qq.com/mstats/" and
                    all(j in frag_qs.keys() for j in ['mod', 'id', 'typename', 'sign'])):
                page = i
                break

        if page is None:
            print("未找到目标页面, 请确保已打开 https://stockapp.finance.qq.com/mstats/")
            page = await browser.new_page()

        web_url = "https://stockapp.finance.qq.com/mstats/"
        params = {
            "mod": "list",
            "id": plate_code_ori,
            "typename": plate_name,
            "sign": 'web'
        }
        target_hash = urlencode(params)
        full_url = f"{web_url}#{target_hash}"

        api_data = None
        resp_future = asyncio.Future()

        def on_request(req):
            if base_url_path in req.url:
                print(f"[PLAYWRIGHT REQ] {req.url}")

        def on_response(resp):
            if base_url_path in resp.url and not resp_future.done():
                resp_future.set_result(resp)
                print(f"[PLAYWRIGHT RESP] 收到bisheng接口响应")

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            # 第一步：执行goto导航
            await page.goto(full_url, timeout=2000)

            # 等待列表DOM元素渲染完成
            await page.wait_for_selector("ul#list-body", timeout=8000)
            print("[INFO] ul#list-body 元素已渲染完成")

            # 检查：是否已经拿到接口响应
            if not resp_future.done():
                print("[WARN] DOM已渲染, 但未捕获到接口响应, 执行reload重试")
                await page.reload(timeout=15000)

            # 等待接口响应, 收到数据立刻返回, 兜底超时30秒
            response = await asyncio.wait_for(resp_future, timeout=30)
            api_data = await response.json()

        except asyncio.TimeoutError:
            print(f"【超时】等待bisheng接口响应超时（30s）")
        except PlaywrightTimeoutError as e:
            print(f"Playwright超时: {e}")
        except Exception as e:
            print(f"抓取异常: {repr(e)}")
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)

        if api_data is None or api_data.get('stocks') is None:
            api_data = {}
        rank_list = api_data.get("stocks", {}).get("stocks_list", [])
        df = pd.DataFrame(rank_list)
        return df
#endregion
#region Myts类, 从tushare中获取交易日历/股票清单/日交易信息
class Myqq():
    def __init__(self):
        self.db = MySQLHelper(db='jhd')
        self.trade_date = pd.Timestamp.now().date()
        self.BASE_URL = "https://webapi.cninfo.com.cn"
    def get_qq_sign(self,params, t=int(time.time() * 1000)):
        """获取行业对应的股票明细api,生成需要的动态sign值, plate_code只含数字
        引用：
        t = int(time.time() * 1000)  # 毫秒时间戳
        sign = gen_sign("12", "01801733", "1", "zxg", "3", "4", t)
        """
        params = {
            "exchange": params["exchange"],
            "plate_code": params["plate_code"],
            "sort_type": params["sort_type"],
            "source": params["source"],
            "stocks_type": params["stocks_type"],
            "time": t,
            "user_type": params["user_type"],
        }
        s = "&".join(f"{k}={v}" for k, v in params.items())
        s += "&key=7ad247390dafce0cf9911de0f2083eba"
        token = hashlib.md5(s.encode()).hexdigest()
        return token
    def _request(self,data_url: str, params: dict, rhead: dict,rtype:str = 'json',  
                    method: str = "GET",timeout: int = 30, proxy_pool: list = None,
                    max_retries: int = 3, retry_sleep: tuple = (2, 6)):
        """按 method("GET"/"POST")请求一个 sysapi 并返回 records 列表；失败返回 -1。"""
        method = method.upper()
        if method not in ("GET", "POST"):
            raise ValueError(f"method 只支持 'GET' / 'POST'，收到 {method!r}")
        if proxy_pool is None:
            proxy_pool = getattr(self, "proxy_pool", None)
        if proxy_pool is None:
            alive = None
            single_proxy = None
            bad_proxy = None
        else:
            single_proxy = len(proxy_pool) == 1
            bad_proxy = getattr(self, "_BAD_PROXY", {})
            self._BAD_PROXY = bad_proxy
            alive = [p for p in proxy_pool if bad_proxy.get(p, 0) < 3] or proxy_pool
        
        def _do_request():
            last_exc = None
            for attempt in range(max_retries):
                proxy = random.choice(alive) if alive else None
                proxies = {"http": proxy, "https": proxy} if proxy else None
                try:
                    with requests.Session() as session:
                        session.headers.update(rhead)
                        resp = session.request(method, data_url, params=params,
                                                timeout=timeout, proxies=proxies)
                        resp.raise_for_status()
                        if rtype == 'json':
                            return resp.json()
                        elif rtype == 'str':
                            return resp
                except (ProxyError, Timeout) as e:
                    if not single_proxy and proxy:
                        bad_proxy[proxy] = bad_proxy.get(proxy, 0) + 1
                        alive[:] = [p for p in alive if bad_proxy.get(p, 0) < 3] or proxy_pool
                    last_exc = e
                except RequestException as e:
                    last_exc = e
                if attempt < max_retries - 1:
                    wait = random.uniform(*retry_sleep)
                    print(f"[重试 {attempt + 1}/{max_retries}] 等待 {wait:.2f}s …")
                    time.sleep(wait)
            raise last_exc if last_exc else RuntimeError("request failed")

        data = _do_request()
        # 令牌失效重试（换 key，同样走代理）
        if isinstance(data, dict) and data.get("resultcode") in (401, 451):
            time.sleep(random.uniform(*retry_sleep))
            data = _do_request()
        return data
    def fetch_plate_stocks_df(self,plate_code_ori ):
        """获取行业（板块）下的个股详情。直接获取sign，然后获取api数据 """
        base_url_path = "https://bisheng.tenpay.com/fcgi-bin/xg_plate_stocks.fcgi"
        plate_code = next(iter(re.findall(r"\d+", plate_code_ori)), None)
        params = {
                    "exchange": 12, #"mod": "list", 
                    "plate_code": plate_code, #"id": plate_code_ori,
                    "sort_type": 1, #"typename": plate_name,
                    "source": "zxg", #"sign": 'web'
                    "stocks_type": 3,
                    "time": "",
                    "user_type": 4,
                }
        _t = int(time.time() * 1000)
        token = self.get_qq_sign(params=params,t=_t)
        params.update({"sign": token,"time":_t})
        rhead = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": "https://stockapp.finance.qq.com/",
            }
        api_data = self._request(data_url=base_url_path,params=params,rhead=rhead)
        if api_data is None or api_data.get('stocks') is None:
            api_data = {}
        rank_list = api_data.get("stocks", {}).get("stocks_list", [])
        df = pd.DataFrame(rank_list)
        return df
    def fetch_plate_stocks_db(self,df=None,plate_code_ori=None, plate_name=None,waitsecond=None):
        """获取行业（板块）下的个股详情"""
        def process_df(df,pcode,pname):
            df.rename(columns={"stock_code": "ts_code","stock_name":"name","price":"zxj","change_percent":"pct_chg",
                                "price_change":"change_amount","market_cap":"zsz",
                                "liutong_cap":"ltsz","main_net_inflow":"zljlr","turnover_amount":"vol",
                                "turnover_money":"amount","turnover_ratio":"hsl",
                                "amplitude":"zf","quantity_ratio":"lb","stock_type":"market"}, inplace=True)
            df["trade_date"] = self.trade_date #- pd.Timedelta(days=1)
            df["symbol"] = df['ts_code'].str.extract(r'(\d+)')
            df['tags'] = df["tags"].apply(lambda x: json.dumps(x, ensure_ascii=False))
            df['code_qqhy'] = pcode
            df['symbol_hy'] = df['code_qqhy'].str.extract(r'(\d+)')
            df['name_hy'] = pname
            return df
        def fetch_plate_stocks_db_sub(df=None,plate_code_ori=None, plate_name=None,waitsecond=None):
            if df is None and plate_code_ori is not None and plate_name is not None:
                df_rst = self.fetch_plate_stocks_df(plate_code_ori)
                df_rst = process_df(df_rst,plate_code_ori,plate_name)
                self.db.bulkInsUpd(df=df_rst,table='qq_hy_detail')
                self.db.bulkInsUpd(df=df_rst,table='qq_hy_detail_daily')
            elif df is not None:
                for i in range(len(df)):
                    code = df.loc[i, 'code_qqhy']
                    name = df.loc[i, 'name_hy']
                    print(f"\n=== 抓取 {code} {name} ===")
                    df_rst = self.fetch_plate_stocks_df(code)
                    df_rst = process_df(df_rst,code,name)
                    self.db.bulkInsUpd(df=df_rst,table='qq_hy_detail')
                    self.db.bulkInsUpd(df=df_rst,table='qq_hy_detail_daily')
                    if waitsecond is not None : time.sleep(waitsecond)
        if df is None and plate_code_ori is None and plate_name is None:
            df = self.db.qry2df(sql="select max(trade_date) as trade_date from qq_hy_hsj ")
            sql = f"""select DISTINCT qhh.code_qqhy,qhh.name_hy
            from qq_hy_hsj AS qhh where trade_date='{df.iat[0,0].strftime("%Y-%m-%d")}'
            and not EXISTS (SELECT 1 FROM qq_hy_detail AS qhd
            WHERE qhd.code_qqhy = qhh.code_qqhy and qhd.trade_date = '{self.trade_date.strftime("%Y-%m-%d")}')"""
            df = self.db.qry2df(sql)
            df = df.iloc[np.random.permutation(len(df))].reset_index(drop=True)
        fetch_plate_stocks_db_sub(df=df,plate_code_ori=plate_code_ori,plate_name=plate_name,waitsecond=waitsecond)
        return 0


    async def fetch_xg_plate_stocks_df(self,plate_code_ori, plate_name,browser ):
        """使用监控web中的request，获取行业（板块）下的个股详情"""
        base_url_path = "https://bisheng.tenpay.com/fcgi-bin/xg_plate_stocks.fcgi"

        context = browser.contexts[0]
        page = None
        for i in context.pages:
            u = urlparse(i.url)
            frag_qs = parse_qs(u.fragment)
            if (i.url.split("#")[0] == "https://stockapp.finance.qq.com/mstats/" and
                    all(j in frag_qs.keys() for j in ['mod', 'id', 'typename', 'sign'])):
                page = i
                break

        if page is None:
            print("未找到目标页面，请确保已打开 https://stockapp.finance.qq.com/mstats/")
            page = await browser.new_page()

        web_url = "https://stockapp.finance.qq.com/mstats/"
        params = {
            "mod": "list",
            "id": plate_code_ori,
            "typename": plate_name,
            "sign": 'web'
        }
        target_hash = urlencode(params)
        full_url = f"{web_url}#{target_hash}"

        api_data = None
        resp_future = asyncio.Future()

        def on_request(req):
            if base_url_path in req.url:
                print(f"[PLAYWRIGHT REQ] {req.url}")

        def on_response(resp):
            if base_url_path in resp.url and not resp_future.done():
                resp_future.set_result(resp)
                print(f"[PLAYWRIGHT RESP] 收到bisheng接口响应")

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            await page.goto(full_url, timeout=15000)

            await page.wait_for_selector("ul#list-body", timeout=10000)
            print("[INFO] ul#list-body 元素已渲染完成")

            if not resp_future.done():
                print("[WARN] DOM已渲染，但未捕获到接口响应，执行reload重试")
                await page.reload(timeout=15000)

            response = await asyncio.wait_for(resp_future, timeout=30)
            api_data = await response.json()

        except asyncio.TimeoutError:
            print(f"【超时】等待bisheng接口响应超时（30s）")
        except PlaywrightTimeoutError as e:
            print(f"Playwright超时: {e}")
        except Exception as e:
            print(f"抓取异常: {repr(e)}")
        finally:
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)

        if api_data is None or api_data.get('stocks') is None:
            api_data = {}
        rank_list = api_data.get("stocks", {}).get("stocks_list", [])
        df = pd.DataFrame(rank_list)
        return df
    async def fetch_xg_plate_stocks_db(self,df=None,plate_code_ori=None, plate_name=None,waitsecond=None):
        """获取行业（板块）下的个股详情"""
        def process_df(df,pcode,pname):
            df.rename(columns={"stock_code": "ts_code","stock_name":"name","price":"zxj","change_percent":"pct_chg",
                                "price_change":"change_amount","market_cap":"zsz",
                                "liutong_cap":"ltsz","main_net_inflow":"zljlr","turnover_amount":"vol",
                                "turnover_money":"amount","turnover_ratio":"hsl",
                                "amplitude":"zf","quantity_ratio":"lb","stock_type":"market"}, inplace=True)
            df["trade_date"] = self.trade_date #- pd.Timedelta(days=1)
            df["symbol"] = df['ts_code'].str.extract(r'(\d+)')
            df['tags'] = df["tags"].apply(lambda x: json.dumps(x, ensure_ascii=False))
            df['code_qqhy'] = pcode
            df['symbol_hy'] = df['code_qqhy'].str.extract(r'(\d+)')
            df['name_hy'] = pname
            return df
        async with async_playwright() as p:
            # 只连接一次CDP，全局复用browser
            # browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            context = await p.chromium.launch_persistent_context(
                user_data_dir="./chrome_user_data",headless=True
            )
            browser = context.browser
            # page = await context.new_page()
            
            if df is None and plate_code_ori is not None and plate_name is not None:
                df_rst = await self.fetch_xg_plate_stocks_df(plate_code_ori, plate_name,browser)
                df_rst = process_df(df_rst,plate_code_ori,plate_name)
                self.db.bulkInsUpd(df=df_rst,table='qq_hy_detail')
                self.db.bulkInsUpd(df=df_rst,table='qq_hy_detail_daily')
            elif df is not None:
                for i in range(len(df)):
                    code = df.loc[i, 'code_qqhy']
                    name = df.loc[i, 'name_hy']
                    print(f"\n=== 抓取 {code} {name} ===")
                    df_rst = await self.fetch_xg_plate_stocks_df(code, name,browser)
                    df_rst = process_df(df_rst,code,name)
                    self.db.bulkInsUpd(df=df_rst,table='qq_hy_detail')
                    self.db.bulkInsUpd(df=df_rst,table='qq_hy_detail_daily')
                    if waitsecond is not None : time.sleep(waitsecond)
    async def fetch_batch_plate_db(self,again=0):
        """批量获取行业数据对应的个股明细，保存到数据库"""
        if again == 0:
            await self.fetch_getBoardRankList_db()
            await self.fetch_getRank_db()
            # 下面是两个任务并行跑
            # task1 = self.fetch_getBoardRankList_db()
            # task2 = self.fetch_getRank_db()
            # await asyncio.gather(task1, task2)
        sql = f"""'{self.trade_date.strftime("%Y-%m-%d")}'"""
        sql = f"""select DISTINCT qhh.code_qqhy,qhh.name_hy
        from qq_hy_hsj AS qhh where trade_date='{self.trade_date.strftime("%Y-%m-%d")}'
        and not EXISTS (SELECT 1 FROM qq_hy_detail AS qhd
        WHERE qhd.code_qqhy = qhh.code_qqhy and date(qhd.update_time) = qhh.trade_date)"""
        df = self.db.qry2df(sql)
        df = df.iloc[np.random.permutation(len(df))].reset_index(drop=True)
        await self.fetch_xg_plate_stocks_db(df=df)

    def get_qq_quotes_df(self,codes):
        """
        从腾讯财经接口获取股票实时行情数据,返回包含指定中文字段名的 DataFrame。
        如果字段数量不是88个,或发生其他错误,控制台打印错误并返回空 DataFrame（仅保留列名）。
        参数
        codes : List[str]:股票代码列表,需包含市场前缀,例如 ['sh600346', 'sz301086']。
        返回
        pd.DataFrame :成功时返回包含数据的 DataFrame,失败时返回空 DataFrame。
        """
        # 定义 88 个字段的中文名称
        field_names = ['market_stus','name','symbol','zxj','pre_close','open_price','vol','vol_outer','vol_inner',
            'price_buy1','vol_buy1','price_buy2','vol_buy2','price_buy3','vol_buy3','price_buy4','vol_buy4','price_buy5','vol_buy5',
            'price_sel1','vol_sel1','price_sel2','vol_sel2','price_sel3','vol_sel3','price_sel4','vol_sel4','price_sel5','vol_sel5',
            'reserve1','price_time','change_amount','pct_chg','high','low','zxjle','vol_repeat','amount','hsl','pe_ttm',
            'reserve2','high_repeat','low_repeat','zf','ltsz','zsz','pn','ztj','dtj','lb','weicha',
            'price_avg','pe_dyna','pe_static','reserve3','reserve4','unknown5','amount_repeat','amount_fixed','vol_fixed','stock_stus_auth','market',
            'upps','zdf_d5','ttm','roe_ttm','unknown10','high_1y','low_1y','zdf_d10','zdf_d20','zdf_d60','ltgb',
            'zgben','weibi','reserve5','ltgb_repeat','reserve7','reserve8','reserve9','reserve10','reserve11','curr_type','unknown16','trade_stus',
            'price_ah','unknown18','unknown19']

        # 检查是否正好 88 个字段,若不够则补足（但实际已有88个,但为了严谨可检查）
        if len(field_names) != 88:
            # 此处补全至88个（若有遗漏）
            while len(field_names) < 88:
                field_names.append(f"未知{len(field_names)}")
            field_names = field_names[:88]  # 截断
        if isinstance(codes,str):
            codes = [codes]
        # 构建 URL
        url = f"http://qt.gtimg.cn/q={','.join(codes)}"

        try:
            resp = requests.get(url, timeout=10)
            resp.encoding = 'gbk'
            text = resp.text
        except Exception as e:
            print(f"请求失败: {e}")
            return pd.DataFrame(columns=field_names)  # 返回空 DataFrame

        # 正则匹配每个股票的数据
        pattern = re.compile(r'v_[^=]+="([^"]+)"')
        matches = pattern.findall(text)
        
        if not matches:
            print("未解析到任何股票数据,请检查输入代码或接口返回格式。")
            return pd.DataFrame(columns=field_names)

        rows = []
        for data_str in matches:
            fields = data_str.split('~')
            if fields[0][0:2] != '62' and len(fields) != 88:
                print(f"错误：字段数量为 {len(fields)},{fields[1]},{fields[2]},应为 88。返回空 DataFrame。")
            elif fields[0][0:2] != '62' and len(fields) == 88:
                rows.append(fields)
            elif fields[0][0:2] == '62' and len(fields) != 87:
                print(f"错误：字段数量为 {len(fields)},{fields[1]},{fields[2]},应为 87。返回空 DataFrame。")
            elif fields[0][0:2] == '62' and len(fields) == 87:
                fields.append(None)
                rows.append(fields)
                # return pd.DataFrame(columns=field_names)  # 立即返回空 DataFrame
        # 构建 DataFrame
        df = pd.DataFrame(rows, columns=field_names)
        return df
    def fetch_quotes_db(self,codes=None):
        """codes :list类型, 是qqcode的格式: sz000408"""
        if codes is None:
            sql = f"""select distinct qh.qq_code from qq_hsj AS qh
            where qh.trade_date  = CURDATE()
            and not EXISTS (SELECT 1 FROM qq_qt AS qq where qh.qq_code = qq.qq_code and qh.trade_date = qq.trade_date )"""
            df2 = self.db.qry2df(sql=sql)
            df2 = df2.iloc[np.random.permutation(len(df2))].reset_index(drop=True)
            while len(df2)>=1:
                if len(df2) <= 100 : 
                    codes = df2['qq_code'].to_list()
                    df2 = pd.DataFrame(columns=df2.columns)
                else:
                    codes = df2.loc[0:99,'qq_code'].to_list()
                    df2 = df2.loc[100:,:].reset_index(drop=True)
                df = self.get_qq_quotes_df(codes=codes)
                if len(df) >= 1:
                    df = df.replace(["",r"^\s*$"],None,regex=True)
                    df['trade_date'] = self.trade_date
                    df['qq_code']=codes
                    self.db.bulkInsUpd(table='qq_qt',df=df)
        else:
            df = self.get_qq_quotes_df(codes=codes)
            if len(df) >= 1:
                df = df.replace(["",r"^\s*$"],None,regex=True)
                df['trade_date'] = self.trade_date
                df['qq_code']=codes
                self.db.bulkInsUpd(table='qq_qt',df=df)
    @staticmethod
    def get_qq_gudong(df = None, qq_code = None):
        """
        获取腾讯财经流通股东数据, 返回结构化DataFrame
        :param df: DataFrame, 可选, 必须包含列 qq_code, 批量抓取
        :param qq_code: str, 可选, 单股票代码, 例如 sz301206
        :return: pd.DataFrame, 字段对齐mysql表：dt,pdt,trade_date,qq_code,bdms,cgsl,gddm,gdmc,gfxz,ltbl,sqcgsl
        """
        base_url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/hs/ltgd/get"
        headers = {
            "Referer": "https://gu.qq.com/", #page.evaluate("()=>document.referrer")
            "Origin": "https://gu.qq.com", #page.evaluate("() => window.origin")
            "User-Agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36' #await page.evaluate("()=>navigator.userAgent")
        }
        pattern = re.compile(r'v_liutonggd=({.*})', re.S)
        cols=["bdms", "cgsl","gddm", "gdmc", "gfxz", "ltbl", "sqcgsl", "dt", "pdt","trade_date", "qq_code"] #
        def _get_one(qq_code):
            params = {
                "type": "ltgd",
                "_var": "v_liutonggd",
                "code": qq_code,
                "_": int(time.time() * 1000) #int(asyncio.get_event_loop().time() * 1000)
            }
            # url = f"{base_url}?{urlencode(params)}"
            # resp = await page.request.get(url, headers=headers)
            resp = requests.get(base_url, params=params, headers=headers, timeout=10)
            df = pd.DataFrame(columns=cols)
            if resp.ok:
                match = pattern.search(resp.text)
                if not match:
                    return df
                else:
                    resp = match.group(1)
                    try:
                        resp = json.loads(resp)
                        resp = resp['data']['data']
                        for i in range(len(resp)):
                            df2 = pd.DataFrame(resp[i]['rows'])
                            df2['dt'] = resp[i]['dt']
                            df2['pdt'] = resp[i]['pdt']
                            df2['trade_date'] = pd.Timestamp.now().date()
                            df2['qq_code'] = qq_code
                            df = pd.concat([df, df2], ignore_index=True)
                        return df
                    except json.JSONDecodeError as e:
                        return df
        if df is not None and len(df)>=1 and qq_code is None :
            df_rst = _get_one(df.loc[0,'qq_code'])
            for i in range(1,len(df)):
                df_rst = pd.concat((df_rst,_get_one(df.loc[i,'qq_code'])),ignore_index=True)
        elif qq_code is not None and df is None:
            df_rst = _get_one(qq_code)
        return df_rst
    async def fetch_qt_web(self,qq_code = "sh688041"):
            """获取整个html的数据"""
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir="./chrome_user_data",headless=False)
                page = await context.new_page()
                browse = context.browser
                
                web_url = f"""https://gu.qq.com/{qq_code}/gp"""
                await page.goto(web_url)
                await page.wait_for_selector("li.zoom", state="visible", timeout=10000)
                
                html = await page.content()
            return html
#region Myts类，从tushare中获取交易日历/股票清单/日交易信息
class Myts():
    def __init__(self):
        ts.set_token(os.getenv(key="tskey"))
        self.pro = ts.pro_api()
        self.db = MySQLHelper(db='jhd')
        self.max_trade_date = self.get_max_trade_date()
    def get_max_trade_date(self):
        df = self.db.qry2df(f"""select max(trade_date) as trade_date from ts_stock_daily_his AS ttd""") 
        return pd.Timestamp.now().date() if pd.isna(df.iloc[0,0]) else pd.to_datetime(df.iloc[0,0]).date()
    def lookUpColName(self,df):
        """把字段映射为可以理解的短备注"""
        cols = self.db.qry2df(f"""select distinct cols.COLUMN_NAME, \
            SUBSTRING_INDEX(cols.COLUMN_COMMENT,'~',1) as COLUMN_COMMENT \
            from information_schema.`COLUMNS` as cols \
            where cols.TABLE_SCHEMA = 'jhd'""")
        cols["COLUMN_COMMENT"] = cols["COLUMN_COMMENT"].apply(lambda x: x.decode('utf-8') if isinstance(x, bytes) else x)
        colmap = cols['COLUMN_COMMENT']
        colmap.index = cols['COLUMN_NAME']
        return df.columns.map(colmap)
    def get_trade_calendar(self,startdate=None,enddate=None):
        """获取交易日历，一小时只能访问一次,日期格式：%Y%m%d"""
        if startdate is None : startdate=self.max_trade_date
        if enddate is None: enddate=pd.Timestamp.now().date() #pd.Timestamp(year=startdate.year, month=12, day=31).date()
        if not isinstance(startdate, str): 
            startdate=startdate.strftime("%Y%m%d")
            enddate=enddate.strftime("%Y%m%d")
        df = self.pro.trade_cal(exchange='', start_date=startdate, end_date=enddate)
        # exchange='', list_status='L' , fields='ts_code,symbol,name,area,industry,list_date')
        #实际导出的列有：ts_code,symbol,name,area,industry,cnspell,market,list_date,act_name,act_ent_type
        df['cal_date'] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
        df['pretrade_date'] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d").dt.date
        df.rename(columns={"cal_date": "trade_date"},inplace=True)
        
        df = df.where(pd.notna(df), None)
        df = df.replace({np.nan: None})  # 替换所有 NaN 为 None
        self.db.bulkInsUpd(table='ts_trading_dates',df=df)
    def get_stock_list(self):
        """获取股票列表清单"""
        df = self.pro.stock_basic(fields=f"""ts_code,symbol,name,area,industry,fullname,enname,cnspell,market,exchange,curr_type,list_status,
            list_date,delist_date,is_hs,act_name,act_ent_type""") 
        # exchange='', list_status='L' , fields='ts_code,symbol,name,area,industry,list_date')
        #实际导出的列有：ts_code,symbol,name,area,industry,cnspell,market,list_date,act_name,act_ent_type
        df['list_date'] = pd.to_datetime(df["list_date"], format="%Y%m%d").dt.date
        df['delist_date'] = pd.to_datetime(df["delist_date"], format="%Y%m%d").dt.date

        df = df.where(pd.notna(df), None)
        df = df.replace({np.nan: None})  # 替换所有 NaN 为 None
        #列名称：['ts_code', 'symbol', 'name', 'area', 'industry', 'fullname', 'enname','cnspell', 'market', 
        #'exchange', 'curr_type', 'list_status','list_date', 'delist_date', 'is_hs', 'act_name', 'act_ent_type']
        self.db.bulkInsUpd(table='ts_stock_basic',df=df)
        
    def get_daily_deal(self,start_date_str=None,end_date_str=None,force=0):
        """
        从最大的交易日期开始，然后直到当前日期,格式:%Y-%m-%d
        Args:
            start_date_str (str): 开始日期字符串，格式 'YYYY-MM-DD'。插入的日期是这个日期的下一个交易日期
            end_date_str :结束日期字符串，格式YYYY-MM-DD
            force: 1表示强制更新从startdate 到enddate的交易数据
        Returns:
            list: 交易日期字符串列表，格式 'YYYY-MM-DD'。
        """
        # current_date = pd.to_datetime(start_date_str)
        # current_date_str = current_date.strftime("%Y-%m-%d")
        # tody = pd.to_datetime(date.today() )
        # tody_str = tody.strftime("%Y-%m-%d")
        if start_date_str is None: start_date_str = self.max_trade_date.strftime("%Y-%m-%d")
        if end_date_str is None:
            end_date_str = ''
        else:
            end_date_str = f"and td.trade_date <= '{end_date_str}' "
        if force == 0:
            df = self.db.qry2df(f"""select td.trade_date,td.pretrade_date
                from jhd.ts_trading_dates td left join jhd.ts_stock_daily_his tsd on td.trade_date = tsd.trade_date
                where td.trade_date >= '{start_date_str}' {end_date_str} and td.is_open=1
                and tsd.trade_date is NULL""")
        else:
            df = self.db.qry2df(f"""select td.trade_date,td.pretrade_date
                from jhd.ts_trading_dates td
                where td.trade_date >= '{start_date_str}' {end_date_str} and td.is_open=1""")

        # df = pro.daily(ts_code='001283.SZ', start_date='20260320', end_date='20260323')
        #或者：
        # df = pro.query('daily', ts_code='000001.SZ', start_date='20180701', end_date='20180718')
        #多个股票
        # df = pro.daily(ts_code='001283.SZ,600000.SH', start_date='20180701', end_date='20180718')
        
        #也可以通过日期取历史某一天的全部历史
        # df = pro.daily(trade_date='20260324')
        df['trade_date'] = pd.to_datetime(df.trade_date)
        df['trade_date'] = df["trade_date"].dt.strftime("%Y%m%d")
        for i in range(len(df)):
            print(f"获取日期：{df.iloc[i,0]}的数据")
            df2 = self.pro.daily(trade_date=df.iloc[i,0],
                                 fields='ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount,ah_vol,ah_amount')
            df2.rename(columns={"open":"open_price","close":"close_price","change":"change_amount"},inplace=True)
            df2['trade_date'] = pd.to_datetime(df2['trade_date'],format='%Y%m%d').dt.date
            df2 = df2.where(pd.notna(df2), None)
            df2 = df2.replace({np.nan: None})  # 替换所有 NaN 为 None
            if len(df2) >= 1:
                self.db.bulkInsUpd(table='ts_stock_daily_his',df=df2)
        
        print('完成数据插入')
        return 0

async def run_qq(qq,again=0):
    if again == 0:
        await qq.fetch_getBoardRankList_db()
        await qq.fetch_getRank_db()
        # 下面是两个任务并行跑
        # task1 = qq.fetch_getBoardRankList_db()
        # task2 = qq.fetch_getRank_db()
        # await asyncio.gather(task1, task2)
        sql = f"""'{qq.trade_date.strftime("%Y-%m-%d")}'"""
        sql = f"""select DISTINCT qhh.code_qqhy,qhh.name_hy
        from qq_hy_hsj AS qhh where trade_date='{qq.trade_date.strftime("%Y-%m-%d")}'
        and not EXISTS (SELECT 1 FROM qq_hy_detail AS qhd
        WHERE qhd.code_qqhy = qhh.code_qqhy and date(qhd.update_time) = qhh.trade_date)"""
        df = qq.db.qry2df(sql)
        df = df.iloc[np.random.permutation(len(df))].reset_index(drop=True)
        await qq.fetch_xg_plate_stocks_db(df=df)
    elif again == 1:
        sql = f"""'{qq.trade_date.strftime("%Y-%m-%d")}'"""
        sql = f"""select DISTINCT qhh.code_qqhy,qhh.name_hy
        from qq_hy_hsj AS qhh where trade_date='{qq.trade_date.strftime("%Y-%m-%d")}'
        and not EXISTS (SELECT 1 FROM qq_hy_detail AS qhd
        WHERE qhd.code_qqhy = qhh.code_qqhy and date(qhd.update_time) = qhh.trade_date)"""
        df = qq.db.qry2df(sql)
        df = df.iloc[np.random.permutation(len(df))].reset_index(drop=True)
        await qq.fetch_xg_plate_stocks_db(df=df)


#endregion
if __name__ == "__main__":
    #下面是获取交易日历，股票清单，日交易数据
    ts = Myts()
    # ts.get_trade_calendar() #获取交易日历
    # ts.get_stock_list()     #获取A股清单
    # ts.get_daily_deal()     #获取日交易数据
    
    qq = Myqq()
    # asyncio.run(run_qq(qq=qq,again=0))
    # qq.fetch_quotes_db(codes=None)
    # df = qq.fetch_plate_stocks_df(plate_code_ori="pt01801124") #pt01801111
    # df = qq.fetch_plate_stocks_df(plate_code_ori="pt01801111") #pt01801111
    df = pd.DataFrame({"code_qqhy":['pt01801082','pt01801111'],"name_hy":['其他电子Ⅱ','白色家电']})
    qq.fetch_plate_stocks_db()


    embed()  # 进入交互式调试环境，方便你手动操作浏览器










