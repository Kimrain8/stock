import json
import subprocess
import os
import sys
import pandas as pd
import pandas as pd
import mplfinance as mpf
from typing import Optional
import matplotlib.pyplot as plt
# ========= 全局设置中文字体（放在代码最开头，只需要执行一次） =========
plt.rcParams["font.sans-serif"] = ["SimHei"]  #黑体
plt.rcParams["axes.unicode_minus"] = False    #解决负号变成方框

import requests
from datetime import datetime,date,timedelta
import numpy as np
import re
import hashlib, time

import akshare as ak
import tushare as ts

import asyncio #运行异步函数需要 asyncio.run() 来执行
from playwright.async_api import async_playwright,TimeoutError as PlaywrightTimeoutError #使用异步方式连接到已启动的chrome浏览器
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator #将HTML内容解析成markdown格式
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig,CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter #配合 DefaultMarkdownGenerator 使用，过滤掉不需要的内容
# from crawl4ai.content_extractor import ContentExtractor #有问题，暂时不使用
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy #将网页内容提取成json格式
from types import SimpleNamespace #创建一个简单的对象，方便存储和访问属性
from urllib.parse import quote, urlencode, urlparse, parse_qs

from IPython import embed
from utils.dbManager import MySQLHelper

class Myqq():
    def __init__(self):
        self.db = MySQLHelper(db='jhd')
        self.trade_date = pd.Timestamp.now().date()
    @staticmethod
    def get_qq_sign(plate_code, exchange=12, sort_type=1, source='zxg', stocks_type=3, user_type=4, t=int(time.time() * 1000)):
        """获取行业对应的股票明细api,生成需要的动态sign值, plate_code只含数字
        引用：
        t = int(time.time() * 1000)  # 毫秒时间戳
        sign = gen_sign("12", "01801733", "1", "zxg", "3", "4", t)"""
        params = {
            "exchange": exchange,
            "plate_code": plate_code,
            "sort_type": sort_type,
            "source": source,
            "stocks_type": stocks_type,
            "time": t,
            "user_type": user_type,
        }
        s = "&".join(f"{k}={v}" for k, v in params.items())
        s += "&key=7ad247390dafce0cf9911de0f2083eba"
        return hashlib.md5(s.encode()).hexdigest()
    async def get_qq_getBoardRankList(self,page, board_code = "aStock",offset: int = 0, count: int = 20):
        """获取腾讯 hsj股票数据,使用page环境的request,直接调用api接口"""
        base_url = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
        params = {
            "_appver": "11.17.0",
            "board_code": board_code,
            "sort_type": "price",
            "direct": "down",
            "offset": offset,
            "count": count,
            "_t": int(asyncio.get_event_loop().time() * 1000)
        }
        url = f"{base_url}?{urlencode(params)}"
        headers = {
            "Referer": "https://stockapp.finance.qq.com", #page.evaluate("()=>document.referrer")
            "Origin": "https://stockapp.finance.qq.com", #page.evaluate("() => window.origin")
            "User-Agent": await page.evaluate("()=>navigator.userAgent")
        }
        resp = await page.request.get(url, headers=headers)
        if resp.ok:
            return await resp.json()
        return {}
    async def fetch_getBoardRankList_df(self,board_code = "aStock"):
        """获取腾讯hsj股票数据,对应get_qq_getBoardRankList,先分批获取全部数据,整合到一个df中,
        board_code: aStock, cyb,ksh"""
        async with async_playwright() as p:
            # browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            # context = browser.contexts[0]
            # page = None
            context = await p.chromium.launch_persistent_context(
                user_data_dir="./chrome_user_data",headless=True)
            page = await context.new_page()
            
            if board_code == "aStock":
                web_url = "https://stockapp.finance.qq.com/mstats/#mod=list&id=hs_hsj&module=hs&type=hsj"
            elif board_code == "cyb":
                web_url = "https://stockapp.finance.qq.com/mstats/#mod=list&id=hs_cyb&module=hs&type=cyb"
            elif board_code == "ksh":
                web_url = "https://stockapp.finance.qq.com/mstats/#mod=list&id=hs_kcb&module=hs&type=kcb"
            # for i in context.pages:
            #     # i.url.split("#")[0]   # 去掉参数部分
            #     if i.url == web_url:
            #         page = i
            #         break
            # if page is None:
            #     # raise RuntimeError("未找到目标页面，请确保已打开 https://stockapp.finance.qq.com/mstats/")
            #     print("未找到目标页面，请确保已打开 https://stockapp.finance.qq.com/mstats/")
            #     page = await browser.new_page()
            #     await page.goto(web_url)
            #     await page.wait_for_selector("div#page-navi>a#p-1", state="visible", timeout=10000)
            await page.goto(web_url)
            await page.wait_for_selector("div#page-navi>a#p-1", state="visible", timeout=10000)
            
            # 主动调用接口，offset=40 count=20，对应你的curl示例
            api_data = await self.get_qq_getBoardRankList(page, board_code=board_code,offset=0, count=100)
            rank_list = api_data.get("data", {}).get("rank_list", [])
            df = pd.DataFrame(rank_list)
            cnt = api_data.get('data').get('total')
            cnt = cnt // 100 + (1 if cnt % 100 > 0 else 0)  # 计算总页数，每页100条
            for i in range(1, cnt):
                api_data = await self.get_qq_getBoardRankList(page, board_code=board_code, offset=i*100, count=100)
                rank_list = api_data.get("data", {}).get("rank_list", [])
                df2 = pd.DataFrame(rank_list)
                df = pd.concat([df, df2], ignore_index=True)
            return df
    async def fetch_getBoardRankList_db(self):
        """获取沪深京个股数据"""
        df = await self.fetch_getBoardRankList_df(board_code="aStock") #获取腾讯 BoardRankList,hsj个股数据
        df.rename(columns={"code": "qq_code","zdf":"pct_chg","zd":"change_amount",
                            "volume":"vol","turnover":"amount","stock_type":"market"}, inplace=True)
        df["symbol"] = df['qq_code'].str.extract(r'(\d+)')
        df["trade_date"] = self.trade_date
        self.db.bulkInsUpd(df,table="qq_hsj")
        # df = await self.fetch_getBoardRankList_df(board_code="cyb") #获取腾讯 BoardRankList,创业板个股数据
        # df.rename(columns={"code": "qq_code","zdf":"pct_chg","zd":"change_amount",
        #                     "volume":"vol","turnover":"amount","stock_type":"market"}, inplace=True)
        # df["symbol"] = df['qq_code'].str.extract(r'(\d+)')
        # df["trade_date"] = self.trade_date
        # self.db.bulkInsUpd(df,table="qq_hsj")
        # df = await self.fetch_getBoardRankList_df(board_code="ksh") #获取腾讯 BoardRankList,科创板个股数据
        # df.rename(columns={"code": "qq_code","zdf":"pct_chg","zd":"change_amount",
        #                     "volume":"vol","turnover":"amount","stock_type":"market"}, inplace=True)
        # df["symbol"] = df['qq_code'].str.extract(r'(\d+)')
        # df["trade_date"] = self.trade_date
        # self.db.bulkInsUpd(df,table="qq_hsj")
    
    async def get_qq_getRank(self,page,board_type="hy2", offset: int = 0, count: int = 20):
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
            "_t": int(asyncio.get_event_loop().time() * 1000)
        }
        url = f"{base_url}?{urlencode(params)}"
        headers = {
            "Referer": "https://stockapp.finance.qq.com", #page.evaluate("()=>document.referrer")
            "Origin": "https://stockapp.finance.qq.com", #page.evaluate("() => window.origin")
            "User-Agent": await page.evaluate("()=>navigator.userAgent")
        }
        resp = await page.request.get(url, headers=headers)
        if resp.ok:
            return await resp.json()
        return {}
    async def fetch_getRank_df(self,board_type="hy2"):
        """获取腾讯行业数据,对应get_qq_getRank,先分批获取全部数据,整合到一个df中"""
        async with async_playwright() as p:
            # browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            # context = browser.contexts[0]
            # page = None
            # for i in context.pages:
            #     # i.url.split("#")[0]   # 去掉参数部分
            #     if i.url == "https://stockapp.finance.qq.com/mstats/#mod=list&id=hy_second&module=hy&type=second":
            #         page = i
            #         break
            context = await p.chromium.launch_persistent_context(
                user_data_dir="./chrome_user_data",headless=True
            )
            page = await context.new_page()
            await page.goto("https://stockapp.finance.qq.com/mstats/#mod=list&id=hy_second&module=hy&type=second")
            await page.wait_for_selector("div#page-navi>a#p-1", state="visible", timeout=10000)

            # 主动调用接口，offset=40 count=20，对应你的curl示例
            api_data = await self.get_qq_getRank(page,board_type=board_type, offset=0, count=100)
            rank_list = api_data.get("data", {}).get("rank_list", [])
            df = pd.DataFrame(rank_list)
            cnt = api_data.get('data').get('total')
            cnt = cnt // 100 + (1 if cnt % 100 > 0 else 0)  # 计算总页数，每页100条
            for i in range(1, cnt):
                api_data = await self.get_qq_getRank(page,board_type=board_type, offset=i*100, count=100)
                rank_list = api_data.get("data", {}).get("rank_list", [])
                df2 = pd.DataFrame(rank_list)
                df = pd.concat([df, df2], ignore_index=True)
            return df
    async def fetch_getRank_db(self):
        """获取行业（板块）的整体变化"""
        df = await self.fetch_getRank_df(board_type="hy2") #获取行业数据
        df.rename(columns={"code": "code_qqhy","name":"name_hy","stock_type":"type_hy","turnover":"amount","volume":"vol","zd":"change_amount",
                        "zdf":"pct_chg"}, inplace=True)
        df["trade_date"] = self.trade_date #- pd.Timedelta(days=1)
        df["symbol_hy"] = df['code_qqhy'].str.extract(r'(\d+)')
        df['lzg'] = df["lzg"].apply(lambda x: json.dumps(x, ensure_ascii=False))
        self.db.bulkInsUpd(df,table="qq_hy_hsj")
    async def fetch_xg_plate_stocks_df(self,plate_code_ori, plate_name,browser ):
        """获取行业（板块）下的个股详情"""
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


