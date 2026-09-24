import asyncio
from playwright.async_api import async_playwright
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

async def get_div_markdown(selector: str):
    """
    从当前 Chrome 页面中提取指定 CSS 选择器对应的 div 内容，并转为 Markdown。

    :param selector: CSS 选择器，例如 '#main-content', '.article-body', 'div[data-id="123"]'
    """
    async with async_playwright() as p:
        # 1. 连接到已启动的 Chrome (确保端口 9222 与启动参数一致)
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.pages[0]          # 当前活动标签页

        # 2. 等待目标元素出现（最多等待 5 秒，可根据需要调整）
        try:
            element = await page.wait_for_selector(selector, timeout=5000)
        except Exception as e:
            print(f"未找到元素，请检查选择器: {selector}")
            print(f"错误详情: {e}")
            return

        # 3. 获取元素的内部 HTML（不包括元素自身标签，只包括子元素）
        #    如果想包含自身，可使用 element.outer_html()
        inner_html = await element.inner_html()

        # 4. 获取当前页面的 URL，用于生成器解析相对路径
        url = page.url

        # 5. 使用 Crawl4AI 的 Markdown 生成器转换这段 HTML
        generator = DefaultMarkdownGenerator()
        # 注意：generate_markdown 是同步方法，且参数名为 input_html (0.9.2)
        markdown_result = generator.generate_markdown(
            input_html=inner_html,
            base_url=url
        )

        # 6. 提取 Markdown 文本
        if hasattr(markdown_result, 'raw_markdown'):
            markdown_text = markdown_result.raw_markdown
        else:
            markdown_text = markdown_result

        print(f"当前页面: {url}")
        print(f"目标元素 (选择器: {selector}) 的 Markdown 内容 (前500字符):")
        print(markdown_text[:500])

        # 可选：将结果保存到文件
        # with open("output.md", "w", encoding="utf-8") as f:
        #     f.write(markdown_text)

if __name__ == "__main__":
    # 使用示例：提取 id 为 "main-content" 的 div
    asyncio.run(get_div_markdown("#main-content"))