import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from playwright.async_api import async_playwright

USERNAME = "13417554558"
PASSWORD = "Jyp1681314"

async def tushare_login_manual():
    async with async_playwright() as p:
        # ✅ launch_persistent_context 支持 user_data_dir
        context = await p.chromium.launch_persistent_context(
            user_data_dir="./tushare_profile",
            headless=False
        )
        page = await context.new_page()

        await page.goto("https://tushare.pro/login")
        await page.wait_for_load_state("networkidle")

        await page.locator("input[placeholder='请输入手机号/邮箱']").fill(USERNAME)
        await page.locator("input[placeholder='请输入密码']").fill(PASSWORD)

        print("\n===== 请在浏览器窗口：手动输入图形验证码 → 点击登录按钮 =====")
        print("不要关闭浏览器，等待控制台提示登录成功\n")

        # 等待登录成功出现个人中心
        await page.wait_for_selector("a:has-text('个人中心')", timeout=120000)
        print("✅登录成功！会话保存到 ./tushare_profile")

        await asyncio.sleep(5)
        await context.close()

async def crawl_after_login():
    browser_cfg = BrowserConfig(
        headless=False,
        user_data_dir="./tushare_profile",
        use_persistent_context=True
    )
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        res = await crawler.arun("https://tushare.pro/weborder/#/user/privilege")
        await asyncio.sleep(30)
        print(res.markdown[:1000])


if __name__ == "__main__":
    # asyncio.run(tushare_login_manual())
    asyncio.run(crawl_after_login())