def mySearchDf(df, target, regex=False):
    """
    在整个 DataFrame 中查找 target。
    参数:
        target : 查找内容
        regex  : False → 精确匹配（两边都转成 str 再比较）
                 True  → 把 target 当成正则表达式（re.search 语义），
                         也就是你直接传 r'string' 里的那个 string
    返回: list，每项是单键 dict：
        列名命中 → {列名: [前3行的值]}          # 不足3行返回全部
        值命中   → {列名: [行索引, 值]}
    """
    results = []

    def col_hit(name):
        s = str(name)
        return re.search(target, s) is not None if regex else s == str(target)

    # 1) 列名命中
    for col in df.columns:
        if col_hit(col):
            results.append({col: df[col].head(3).tolist()})

    # 2) 值命中
    for col in df.columns:
        if regex:
            mask = df[col].astype(str).str.contains(target, regex=True, na=False)
        else:
            mask = df[col].astype(str) == str(target)
        for idx in df.index[mask]:
            results.append({col: [idx, df.at[idx, col]]})

    return results
def myJson2txt(jfile,txtfile):
    """将复杂的json文件进行解读后保存到文本文件"""
    def save_dict_to_text(obj, file_path: str, encoding="utf-8"):
        """把嵌套dict结构保存到txt文件，保留原生换行"""
        def dump_struct_text(obj, indent=0):
            """递归生成带缩进的结构化文本字符串，字符串内\n保留原生换行"""
            lines = []
            pad = "  " * indent
            if isinstance(obj, dict):
                if not obj:
                    lines.append(f"{pad}(empty dict)")
                else:
                    for k, v in obj.items():
                        lines.append(f"{pad}===== {k} =====")
                        sub_lines = dump_struct_text(v, indent + 1)
                        lines.extend(sub_lines)
            elif isinstance(obj, list):
                if not obj:
                    lines.append(f"{pad}(empty list)")
                else:
                    for idx, item in enumerate(obj):
                        lines.append(f"{pad}[{idx}]")
                        sub_lines = dump_struct_text(item, indent + 1)
                        lines.extend(sub_lines)
            elif obj is None:
                lines.append(f"{pad}(None)")
            else:
                # 字符串直接加入，自带的\n会保留，写入文件自动换行
                val_str = str(obj)
                # 这里不要做replace("\n",...)，保留原文段落换行
                lines.append(f"{pad}{val_str}")
            return lines
        
        
        all_lines = dump_struct_text(obj)
        content = "\n".join(all_lines)
        with open(file_path, "w", encoding=encoding) as f:
            f.write(content)
    #"D:/pyprj/TradingAgents/.cache/logs/000921.SZ/TradingAgentsStrategy_logs/full_states_log_2026-09-11.json"
    with open(jfile, "r", encoding="utf-8") as f:
        data = json.load(f)
        save_dict_to_text(data,txtfile) #"./sz000921.txt"
