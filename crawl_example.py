from playwright.sync_api import sync_playwright
import pandas as pd
import os
from IPython import embed

""" 使用同步方式的playwright 打开浏览器进行模拟手工爬取数据 """
def sync_crawl_base():
    with sync_playwright() as p:
        # 启动浏览器（headless=True表示无界面运行）
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        # 访问网页
        page.goto("https://www.baidu.com/")
        page.wait_for_load_state("networkidle")  # 等待页面加载完成

        # 搜索商品
        page.fill("#chat-textarea", "笔记本电脑")
        # page.click(".button")
        page.click("#chat-submit-button")
        page.wait_for_selector(".gl-item")  # 等待商品列表加载

        # 提取数据
        products = []
        for item in page.query_selector_all(".gl-item"):
            name = item.query_selector(".p-name em").inner_text()
            price = item.query_selector(".p-price i").inner_text()
            products.append({"商品名称": name, "价格": price})

        # 保存数据
        df = pd.DataFrame(products)
        df.to_excel("京东笔记本电脑.xlsx", index=False)
        print(f"成功爬取{len(products)}个商品")

        browser.close()

import asyncio
from crawl4ai import AsyncWebCrawler, CacheMode
from playwright.async_api import async_playwright 
from playwright.sync_api import sync_playwright
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig


"""异步方式基本爬取url内容，适合在需要高并发抓取时使用。"""
async def async_crawl_base():
    async with AsyncWebCrawler() as crawler:
        # 最简单的抓取
        result = await crawler.arun(url="https://www.163.com/")
        print(result.markdown[:300])
        # 带浏览器渲染和反反爬的抓取（需要 Playwright）
        config = CrawlerRunConfig(
            simulate_user=True,   # 模拟用户滚动、点击等
            cache_mode=CacheMode.BYPASS  #禁用缓存，强制每次都重新抓取
        )
        result_js = await crawler.arun(url="https://httpbin.org/user-agent", config=config)
        print("\n--- Rendered content ---")
        print(result_js.markdown[:500])

# 尝试爬取 豆包或者deepseek 交互对话内容。这样避免了直接使用 API 的限制，同时也能获取到网页端的完整内容。



# region 也是异步方式基本爬取url内容，增加使用了BrowserConfig
# os.environ['HTTP_PROXY'] = 'http://127.0.0.1:7890'
# os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:7890'
async def async_crawl_browseconfig():
    browser_config = BrowserConfig()  # Default browser configuration
    run_config = CrawlerRunConfig()   # Default crawl run configuration

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url="https://blog.csdn.net/weixin_45399602/article/details/148739200",
            config=run_config
        )
        print(result.markdown)  # Print clean markdown content

# if __name__ == "__main__":
#     asyncio.run(async_crawl_browseconfig())
# endregion

#使用基本的playwright 操作页面
async def async_playwright_browse():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless = False)
        page = await browser.new_page()
        await page.goto('https://blog.csdn.net/weixin_45399602/article/details/148739200')
        await page.wait_for_load_state("networkidle")
        str_title=await page.title()
        print(f'Title: {str_title}')
        await browser.close()



if __name__ == "__main__":
    # crawl_dynamic_website()
    # asyncio.run(main())
    # asyncio.run(test_browser())
    pass

    
    