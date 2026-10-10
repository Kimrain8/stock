# -*- coding: utf-8 -*-
"""
巨潮资讯（深证信）数据接口调用函数 —— 免登录版
=================================================
目标接口: https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1018?scode=000408

【关键结论（已实测验证）】
  该接口无需登录、无需 access_token，只要在请求头里带一个
  由页面脚本 window.indexcode.getResCode() 生成的加密令牌即可：

      Accept-EncKey: <令牌>          # 页面运行时生成，约 1~2 分钟有效

  - 不带该头 -> 返回 401/451 "token null"
  - 带上该头 -> 返回 200 + 数据（GET / POST 均可）

  由于该令牌是页面运行时注入的原生 JS 生成的、静态源码里拿不到算法，
  本函数用 Playwright 无头浏览器加载页面取到令牌，再用 requests 调接口。
  这与你在浏览器里免登录查询的机制完全一致，可靠且无需破解加密。

依赖:
  pip install requests playwright
  playwright install chromium          # 或复用系统 chromium（见 CHROMIUM_PATHS）
"""

import os
import time
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://webapi.cninfo.com.cn"
DATA_URL = BASE_URL + "/api/sysapi/p_sysapi1018"      # 业务数据接口
PAGE_URL = BASE_URL + "/#/dataBrowse"                 # 用于取令牌的页面

# 候选 chromium 可执行文件路径（按序尝试；留 None 表示用 playwright 自带浏览器）
CHROMIUM_PATHS=[]
# CHROMIUM_PATHS = [
#     "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
#     "/usr/bin/chromium",
#     "/usr/bin/chromium-browser",
#     "/usr/bin/google-chrome",
# ]

# 令牌本地缓存（令牌有效约 1~2 分钟，这里缓存 50 秒，避免每次启动浏览器）
_ENKEY_CACHE = {"value": None, "time": 0.0}
_ENKEY_TTL = 50.0


def _pick_chromium():
    """返回可用的 chromium 路径，找不到则返回 None（用 playwright 自带浏览器）。"""
    for path in CHROMIUM_PATHS:
        if path and os.path.exists(path):
            return path
    return None


def get_enckey(force: bool = False) -> str:
    """
    获取接口鉴权令牌 Accept-EncKey 的值（= window.indexcode.getResCode()）。
    带 50 秒本地缓存，避免频繁启动浏览器。

    :param force: 强制重新获取（忽略缓存）
    :return: 令牌字符串
    """
    if not force:
        cached = _ENKEY_CACHE.get("value")
        if cached and (time.time() - _ENKEY_CACHE.get("time", 0)) < _ENKEY_TTL:
            return cached

    executable = _pick_chromium()
    launch_kwargs = {"headless": True}
    if executable:
        launch_kwargs["executable_path"] = executable

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

    _ENKEY_CACHE["value"] = token
    _ENKEY_CACHE["time"] = time.time()
    return token


def fetch_cninfo_api(scode: str, enckey: str = None, timeout: int = 30) -> dict:
    """
    调用巨潮 p_sysapi1018 业务接口，返回完整 JSON（dict）。

    :param scode:   证券代码，如 "000408"
    :param enckey:  可选，直接传入 Accept-EncKey 令牌；不传则自动获取
    :param timeout: 请求超时（秒）
    :return:        接口返回 JSON，如 {"resultcode":200,"resultmsg":"success","total":N,"records":[...]}
    """
    if not enckey:
        enckey = get_enckey()

    session = requests.Session()
    session.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0 Safari/537.36"),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": BASE_URL + "/",
        "Accept-EncKey": enckey,          # 关键鉴权头
    })

    resp = session.get(f"{DATA_URL}?scode={scode}", timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    # 令牌失效（401/451）时自动重试一次
    if data.get("resultcode") in (401, 451):
        new_key = get_enckey(force=True)
        session.headers["Accept-EncKey"] = new_key
        resp = session.get(f"{DATA_URL}?scode={scode}", timeout=timeout)
        data = resp.json()

    return data


def fetch_cninfo_records(scode: str, enckey: str = None) -> list:
    """调用接口并直接返回 records 列表（无数据时为空列表）。"""
    data = fetch_cninfo_api(scode, enckey)
    if data.get("resultcode") == 200 and data.get("resultmsg") == "success":
        return data.get("records") or []
    return []


if __name__ == "__main__":
    # 演示：抓取 000408（藏格矿业）的基本资料
    result = fetch_cninfo_api("000408")
    print("resultcode:", result.get("resultcode"))
    print("resultmsg :", result.get("resultmsg"))
    records = result.get("records") or []
    print("记录条数   :", len(records))
    if records:
        print("SECNAME:", records[0].get("SECNAME"))
        print("ORGNAME:", records[0].get("ORGNAME"))