def myAnalyzeInOrder(df,cols):
    j = 0
    for i in range(len(df) -1):
        if df.iloc[i+1,cols[0]] == df.iloc[i+1,cols[0]]:
            if df.iloc[i+1,'zxj'] >= 1:
                pass
        else:
            j=0
    def _analyzeCol(col):
        
        pass
    pass




def plot_stock_kline(df: pd.DataFrame,title: Optional[str] = None, show_volume: bool = True, show_zljlr: bool = True,
    figsize: tuple = (14, 8),
    save_path: Optional[str] = None,
    style="charles"):
    """
    绘制单只股票日K线（真实蜡烛K线，适配新字段：open_price,close_price,high,low）
    :param df: 单股票DataFrame，必须包含 trade_date, open_price,close_price,high,low,vol,zljlr
    :param title: 图表标题，不传自动生成
    :param show_volume: 是否绘制成交量副图
    :param show_zljlr: 是否绘制主力净流入副图
    :param figsize: 画布大小
    :param save_path: 图片保存路径，None直接弹窗展示
    :param style: mplfinance 主题
    """
    df = df.copy()
    # 校验：只能传入单只股票
    if df["symbol"].nunique() > 1:
        raise ValueError("仅支持单股票绘图，请先groupby取出单股票数据！")

    # 日期转换 + 按交易日期排序
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date", ignore_index=True)

    # ---------------------- 数据清洗：转为数值，剔除空值 ----------------------
    numeric_cols = ["open_price", "close_price", "high", "low", "zxj", "vol", "zljlr"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 价格四要素任意为空直接丢弃该行
    df = df.dropna(subset=["open_price", "close_price", "high", "low"])
    if len(df) <= 1:
        raise ValueError("有效行情数据不足，无法绘图！")

    # 重命名列，适配mplfinance固定列名要求
    df["Open"] = df["open_price"]
    df["High"] = df["high"]
    df["Low"] = df["low"]
    df["Close"] = df["close_price"]
    df["Volume"] = df["vol"]

    # 设置时间索引（mplfinance要求index为datetime）
    df = df.set_index("trade_date")

    add_plots = []
    panel = 1
    if show_zljlr:
        add_plots.append(
            mpf.make_addplot(df["zljlr"], panel=panel, ylabel="主力净流入", color="#1f77b4")
        )
        panel += 1

    if title is None:
        title = f"{df['name'].iloc[0]}({df['symbol'].iloc[0]}) 日K线"

    plot_kwargs = {
        "data": df[["Open", "High", "Low", "Close", "Volume"]],
        "type": "candle",
        "title": title,
        "volume": show_volume,
        "addplot": add_plots,
        "figsize": figsize,
        "style": style,
    }
    # 只有路径不为None才传入savefig，规避之前的报错
    if save_path is not None:
        plot_kwargs["savefig"] = save_path

    mpf.plot(**plot_kwargs)



def plot_stock_line_kline(
    df: pd.DataFrame,
    title: Optional[str] = None,
    save_path: Optional[str] = None,
    figsize=(16,9)
):
    """
    纯matplotlib绘制单股票时序图：价格、成交量、主力净流入
    不需要mplfinance，不需要OHLC，适配你当前数据结构
    :param df: 单股票DataFrame
    :param title: 图表标题
    :param save_path: 图片保存路径，None直接展示
    :param figsize: 画布大小
    """
    df = df.copy()
    if df["symbol"].nunique() > 1:
        raise ValueError("仅支持单只股票绘图，请传入单股票数据！")

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date", ignore_index=True)

    # 3个子图：价格 / 成交量 / 主力净流入
    fig, axes = plt.subplots(3, 1, figsize=figsize, sharex=True)

    # 1. 价格图 zxj最新价
    ax1 = axes[0]
    ax1.plot(df["trade_date"], df["zxj"], color="#E63946", linewidth=1.5, label="最新价")
    ax1.set_title(title or f"{df['name'].iloc[0]}({df['symbol'].iloc[0]}) 日行情", fontsize=14)
    ax1.set_ylabel("价格")
    ax1.grid(alpha=0.3)
    ax1.legend()

    # 2. 成交量 vol
    ax2 = axes[1]
    colors = ["#E63946" if c>0 else "#457B9D" for c in df["pct_chg"]]
    ax2.bar(df["trade_date"], df["vol"], color=colors, alpha=0.7)
    ax2.set_ylabel("成交量")
    ax2.grid(alpha=0.3)

    # 3. 主力净流入 zljlr
    ax3 = axes[2]
    colors_zlj = ["#E63946" if v>0 else "#457B9D" for v in df["zljlr"]]
    ax3.bar(df["trade_date"], df["zljlr"], color=colors_zlj, alpha=0.7)
    ax3.set_ylabel("主力净流入")
    ax3.set_xlabel("日期")
    ax3.grid(alpha=0.3)
    plt.xticks(rotation=30)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()




if __name__ == "__main__":
    # if len(sys.argv) < 3:
    #     print("用法：python py文件.py gpcode findstr")
    #     sys.exit(1)
    # codes= sys.argv[1]
    # findstr = sys.argv[2]
    # rege = int(sys.argv[3]) if len(sys.argv) == 4 else 0
    qq = Myqq()
    
    # df = qq.get_qq_quotes_df(codes=codes)
    # print(mySearchDf(df=df,target=findstr,regex=bool(rege)))
    # cols = qq.db.lookUpColName(df)
    # c = [x + y for x, y in zip(cols, df.columns)]
    # df.columns = c
    # with pd.option_context('display.max_rows', None, 'display.max_columns', None):
    #     print(df.T)
    # html = asyncio.run(qq.fetch_qt_web())
    # print('1')

    # 使用示例
    sql = f"""select qh.trade_date, qh.qq_code, qh.symbol, qh.name, qh.zxj, tsdh.open_price,tsdh.close_price,tsdh.high,tsdh.low,tsdh.pre_close,
qh.pct_chg, qh.change_amount, qh.hsl, qh.zljlr, qh.lb, qh.zf, qh.vol, qh.amount, qh.ltsz, qh.zsz, 
qh.pe_ttm, qh.pn, qh.zdf_d5, qh.zdf_d10, qh.zdf_d20, qh.zdf_d60, qh.zdf_w52, qh.zdf_y, qh.zllr, 
qh.zllc, qh.zllr_d5, qh.zllc_d5, qh.market, qh.state, qh.speed, qh.update_time 
from qq_hsj AS qh left join ts_stock_basic tsb on qh.symbol  = tsb.symbol 
left join ts_stock_daily_his AS tsdh on tsb.ts_code = tsdh.ts_code and qh.trade_date = tsdh.trade_date"""
    df = qq.db.qry2df(sql)
    
    group_df = df[df["symbol"]=="000608"]
    plot_stock_kline(group_df)
    
    # 模拟数据，和你爬取结构完全一致
    # import numpy as np
    # plot_stock_line_kline(df[df["symbol"]=="000608"])
    
    # embed()