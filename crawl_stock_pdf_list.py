import asyncio
import json
import re
import os
from playwright.async_api import async_playwright
from typing import List, Dict, Any

def parse_finance_notice_body(body_text: str):
    pat = re.compile(r"finance_notice\s*\s=*(\{.*?\})(?=;|$)", re.S)
    m = pat.search(body_text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


async def fetch_link_related_items(symbol: str, user_data_dir: str, page_no:int=1, n:int=51):
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            user_agent=UA,
            args=[
                "--no-first-run",
                "--no-default-browser-check"
            ]
        )
        page = await browser.new_page()

        captured_responses: List[Dict] = []

        async def on_response(resp):
            if "news/info/search" in resp.url:
                try:
                    body_bytes = await resp.body()
                    body_text = body_bytes.decode("utf-8", errors="ignore")
                    captured_responses.append({
                        "api_url": resp.url,
                        "body_text": body_text
                    })
                except Exception:
                    pass

        page.on("response", on_response)

        stock_url = f"https://gu.qq.com/{symbol}/gp"
        await page.goto(stock_url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)

        notice_tab = page.locator('a[data-report="quotpage_leftnav_gg_notice_hs"]')
        await notice_tab.wait_for(timeout=15000)
        await notice_tab.click()

        poll_start = asyncio.get_event_loop().time()
        while len(captured_responses) == 0:
            if asyncio.get_event_loop().time() - poll_start > 15:
                print("[WARN]等待公告接口超时")
                break
            await asyncio.sleep(0.4)

        item_list: List[Dict] = []
        for resp_rec in captured_responses:
            data = parse_finance_notice_body(resp_rec["body_text"])
            if data:
                data_list = data.get("data", {}).get("data", [])
                for row in data_list:
                    title = row.get("title", "").strip()
                    notice_id = row.get("id", "")
                    # 手动拼接公告详情链接
                    if notice_id:
                        detail_url = f"https://gu.qq.com/notice/{notice_id}"
                    else:
                        detail_url = ""
                    pub_time = row.get("time", "")
                    if not title:
                        continue
                    item_list.append({
                        "notice_id": notice_id,
                        "title": title,
                        "pub_time": pub_time,
                        "url": detail_url,
                        "api_url": resp_rec["api_url"]
                    })

        print(f"[DEBUG]解析得到条目数量：{len(item_list)}")
        await browser.close()
        return {
            "trigger_ok": len(item_list)>0,
            "item_list": item_list
        }


async def main_demo():
    user_dir = r"D:\pyprj\stock2\chrome_user_data"
    os.makedirs(user_dir, exist_ok=True)

    result = await fetch_link_related_items(symbol="sh688801", user_data_dir=user_dir)
    print("=== 是否成功拿到公告数据 ===", result["trigger_ok"])
    print(f"=== item_list长度：{len(result['item_list'])} ===")
    # 不再切片[:5]，全部打印，或者取前10条
    for idx, it in enumerate(result["item_list"][:10]):
        print(f"[{idx}] id:{it['notice_id']} time:{it['pub_time']} title:{it['title']}\ndetail_url:{it['url']}\n")


if __name__ == "__main__":
    asyncio.run(main_demo())
