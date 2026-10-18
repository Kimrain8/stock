import random
import pandas as pd
import requests
from requests.exceptions import RequestException, ProxyError, Timeout
from datetime import datetime,date,timedelta
import numpy as np
import hashlib, time
from urllib.parse import quote, urlencode, urlparse, parse_qs
from typing import Optional, List
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import base64

import akshare as ak
import asyncio #运行异步函数需要 asyncio.run() 来执行
from playwright.async_api import async_playwright,TimeoutError as PlaywrightTimeoutError #使用异步方式连接到已启动的chrome浏览器
from playwright.sync_api import sync_playwright

from IPython import embed
from utils.dbManager import MySQLHelper

class Myak():
    def __init__(self):
        self.db = MySQLHelper(db='jhd')
        self.trade_date = pd.Timestamp.now().date()
        self._ENKEY_CACHE = {"value": None, "time": 0.0}
        self._ENKEY_TTL = 50.0
        self.BASE_URL = "https://webapi.cninfo.com.cn"
    def get_enckey(self,force: bool = False) -> str:
        """
        获取接口鉴权令牌 Accept-EncKey 的值（= window.indexcode.getResCode()）。
        带 50 秒本地缓存，避免频繁启动浏览器。
        :param force: 强制重新获取（忽略缓存）
        :return: 令牌字符串
        """
        if not force:
            cached = self._ENKEY_CACHE.get("value")
            if cached and (time.time() - self._ENKEY_CACHE.get("time", 0)) < self._ENKEY_TTL:
                return cached

        launch_kwargs = {"headless": True}
        PAGE_URL = self.BASE_URL + "/#/dataBrowse"                 # 用于取令牌的页面
        
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
                page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3000)   # 等待反爬脚本注入完成
                token = page.evaluate("window.indexcode.getResCode()")
            finally:
                browser.close()

        if not token:
            raise RuntimeError("获取 Accept-EncKey 令牌失败：页面未注入 indexcode.getResCode")

        self._ENKEY_CACHE["value"] = token
        self._ENKEY_CACHE["time"] = time.time()
        return token
    def get_enckey_gen(self,force: bool = False) -> str:
        now = time.time()
        if (not force and self._ENKEY_CACHE["value"] and now - (self._ENKEY_CACHE["time"] or 0) < 5400):
            return self._ENKEY_CACHE["value"]
        key = iv = b"1234567887654321"
        plain = str(int(now)).encode()
        cipher = AES.new(key, AES.MODE_CBC, iv)
        token = base64.b64encode(cipher.encrypt(pad(plain, 16))).decode()
        self._ENKEY_CACHE["value"], self._ENKEY_CACHE["time"] = token, now
        return token

    def _request(self,data_url: str, params: dict, method: str = "GET",timeout: int = 30, proxy_pool: list = None,
                    max_retries: int = 3, retry_sleep: tuple = (2, 6)):
        """按 method（"GET"/"POST"）请求一个 sysapi 并返回 records 列表；失败返回 -1。"""
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
            bad_proxy = self._BAD_PROXY
            alive = [p for p in proxy_pool if bad_proxy.get(p, 0) < 3] or proxy_pool

        if not self._ENKEY_CACHE.get("value"):
            enckey = self.get_enckey_gen()
        else:
            enckey = self._ENKEY_CACHE.get("value")

        def _standard_headers() -> dict:
            """构造与 get_ak_dividend 一致的请求头。"""
            return {
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0 Safari/537.36"),
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": self.BASE_URL + "/",
                "Accept-EncKey": enckey,
            }
        def _do_request():
            last_exc = None
            for attempt in range(max_retries):
                proxy = random.choice(alive) if alive else None
                proxies = {"http": proxy, "https": proxy} if proxy else None
                try:
                    with requests.Session() as session:
                        session.headers.update(_standard_headers())
                        resp = session.request(method, data_url, params=params,
                                               timeout=timeout, proxies=proxies)
                        resp.raise_for_status()
                        return resp.json()
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

    def get_ak_companyinfo(self, symbol=None, proxy_pool: list = None,method="GET"):
        """ 使用_request 函数
        调用巨潮 p_sysapi1018 业务接口，返回 pandas.DataFrame。
        :param symbol:      多个证券代码，如 "000408,300750"
        :param timeout:     请求超时（秒）
        :param proxy_pool:  代理列表。默认使用本机 SSH 动态转发的 SOCKS5 代理 socks5h://127.0.0.1:9988
        :param max_retries: 单次请求最大重试次数（每次都会随机等待）
        :param retry_sleep: 重试间隔（秒），随机区间 (min, max)
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1018"      # 业务数据接口

        data = self._request(data_url=DATA_URL,params={"scode":symbol},method=method,proxy_pool=proxy_pool)
        if not isinstance(data, dict) or "records" not in data:
            return -1

        data = pd.DataFrame(data['records'])
        data.rename(columns={"SECCODE":"symbol","SECNAME":"name","ORGNAME":"fullname","F001V":"market","F003V":"exchange","F002D":"list_date",
"F005D":"del_Date","F004V":"list_status","F009D":"estab_date","F007V":"enname","F008V":"enshort","F006V":"isin_code",
"F011V":"act_name","F029V":"Chairman","F031V":"ceo","F032V":"secretary","F036V":"stock_rep","F030V":"indep_dir",
"F015V":"area","F016V":"city","F017V":"reg_adrs","F018V":"off_adrs","F019V":"zipcode","F020V":"phone","F021V":"fax",
"F023V":"website","F022V":"email","F024V":"camac_l1","F025V":"camac_l2","F026V":"sw_l1","F027V":"name_hy",
"F028V":"sw_l3","F012V":"main_bus","F013V":"bus_scope","F014V":"cmpy_desc","F010N":"reg_capital","F037V":"ext_aud",
"F038V":"law_firm","F033V":"sec_phone","F034V":"sec_fax","F035V":"sec_email"},inplace=True)
        
        for col in ("list_date", "estab_date"):
            if col in data.columns:
                data[col] = pd.to_datetime(data[col], format="%Y-%m-%d",errors="coerce").dt.date
        if len(data)>=1:
            try:
                self.db.bulkInsUpd(df=data, table="ak_stock_basic")
                #下面更新my_choice_stock
                key = data['exchange'].str.extract(r'^(上交所|深交所|北交所)', expand=False)
                ts_map = {'上交所': '.SH', '深交所': '.SZ', '北交所': '.BJ'}
                qq_map = {'上交所': 'sh',  '深交所': 'sz',  '北交所': 'bj'}
                data = data[['symbol','name','camac_l1','camac_l2','sw_l1','name_hy','sw_l3']]
                data['qq_code'] = key.map(qq_map) + data['symbol'].astype(str)
                data['ts_code'] = data['symbol'].astype(str) + key.map(ts_map)
                self.db.bulkInsUpd(df=data, table="my_choice_stock")
            except Exception as e:
                data.to_pickle('error.pkl')
                return -1
        return 0
    def get_ak_companyinfo2(self, symbol, timeout: int = 30,proxy_pool: list = None, max_retries: int = 3,
                       retry_sleep: tuple = (2, 6)):
        """ 不使用_request
        调用巨潮 p_sysapi1018 业务接口，返回 pandas.DataFrame。

        :param symbol:      证券代码，如 "000408", "300750"
        :param timeout:     请求超时（秒）
        :param proxy_pool:  代理列表。默认使用本机 SSH 动态转发的 SOCKS5 代理 socks5h://127.0.0.1:9988
        :param max_retries: 单次请求最大重试次数（每次都会随机等待）
        :param retry_sleep: 重试间隔（秒），随机区间 (min, max)
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1018"      # 业务数据接口
        # ---------- 1. 代理池：默认走 SSH 转发的本地 SOCKS5 ----------
        if proxy_pool is None:
            proxy_pool = getattr(self, "proxy_pool", None)
            # proxy_pool = ["socks5h://127.0.0.1:9988"]
        if proxy_pool is None:
            alive=None
            single_proxy = None
            bad_proxy=None
        else:
            # 单条代理时不做"剔除"逻辑，否则一失败就池子空了
            single_proxy = len(proxy_pool) == 1
            bad_proxy = getattr(self, "_BAD_PROXY", {})
            self._BAD_PROXY = bad_proxy
            alive = [p for p in proxy_pool if bad_proxy.get(p, 0) < 3] or proxy_pool

        # ---------- 2. 获取 enckey ----------
        if not self._ENKEY_CACHE.get("value"):
            enckey = self.get_enckey_gen()
        else:
            enckey = self._ENKEY_CACHE.get("value")

        # ---------- 3. 请求（SOCKS5 代理 + 重试 + 随机等待） ----------
        def _do_request(enc_key: str):
            last_exc = None
            for attempt in range(max_retries):
                proxy = random.choice(alive) if alive else None
                proxies = {"http": proxy, "https": proxy} if proxy else None

                headers = {
                    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/120.0 Safari/537.36"),
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Referer": self.BASE_URL + "/",
                    "Accept-EncKey": enc_key,
                }

                try:
                    with requests.Session() as session:
                        session.headers.update(headers)
                        resp = session.get(
                            f"{DATA_URL}?scode={symbol}",
                            timeout=timeout,
                            proxies=proxies,
                        )
                        resp.raise_for_status()
                        return resp.json()

                except (ProxyError, Timeout) as e:
                    if not single_proxy:
                        bad_proxy[proxy] = bad_proxy.get(proxy, 0) + 1
                        alive[:] = [p for p in alive if bad_proxy.get(p, 0) < 3] or proxy_pool
                    last_exc = e
                except RequestException as e:
                    last_exc = e

                # 重试前随机等待 2~6 秒
                if attempt < max_retries - 1:
                    wait = random.uniform(*retry_sleep)
                    print(f"[重试 {attempt + 1}/{max_retries}] 等待 {wait:.2f}s …")
                    time.sleep(wait)

            raise last_exc if last_exc else RuntimeError("request failed")
        
        data = _do_request(enc_key=enckey)
        # ---------- 4. 令牌失效重试（换 key，同样走代理） ----------
        if isinstance(data, dict) and data.get("resultcode") in (401, 451):
            # 令牌失效也随机等待一下，避免立刻打脸
            time.sleep(random.uniform(*retry_sleep))
            new_key = self.get_enckey_gen(force=True)
            data = _do_request(enc_key=enckey)
        if not isinstance(data, dict) or "records" not in data:
            return -1

        data = pd.DataFrame(data['records'])
        data.rename(columns={"SECCODE":"symbol","SECNAME":"name","ORGNAME":"fullname","F001V":"market","F003V":"exchange","F002D":"list_date",
"F005D":"del_Date","F004V":"list_status","F009D":"estab_date","F007V":"enname","F008V":"enshort","F006V":"isin_code",
"F011V":"act_name","F029V":"Chairman","F031V":"ceo","F032V":"secretary","F036V":"stock_rep","F030V":"indep_dir",
"F015V":"area","F016V":"city","F017V":"reg_adrs","F018V":"off_adrs","F019V":"zipcode","F020V":"phone","F021V":"fax",
"F023V":"website","F022V":"email","F024V":"camac_l1","F025V":"camac_l2","F026V":"sw_l1","F027V":"name_hy",
"F028V":"sw_l3","F012V":"main_bus","F013V":"bus_scope","F014V":"cmpy_desc","F010N":"reg_capital","F037V":"ext_aud",
"F038V":"law_firm","F033V":"sec_phone","F034V":"sec_fax","F035V":"sec_email"},inplace=True)
        
        for col in ("list_date", "estab_date"):
            if col in data.columns:
                data[col] = pd.to_datetime(data[col], format="%Y-%m-%d",errors="coerce").dt.date
        if len(data)>=1:
            try:
                self.db.bulkInsUpd(df=data, table="ak_stock_basic")
            except Exception as e:
                data.to_pickle('error.pkl')
                return -1
        return 0
    def fetch_ak_companyinfo(self,df2=None):
        "批量获取巨潮的公司基本信息，纯数字编码"
        if df2 is None:
            df2 = self.db.qry2df(f"select distinct tsdh.symbol "
                                f"from qq_hsj AS tsdh "
                                f"where tsdh.trade_date=(select max(qh.trade_date) from qq_hsj AS qh) "
                                f"AND not exists(select 1 from ak_stock_basic AS asb where asb.symbol = tsdh.symbol)"
            )
            df2 = df2.iloc[np.random.permutation(len(df2))].reset_index(drop=True)
        while len(df2)>=1:
            if len(df2) <= 100 : 
                codes = df2['symbol'].to_list()
                df2 = pd.DataFrame(columns=df2.columns)
            else:
                codes = df2.loc[0:99,'symbol'].to_list()
                df2 = df2.loc[100:,:].reset_index(drop=True)
            df = self.get_ak_companyinfo(symbol=','.join(codes)) #proxy_pool=["socks5h://127.0.0.1:9988"]
            if df == -1:
                break
    def get_ak_dividend(self, rdate=None, symbol: str = None, proxy_pool: list = None):
        """
        调用巨潮 p_sysapi1129 分红送配接口，返回 pandas.DataFrame。
        :param rdate:       分红年度（报告期），必填,20261231:年报; 20260630: 半年; 20260930: 三季报
        :param symbol:      可选，证券代码（如 "000408"）。该接口返回全市场，传了则在结果内过滤出该只股票
        :param timeout:     请求超时（秒）
        :param proxy_pool:  代理列表。默认使用 self.proxy_pool（SSH 动态转发 SOCKS5）
        :param max_retries: 单次请求最大重试次数（每次随机等待）
        :param retry_sleep: 重试间隔（秒），随机区间 (min, max)
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1129"      # 分红送配接口

        data = self._request(data_url=DATA_URL,method="POST",params={"rdate":rdate},proxy_pool=proxy_pool)
        if not isinstance(data, dict) or "records" not in data:
            return -1
        df = pd.DataFrame(data["records"])
        # ---------- 5. 中文/字段名 -> 英文简写 ----------
        df.rename(columns={
            "SECCODE": "symbol",      # 股票代码
            "SECNAME": "name",        # 股票简称
            "F001D":  "end_date",     # 分红年度
            "F006D":  "imp_ann_date",# 实施方案公告日期
            "F007V":  "imp_plan_desc",    # 实施方案分红说明
            "F010N":  "stk_bo_rate",  # 送股比例
            "F011N":  "stk_co_rate",# 转增比例
            "F012N":  "cash_div_tax",     # 派息比例(人民币)
            "F018D":  "record_date",   # A股股权登记日
            "F020D":  "ex_date",    # A股除权日
        }, inplace=True)

        # ---------- 6. 日期字段标准化 ----------
        date_cols = [c for c in ("div_year", "impl_ann_date", "rec_date_a", "ex_date_a")
                     if c in df.columns]
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date
        # ---------- 7. 按 symbol 过滤（可选） ----------
        if symbol:
            symbol = str(symbol).zfill(6)
            df = df[df["symbol"] == symbol].reset_index(drop=True)
        if len(df)>=1:
            self.db.bulkInsUpd(df=df,table='ak_dividend')
        return 0
    def get_ak_forecast_increase(self, rdate=None, symbol: str = None, proxy_pool: list = None):
        """
        调用巨潮 p_sysapi1040 业绩预告（业绩大幅上升/预增）接口，返回 pandas.DataFrame。
        :param rdate:       报告期，必填,20261231:年报; 20260630: 半年; 20260930: 三季报
        :param symbol:      可选，证券代码（如 "000408"）。该接口返回全市场，传了则在结果内过滤出该只股票
        :param timeout:     请求超时（秒）
        :param proxy_pool:  代理列表。默认使用 self.proxy_pool(SSH 动态转发 SOCKS5)
        :param max_retries: 单次请求最大重试次数（每次随机等待）
        :param retry_sleep: 重试间隔（秒），随机区间 (min, max)
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1040"   # 业绩预告·业绩大幅上升
        data = self._request(data_url=DATA_URL,method="POST",params={"rdate":rdate},proxy_pool=proxy_pool)
        if not isinstance(data, dict) or "records" not in data:
            return -1
        df = pd.DataFrame(data["records"])

        df.rename(columns={
            "SECCODE":   "symbol",           # 证券代码
            "SECNAME":   "name",             # 证券简称
            "F001D":     "rdate",         # 报告期
            "F003V":     "change_type",      # 业绩变动类型（如 业绩大幅上升/下降/扭亏…）
            "F004V":     "forecast_desc",    # 业绩预告说明
            "F009N":     "change_min",       # 预告净利润变动幅度下限（%）
            "F010N":     "change_max",       # 预告净利润变动幅度上限（%）
            "DECLAREDATE": "declare_date",   # 公告日期
        }, inplace=True)

        # 日期字段标准化（与 get_ak_dividend 一致，用改名后的列名）
        date_cols = [c for c in ("end_date", "declare_date") if c in df.columns]
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date

        # 数值字段标准化
        for col in ("change_min", "change_max"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 按 symbol 过滤（可选）
        if symbol:
            symbol = str(symbol).zfill(6)
            df = df[df["symbol"] == symbol].reset_index(drop=True)
        if len(df)>=1:
            self.db.bulkInsUpd(df=df,table='ak_prepfmc')
        return 0
    def get_ak_forecast_decrease(self, rdate=None, symbol: str = None, proxy_pool: list = None) -> pd.DataFrame:
        """
        调用巨潮 p_sysapi1039 业绩预告（业绩大幅下降/预减）接口，返回 pandas.DataFrame。
        :param rdate:       报告期，如 "20260630"；必填（对应专题统计-业绩报表-业绩预告·预减）
        :param symbol:      可选，证券代码（如 "000408"）。该接口返回全市场，传了则在结果内过滤出该只股票
        :param timeout:     请求超时（秒）
        :param proxy_pool:  代理列表。默认使用 self.proxy_pool（SSH 动态转发 SOCKS5）
        :param max_retries: 单次请求最大重试次数（每次随机等待）
        :param retry_sleep: 重试间隔（秒），随机区间 (min, max)
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1039"   # 业绩预告·业绩大幅下降

        data = self._request(data_url=DATA_URL,method="POST",params={"rdate":rdate},proxy_pool=proxy_pool)
        if not isinstance(data, dict) or "records" not in data:
            return -1
        df = pd.DataFrame(data["records"])

        df.rename(columns={
            "SECCODE":   "symbol",           # 证券代码
            "SECNAME":   "name",             # 证券简称
            "F001D":     "rdate",         # 报告期
            "F003V":     "change_type",      # 业绩变动类型（如 业绩大幅上升/下降/扭亏…）
            "F004V":     "forecast_desc",    # 业绩预告说明
            "F009N":     "change_min",       # 预告净利润变动幅度下限（%）
            "F010N":     "change_max",       # 预告净利润变动幅度上限（%）
            "DECLAREDATE": "declare_date",   # 公告日期
        }, inplace=True)

        # 日期字段标准化（与 get_ak_dividend 一致，用改名后的列名）
        date_cols = [c for c in ("end_date", "declare_date") if c in df.columns]
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date

        # 数值字段标准化
        for col in ("change_min", "change_max"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 按 symbol 过滤（可选）
        if symbol:
            symbol = str(symbol).zfill(6)
            df = df[df["symbol"] == symbol].reset_index(drop=True)
        if len(df)>=1:
            self.db.bulkInsUpd(df=df,table='ak_prepfmc_desc')
        return 0
    def get_ak_gd_num(self, rdate=None, symbol=None, proxy_pool: list = None):
        """
        调用巨潮 p_sysapi1034 股东人数及持股集中度，返回 pandas.DataFrame。
        :param rdate:       报告期，必填,20261231:年报; 20260630: 半年; 20260930: 三季报
        :param symbol:      可选，证券代码（如 "000408"）。该接口返回全市场，传了则在结果内过滤出该只股票
        :param timeout:     请求超时（秒）
        :param proxy_pool:  代理列表。默认使用 self.proxy_pool（SSH 动态转发 SOCKS5）
        :param max_retries: 单次请求最大重试次数（每次随机等待）
        :param retry_sleep: 重试间隔（秒），随机区间 (min, max)
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1034"

        data = self._request(data_url=DATA_URL,method="POST",params={"rdate":rdate},proxy_pool=proxy_pool)
        if not isinstance(data, dict) or "records" not in data:
            return -1
        df = pd.DataFrame(data["records"])
        # ---------- 5. 中文/字段名 -> 英文简写 ----------
        df.rename(columns={
            "SECCODE": "symbol",      # 股票代码
            "SECNAME": "name",        # 股票简称
            "ENDDATE":  "end_date",     # 变动日期
            "F001N":  "gd_num",         # 本期股东人数
            "F002N":  "pre_gd_num",    # 上期股东人数
            "F003N":  "zf_gd_num",  # 股东人数增幅
            "F004N":  "cgsl_avg",# 本期人均持股数量
            "F005N":  "sqcgsl_avg",     # 上期人均持股数量
            "F006N":  "zf_cgsl_avg",   # 人均持股数量增幅
        }, inplace=True)

        # ---------- 6. 日期字段标准化 ----------
        date_cols = [c for c in ("end_date") if c in df.columns]
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date
        # ---------- 7. 按 symbol 过滤（可选） ----------
        if symbol:
            symbol = str(symbol).zfill(6)
            df = df[df["symbol"] == symbol].reset_index(drop=True)
        if len(df)>=1:
            self.db.bulkInsUpd(df=df,table='ak_gd_num')
        return 0
    def get_share_float(self, tdate=None, symbol=None, proxy_pool: list = None):
        """
        https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1024?tdate=2026-10-19
        获取解禁数据 只能输入具体的日期信息。所以获取的对于当前日期的7天之内的数据。
        :param tdate:       解禁日期，必填,2026-10-19
        :param symbol:      可选，证券代码（如 "000408"）。该接口返回全市场，传了则在结果内过滤出该只股票
        :param proxy_pool:  代理列表。默认使用 self.proxy_pool（SSH 动态转发 SOCKS5）
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1024"
        
        def _get_one(tdate):
            data = self._request(data_url=DATA_URL,method="POST",params={"tdate":tdate},proxy_pool=proxy_pool)
            if not isinstance(data, dict) or "records" not in data:
                return -1
            df = pd.DataFrame(data["records"])
            # ---------- 5. 中文/字段名 -> 英文简写 ----------
            df.rename(columns={
                "SECCODE": "symbol",      # 股票代码
                "SECNAME": "name",        # 股票简称
                "DECLAREDATE":  "float_ann_date",     # 公告日期
                "F003D":  "float_date",         # 实际解除限售日期
                "F004N":  "float_share",    # 实际解除限售数量
                "F005N":  "float_ratio",  # 实际解除限售比例
                "F008N":  "ltgb", # 实际可流通数量
            }, inplace=True)
    
            # ---------- 6. 日期字段标准化 ----------
            date_cols = [c for c in ("float_ann_date","imp_ann_date") if c in df.columns]
            for col in date_cols:
                df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date
            
            return df
        
        if tdate is None: 
            date_list = pd.date_range(pd.Timestamp.now().strftime("%Y-%m-%d"),
                                      (pd.Timestamp.now()+ pd.DateOffset(days=7)).strftime("%Y-%m-%d"))
            df = pd.DataFrame({
                'symbol': pd.Series(dtype='string'),
                'name': pd.Series(dtype='string'),
                'float_ann_date': pd.Series(dtype='datetime64[ns]'),
                'float_date': pd.Series(dtype='datetime64[ns]'),
                'float_share': pd.Series(dtype='Float64'),
                'float_ratio': pd.Series(dtype='Float64'),
                'ltgb': pd.Series(dtype='Float64'),
            })
            for i in date_list:
                df2 = _get_one(i.strftime("%Y-%m-%d"))
                if len(df2)>=1:
                    df = pd.concat((df,df2))
        else:
            df = _get_one(tdate=tdate)

        # ---------- 7. 按 symbol 过滤（可选） ----------
        if symbol:
            symbol = str(symbol).zfill(6)
            df = df[df["symbol"] == symbol].reset_index(drop=True)
        if len(df)>=1:
            self.db.bulkInsUpd(df=df,table='ak_share_float')
        return 0
    def get_ak_Reduce(self, sdate=None,edate=None, symbol=None, proxy_pool: list = None):
            """
            https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1027?sdate=2026-09-11&edate=2026-10-11
            获取减持汇总信息 大于等于start_date_str 小于等于end_date_str的所有解禁数据
            :param tdate:       解禁日期，必填,2026-10-19
            :param symbol:      可选，证券代码（如 "000408"）。该接口返回全市场，传了则在结果内过滤出该只股票
            :param proxy_pool:  代理列表。默认使用 self.proxy_pool（SSH 动态转发 SOCKS5）
            :return:            pandas.DataFrame 或 -1
            """
            DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1027"
            if sdate is None:
                sdate = (pd.Timestamp.now() + pd.DateOffset(months=-11)).strftime('%Y-%m-%d')
            if edate is None:
                edate = pd.Timestamp.now().strftime('%Y-%m-%d')
            data = self._request(data_url=DATA_URL,params={"sdate":sdate,"edate":edate},method="POST",proxy_pool=proxy_pool)
            if not isinstance(data, dict) or "records" not in data:
                return -1
            df = pd.DataFrame(data["records"])
            # ---------- 5. 中文/字段名 -> 英文简写 ----------
            df.rename(columns={
                "SECCODE": "symbol",      # 股票代码
                "SECNAME": "name",        # 股票简称
                "DECLAREDATE":  "red_ann_date",     # 公告日期
                "F004N":  "red_vol",         # 减持数量汇总(股)
                "F005N":  "red_ratio",    # 减持比例汇总(%)
            }, inplace=True)
    
            # ---------- 6. 日期字段标准化 ----------
            date_cols = [c for c in ("red_ann_date") if c in df.columns]
            for col in date_cols:
                df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date
            # ---------- 7. 按 symbol 过滤（可选） ----------
            if symbol:
                symbol = str(symbol).zfill(6)
                df = df[df["symbol"] == symbol].reset_index(drop=True)
            if len(df)>=1:
                self.db.bulkInsUpd(df=df,table='ak_reduce_tot')
            return 0
    def get_ak_Increase(self, sdate=None,edate=None, symbol=None, proxy_pool: list = None):
        """
        https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1028?sdate=2026-09-11&edate=2026-10-11
        获取减持汇总信息 大于等于start_date_str 小于等于end_date_str的所有解禁数据
        :param tdate:       解禁日期，必填,2026-10-19
        :param symbol:      可选，证券代码（如 "000408"）。该接口返回全市场，传了则在结果内过滤出该只股票
        :param proxy_pool:  代理列表。默认使用 self.proxy_pool（SSH 动态转发 SOCKS5）
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1028"
        if sdate is None:
            sdate = (pd.Timestamp.now() + pd.DateOffset(months=-11)).strftime('%Y-%m-%d')
        if edate is None:
            edate = pd.Timestamp.now().strftime('%Y-%m-%d')
        data = self._request(data_url=DATA_URL,params={"sdate":sdate,"edate":edate},method="POST",proxy_pool=proxy_pool)
        if not isinstance(data, dict) or "records" not in data:
            return -1
        df = pd.DataFrame(data["records"])
        # ---------- 5. 中文/字段名 -> 英文简写 ----------
        df.rename(columns={
            "SECCODE": "symbol",      # 股票代码
            "SECNAME": "name",        # 股票简称
            "DECLAREDATE":  "inc_ann_date",     # 公告日期
            "F004N":  "inc_vol",         # 减持数量汇总(股)
            "F005N":  "inc_ratio",    # 减持比例汇总(%)
        }, inplace=True)

        # ---------- 6. 日期字段标准化 ----------
        date_cols = [c for c in ("inc_ann_date") if c in df.columns]
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date
        # ---------- 7. 按 symbol 过滤（可选） ----------
        if symbol:
            symbol = str(symbol).zfill(6)
            df = df[df["symbol"] == symbol].reset_index(drop=True)
        if len(df)>=1:
            self.db.bulkInsUpd(df=df,table='ak_Increase_tot')
        return 0
    def get_ak_pfmc_frcst(self, rdate=None,symbol=None, proxy_pool: list = None):
        """
        https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1037?rdate=20261231
        获取业绩预测数据 报告年份: 20261231
        :param rdate:       报告年份，必填,2026-10-19
        :param symbol:      可选，证券代码（如 "000408"）。该接口返回全市场，传了则在结果内过滤出该只股票
        :param proxy_pool:  代理列表。默认使用 self.proxy_pool（SSH 动态转发 SOCKS5）
        :return:            pandas.DataFrame 或 -1
        """
        DATA_URL = self.BASE_URL + "/api/sysapi/p_sysapi1037"
        if rdate is None:
            print("报告年份为空")
            return -1

        data = self._request(data_url=DATA_URL,params={"rdate":rdate},method="POST",proxy_pool=proxy_pool)
        if not isinstance(data, dict) or "records" not in data:
            return -1
        df = pd.DataFrame(data["records"])
        # ---------- 5. 中文/字段名 -> 英文简写 ----------
        df.rename(columns={
            "SECCODE": "symbol",      # 股票代码
            "SECNAME": "name",        # 股票简称
            "DECLAREDATE":  "declare_date",  # 预告业绩日期
            "F001D":  "rdate",         # 报告日期
            "F002V":  "name_hy",    # 申万行业二级名称
            "F003V":  "change_type",    # 业绩变动类型
            "F004V":  "forecast_desc",    # 业绩预告说明
            "F005V":  "change_reason",    # 业绩变动原因
            "F009N":  "change_min",    # 预告净利润变动幅度上限
            "F010N":  "change_max",    # 预告净利润变动幅度下限
        }, inplace=True)

        # ---------- 6. 日期字段标准化 ----------
        date_cols = [c for c in ("DECLAREDATE","rdate") if c in df.columns]
        for col in date_cols:
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce").dt.date
        # ---------- 7. 按 symbol 过滤（可选） ----------
        if symbol:
            symbol = str(symbol).zfill(6)
            df = df[df["symbol"] == symbol].reset_index(drop=True)
        if len(df)>=1:
            self.db.bulkInsUpd(df=df,table='ak_pfmc_frcst')
        return 0
    
    
if __name__ == "__main__":
    aks = Myak()
    l = ['000004','000638','002231','002808','002898','300029','300344','300391','600193',
        '600355','600421','600599','600608','600636','600696','603056','605081','688287',
        '689009','920305']
    rst = aks.get_ak_companyinfo(symbol=l) #,proxy_pool=["socks5h://127.0.0.1:9988"]
    # rst = aks.get_ak_companyinfo2(symbol='603137,920725,000504,000505') #,proxy_pool=["socks5h://127.0.0.1:9988"]
    # aks.fetch_ak_companyinfo()
    # rst = aks.get_ak_dividend(rdate='20260331')
    # rst = aks._request(data_url=aks.BASE_URL+"/api/sysapi/p_sysapi1040",params={"rdate": "20260930"},method="POST")
    # rst = aks.get_ak_forecast_increase(rdate='20260930')
    # rst = aks.get_ak_forecast_decrease(rdate='20260630')
    # from conditionFunc import get_ak_dividend_params
    # d = get_ak_dividend_params()
    # rst = aks.get_ak_gd_num(rdate='20260930')
    # rst = aks.get_share_float()
    # rst = aks.get_ak_Reduce()
    # rst = aks.get_ak_Increase()
    # rst = aks.get_ak_pfmc_frcst(rdate='20261231')
    embed()
    