import asyncio
from playwright.async_api import async_playwright
import asyncio  # 异步编程库
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode  # 网页抓取工具
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter
import os

OUTPUT_PATH = './outputs/markdown/'

def output_md(base_filename, md_str):
    # 创建输出目录
    os.makedirs(OUTPUT_PATH, exist_ok=True)

    # 生成带长度的文件名
    length = len(md_str)
    name, ext = os.path.splitext(base_filename)
    filename = f"{name}({length}){ext}"

    # 完整路径
    full_path = os.path.join(OUTPUT_PATH, filename)

    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(md_str)

    print(f"已保存到: {full_path}")



# 1.1 异步抓取网页内容
""" 基本异步网页爬取
async def main(output_filename):
    # 创建爬虫对象，自动管理资源(确保爬虫使用完后会自动关闭，释放资源)
    async with AsyncWebCrawler() as crawler:
        # 访问指定网址并等待响应(await 关键字表示等待这个操作完成后再继续执行下面的代码)
        result = await crawler.arun("https://www.anthropic.com/news/agent-capabilities-api")

        # 打印抓取结果
        print("Markdown length:", len(result.markdown))
        print(result.markdown[:300])

        # 保存到.md文件
        output_md(output_filename, result.markdown)

# 启动异步程序
asyncio.run(main('1_1_Basic.md'))
"""


# 2.1 使用浏览器配置。异步主函数，执行网页爬取任务
"""
async def main(output_filename):
   # 配置浏览器参数
   browser_config = BrowserConfig(
       headless = True,  # 不显示浏览器窗口模式
       viewport_width = 1280,   # 窗口宽度
       viewport_height = 720,   # 窗口高度
       user_agent = 'Chrome/114.0.0.0',  # 浏览器标识
       text_mode = True, #禁用图片加载，可能会加速仅文本的爬取
   )

   # 创建异步网页爬虫，自动管理资源
   async with AsyncWebCrawler(config = browser_config) as crawler:
       # 执行网页爬取
        result = await crawler.arun(
            url = "https://www.anthropic.com/news/agent-capabilities-api",  # 目标网址
        )

        # 显示爬取结果
        print("Markdown length:", len(result.markdown))  # 内容长度
        print(result.markdown[:300])  # 前300字符预览

        output_md(output_filename, result.markdown)

# 启动异步程序
asyncio.run(main('2_1_BrowserConfig.md'))
"""



# 2.2.0  使用爬虫配置。异步主函数，执行网页爬取任务
"""
async def main(output_filename):
   # 配置浏览器参数
   browser_config = BrowserConfig(
       headless = True,  # 无头模式，不显示浏览器窗口
       viewport_width = 1280,   # 窗口宽度
       viewport_height = 720,   # 窗口高度
       user_agent = 'Chrome/114.0.0.0',  # 浏览器标识
       text_mode = True, #禁用图片加载，可能会加速仅文本的爬取
   )

   # 配置爬虫运行参数
   run_config = CrawlerRunConfig(
       cache_mode = CacheMode.DISABLED,  # 禁用缓存，获取最新内容
       markdown_generator = DefaultMarkdownGenerator(),
   )

   # 创建异步网页爬虫，自动管理资源
   async with AsyncWebCrawler(config = browser_config) as crawler:
       # 执行网页爬取
        result = await crawler.arun(
            url = "https://www.anthropic.com/news/agent-capabilities-api",  # 目标网址
            config = run_config,  # 运行配置
        )

        # 显示爬取结果
        print("Markdown length:", len(result.markdown))  # 内容长度
        print(result.markdown[:300])  # 前300字符预览

        output_md(output_filename, result.markdown)

# 启动异步程序
asyncio.run(main('2_2_0_RunConfig.md'))
"""



#2.2.1
"""async def main(output_filename):
    # 浏览器配置
    browser_config = BrowserConfig(headless = True, # 无头模式
                                viewport_width = 1280,  # 窗口宽度
                                viewport_height = 720,  # 窗口高度
                                user_agent = 'Chrome/114.0.0.0', # 浏览器标识
                                text_mode = True,
                                 )

    # 爬虫运行配置
    run_config = CrawlerRunConfig(
    cache_mode = CacheMode.DISABLED,  # 禁用缓存
    markdown_generator = DefaultMarkdownGenerator(
        content_filter = PruningContentFilter(
            # min_word_threshold = 10, # 丢弃少于N个单词的块，因为它们可能太短或无用(不建议)
            threshold = 0.76,  # fixded: 固定阈值 / dynamic: 初始阈值
            threshold_type = "fixed", # 固定
            # threshold_type = "dynamic", # 变动
        )),
    )

    # 创建爬虫并执行
    async with AsyncWebCrawler(config = browser_config) as crawler:
        result = await crawler.arun(
            url = "https://www.anthropic.com/news/agent-capabilities-api",  # 目标网址
            config = run_config,  # 运行配置
        )

        # 保存原始内容
        print("Raw Markdown length:", len(result.markdown.raw_markdown))
        output_md(output_filename.replace('.md', '_raw.md'), result.markdown.raw_markdown)

        # 保存过滤后内容
        print("Fit Markdown length:", len(result.markdown.fit_markdown))
        output_md(output_filename.replace('.md', '_fit.md'), result.markdown.fit_markdown)

asyncio.run(main('2_2_1_RunConfig_ContentFilterPruning.md'))
"""

#2.2.2
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

async def main(output_filename):
    # 浏览器配置
    browser_config = BrowserConfig(headless = True, # 无头模式
                                viewport_width = 1280,  # 窗口宽度
                                viewport_height = 720,  # 窗口高度
                                user_agent = 'Chrome/114.0.0.0', # 浏览器标识
                                text_mode = True,
                                # proxy="http://127.0.0.1:7890"
                                 )

    # 爬虫运行配置
    run_config = CrawlerRunConfig(
    cache_mode = CacheMode.DISABLED,  # 禁用缓存
    markdown_generator = DefaultMarkdownGenerator(
        content_filter = PruningContentFilter(
            # min_word_threshold = 10, # 丢弃少于N个单词的块，因为它们可能太短或无用(不建议)
            threshold = 0.76,  # fixded: 固定阈值 / dynamic: 初始阈值
            # threshold_type = "fixed", # 固定
            threshold_type = "dynamic", # 变动
        ),
        options = {
            "ignore_links": True,
            "ignore_images": True,
            })
    )

    # 创建爬虫并执行
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url = "https://www.anthropic.com/news/agent-capabilities-api",  # 目标网址
            config = run_config,  # 运行配置
        )

        # 保存原始内容
        print("Raw Markdown length:", len(result.markdown.raw_markdown))
        output_md(output_filename.replace('.md', '_raw.md'), result.markdown.raw_markdown)

        # 保存过滤后内容
        print("Fit Markdown length:", len(result.markdown.fit_markdown))
        output_md(output_filename.replace('.md', '_fit.md'), result.markdown.fit_markdown)

asyncio.run(main('2_2_2_RunConfig_ContentFilterPruning_Options.md'))















