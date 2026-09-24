import asyncio
from playwright.async_api import async_playwright
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from crawl4ai.content_filter_strategy import PruningContentFilter
# from crawl4ai.content_extractor import ContentExtractor

from types import SimpleNamespace
from IPython import embed

async def main():
    async with async_playwright() as p:
        # 关键: connect_over_cdp,不是 launch
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        
        # 拿默认 context (就是 Chrome 启动时的那个 profile)
        context = browser.contexts[0]
        
        # 找已开的标签
        for page in context.pages:
            print(f"page: {page.url}")
        
        # 或者新开标签
        page = await context.new_page()
        await page.goto("https://www.zhihu.com/hot")
        
        # 此时 page 是已登录状态,所有 cookie 自动复用


"""下面是使用 crawl4ai 的示例,可以直接复用上面拿到的 context,无需再登录"""
async def fetch():
    browser_cfg = BrowserConfig(
        cdp_url="http://localhost:9222",  # ← 关键:接 CDP 端点
        verbose=False,
    )
    run_cfg = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.4),
        ),
    )
    async with AsyncWebCrawler(config=browser_cfg) as c:
        result = await c.arun(url="https://www.zhihu.com/hot", config=run_cfg)
        return result.markdown  # 21K 清洗后的 markdown


async def fetch_browser_config():
    # 配置浏览器，连接到已启动的 Chrome 实例
    browser_config = BrowserConfig(
        use_managed_browser=True,  # 启用 CDP 模式
        debugging_port=9222,       # 与启动 Chrome 时指定的端口一致
        headless=False             # 保持浏览器可见，方便观察
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        # 配置抓取任务
        run_config = CrawlerRunConfig(
            markdown_generator=DefaultMarkdownGenerator(
                        content_filter=PruningContentFilter(threshold=0.4),
                    ),
        )

        # 执行抓取
        result = await crawler.arun(
            url="https://httpbin.org/ip", 
            config=run_config
        )
        print(result.markdown)


async def fetch_custom_config():
    browser_config = BrowserConfig(
        browser_mode="custom",       # 设置为自定义模式[reference:9]
        cdp_url="http://localhost:9222"  # Chrome 的 CDP 连接地址[reference:10]
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        run_config = CrawlerRunConfig()
        result = await crawler.arun(
            url="https://httpbin.org/ip", 
            config=run_config
        )
        print(result.markdown)


async def fetch_with_builtin():
    # 使用 builtin 模式连接
    browser_config = BrowserConfig(
        browser_mode="builtin" # 设置为内置模式[reference:15]
    )

    async with AsyncWebCrawler(config=browser_config) as crawler:
        # 你的抓取逻辑...
        result = await crawler.arun("https://example.com")
        print(result.markdown[:200])



async def get_current_page_markdown():
    # 1. 连接到已启动的 Chrome
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.pages[0]        # 当前活动页

        # 2. 获取页面 HTML 和 URL（不刷新、不跳转）
        html = await page.content()
        url = page.url
        title = page.title

        # 3. 创建 Markdown 生成器
        generator = DefaultMarkdownGenerator()

        # 4. 生成 Markdown（同步调用，不要 await）
        #    在 0.9.2 中，参数名为 input_html
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
        


rst = asyncio.run(get_current_page_markdown())
embed()

# if __name__ == "__main__":
    # asyncio.run(main())
    # asyncio.run(fetch())
    # asyncio.run(fetch_browser_config())
    # asyncio.run(fetch_custom_config())
    # asyncio.run(fetch_with_builtin())
    # asyncio.run(get_current_page_markdown())

    