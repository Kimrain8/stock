# -*- coding: utf-8 -*-
"""
腾讯股票「个股公告」PDF 批量下载（Playwright 版）

用法:
    python3 download_all_announcements.py sh688801 [输出目录]

针对页面: https://gu.qq.com/sh688801/gp  ->  「个股公告」tab 的公告清单

【已探明的陷阱与规避】
  陷阱1 公告清单是 SPA 异步加载，页面 HTML 里没有数据
        -> 不走页面渲染，直接用接口:  news/info/search?type=0
  陷阱2 接口返回的是 JSONP（finance_notice={...}），不是纯 JSON
        -> 解析时先剥离 "finance_notice=" 前缀再 json.loads
  陷阱3 total_num=9999 / total_page=197 是假封顶占位，不代表真实数量
        -> 真实终止条件 = 某一页 data.data 为空 或 集合不再增长
  陷阱4 公告 id 有新旧两种格式:
          新: nos688801_20260901_USP3   (可拼 PDF 路径)
          旧: nos1225535080             (拼不出路径!)
        -> 一律不拼路径，逐条调详情接口取 pdf 字段:
           news/content/content?id={id}&c={symbol}  ->  data[0].pdf
  陷阱5 详情接口返回的 pdf 地址可能含双斜杠(//finance/hs/pdf/) 或大写 .PDF，
        且路径日期与列表时间可能不一致
        -> 拿到 pdf 字段后统一 url 规范化(https + 去路径双斜杠)再下载
  陷阱6 站内挂了腾讯验证码脚本，高频/异常来源可能被风控
        -> 用 playwright 浏览器上下文(真实 UA + zh-CN + cookie 会话)发请求，
           下载后校验 %PDF 头 + %%EOF 尾，不是 PDF 就报错重试
  陷阱7 页面 notice 详情路由会 302 重定向到股票主页，无法靠浏览器导航拿 PDF
        -> 全程用 page.request（APIRequestContext），不依赖页面导航

依赖:  pip3 install playwright && python3 -m playwright install chromium
probe_list.py 用于探查接口清单
对接口清单的api结构的了解: `type=0` 是公司公告（id 形如 `nos688801_20260901_USP3`，和之前 PDF 的命名完全一致），`type=2` 是新闻（不是 PDF）



"""
import asyncio
import json
import os
import re
import sys
import time
from urllib.parse import urlparse, urlunparse

from playwright.async_api import async_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "sh688801"
OUT_DIR = sys.argv[2] if len(sys.argv) > 2 else "announcements"
CODE = re.sub(r"^(sh|sz|bj)", "", SYMBOL)

LIST_API = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/news/info/search"
            "?page={page}&symbol={symbol}&n=51&_var=finance_notice&type=0&_appver=1.0&_={ts}")
DETAIL_API = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/news/content/content"
              "?id={nid}&c={symbol}&_var=finance_notice_detail&_={ts}")


def parse_jsonp(text: str):
    """剥离 'xxx={...}' 的 JSONP 前缀，返回 dict。"""
    return json.loads(text.split("=", 1)[1])


def normalize_pdf_url(url: str) -> str:
    """统一为 https 并去除路径中的双斜杠。"""
    u = urlparse(url.replace("http://", "https://", 1))
    return urlunparse((u.scheme, u.netloc, u.path, "", "", ""))


def looks_like_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-" and b"%%EOF" in data[-2048:]


def safe_filename(title: str, nid: str, idx: int) -> str:
    """由公告标题生成安全文件名（去非法字符，限长），带 id 兜底。"""
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', "", title or "").strip()
    t = t[:60] or nid
    return f"{idx:02d}_{t}_{nid}.pdf"


async def fetch_jsonp(page, url: str):
    """用 page.request 请求并解析 JSONP，返回 dict 或 None。"""
    resp = await page.request.get(url, headers={"Referer": f"https://gu.qq.com/{SYMBOL}/gp"})
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status} @ {url[:120]}")
    return parse_jsonp(await resp.text())


async def fetch_announcement_list(page) -> list:
    """分页抓全公告清单（去重，直到空页或集合不再增长）。"""
    seen, result = set(), []
    page_no = 1
    while True:
        data = await fetch_jsonp(page, LIST_API.format(page=page_no, symbol=SYMBOL, ts=int(time.time() * 1000)))
        items = (data.get("data") or {}).get("data") or []
        print(f"  [列表] page={page_no} 返回 {len(items)} 条")
        if not items:
            break
        grew = False
        for it in items:
            nid = it["id"]
            if nid not in seen:
                seen.add(nid)
                result.append(it)
                grew = True
        if not grew:
            break
        page_no += 1
        if page_no > 200:  # 防御性上限
            break
    return result


async def resolve_pdf_url(page, nid: str) -> str:
    """调详情接口拿 pdf 地址。"""
    d = await fetch_jsonp(page, DETAIL_API.format(nid=nid, symbol=SYMBOL, ts=int(time.time() * 1000)))
    arr = d.get("data") or []
    if not arr or not arr[0].get("pdf"):
        raise RuntimeError(f"详情接口未返回 pdf: {nid}")
    return arr[0]["pdf"]


async def download_pdf(page, url: str, save_path: str) -> bool:
    """下载并校验 PDF。失败抛异常，由调用方决定是否重试。"""
    resp = await page.request.get(url, headers={"Referer": f"https://gu.qq.com/{SYMBOL}/gp"})
    if resp.status != 200:
        raise RuntimeError(f"下载 HTTP {resp.status}: {url}")
    data = await resp.body()
    if not looks_like_pdf(data):
        raise RuntimeError(f"内容非 PDF (前12字节={data[:12]!r})，疑似被风控拦截: {url}")
    with open(save_path, "wb") as f:
        f.write(data)
    return True


async def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=UA, locale="zh-CN", accept_downloads=True,
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        page = await ctx.new_page()
        try:
            print(f"=== 抓取 {SYMBOL} 个股公告清单 ===")
            anns = await fetch_announcement_list(page)
            print(f"共 {len(anns)} 条公告\n")

            ok, fail = [], []
            for idx, it in enumerate(anns, 1):
                nid = it["id"]
                title = it.get("title", "")
                print(f"[{idx}/{len(anns)}] {nid}  {title[:40]}")
                try:
                    pdf_raw = await resolve_pdf_url(page, nid)
                    pdf_url = normalize_pdf_url(pdf_raw)
                    print(f"      pdf: {pdf_url[:110]}")
                    save = os.path.join(OUT_DIR, safe_filename(title, nid, idx))
                    await download_pdf(page, pdf_url, save)
                    size = os.path.getsize(save)
                    print(f"      已保存: {save} ({size} bytes)")
                    ok.append((nid, title, pdf_url, save, size))
                except Exception as e:
                    print(f"      失败: {type(e).__name__}: {str(e)[:150]}")
                    fail.append((nid, title, str(e)))

            print("\n=== 汇总 ===")
            print(f"成功 {len(ok)}/{len(anns)}，失败 {len(fail)}")
            for nid, title, url, save, size in ok:
                print(f"  OK   {nid:44s} {os.path.basename(save)}  ({size} bytes)")
            for nid, title, err in fail:
                print(f"  FAIL {nid:44s} {err[:80]}")
            return len(fail) == 0
        finally:
            await browser.close()


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
