import asyncio
from datetime import datetime
from pathlib import Path
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

# 基础配置
DEEPSEEK_URL = "https://chat.deepseek.com/"
SAVE_DIR = Path("./outputs/deepseek_chat_log")
SAVE_DIR.mkdir(exist_ok=True, parents=True)

# 元素选择器
INPUT_BOX = "textarea"
CHAT_ITEM = "div.flex.flex-col.gap-4"
ANSWER_BODY = "div.markdown-body"

# 超时配置
MAX_WAIT_SEC = 120
IDLE_SEC = 2.0

async def deepseek_chat_and_save_md():
    # 浏览器配置：关闭代理、模拟正常浏览器，规避反爬
    browser_cfg = BrowserConfig(
        headless=False,
        viewport_width=1200,
        viewport_height=800,
        proxy=None,          # 禁用代理，解决 Proxy direct 报错
        java_script_enabled=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # 关键：移除 config 里的 wait_for，不前置等待元素
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=180000,
        # 不要在这里写 wait_for，会直接超时
    )

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        # 1. 仅打开页面，不做元素等待
        result = await crawler.arun(url=DEEPSEEK_URL, config=run_cfg)
        if not result.success:
            print(f"页面访问异常: {result.error_message}")
            return

        page = crawler.page
        print("✅ 页面已打开，请先处理登录/弹窗，再进行提问")

        # 2. 手动分步等待输入框出现（容错更高）
        print("⌛ 等待输入框加载...")
        try:
            await page.wait_for_selector(INPUT_BOX, timeout=60000)
        except Exception:
            print("❌ 输入框加载超时，请检查网页是否正常打开/登录")
            return

        print("\n===== 操作指引 =====")
        print("1. 浏览器内输入问题并发送")
        print("2. 发送完成后，回到终端按 Enter\n")
        input("发送提问后，请按 Enter 开始监听回答 >>> ")

        # 3. 等待输入框恢复可编辑（回答结束标记）
        print("⌛ 等待AI回答生成...")
        try:
            await page.wait_for_selector(INPUT_BOX, state="editable", timeout=MAX_WAIT_SEC * 1000)
        except Exception:
            print("⚠️ 等待回答超时，继续尝试提取内容")

        # 4. 内容防抖，确保流式输出结束
        last_content = ""
        start_time = datetime.now()
        finished = False

        while True:
            cost = (datetime.now() - start_time).total_seconds()
            if cost > MAX_WAIT_SEC:
                print("⚠️ 内容监听超时")
                break

            try:
                ele = page.locator(CHAT_ITEM).last.locator(ANSWER_BODY)
                current_content = await ele.inner_text()
            except Exception:
                current_content = ""

            if current_content == last_content and current_content.strip():
                await asyncio.sleep(IDLE_SEC)
                final_content = await ele.inner_text()
                if final_content == current_content:
                    print("✅ AI 回答加载完成")
                    finished = True
                    break

            last_content = current_content
            await asyncio.sleep(0.5)

        if not finished:
            print("❌ 未获取到完整回答")
            return

        # 5. 提取问答内容
        try:
            user_q = await page.locator(INPUT_BOX).last.input_value()
            user_q = user_q.strip()
            ai_ans = await page.locator(CHAT_ITEM).last.locator(ANSWER_BODY).inner_text()
            ai_ans = ai_ans.strip()
        except Exception as e:
            print(f"❌ 提取内容失败: {e}")
            return

        if not user_q or not ai_ans:
            print("❌ 提问或回答为空")
            return

        # 6. 生成并保存 Markdown
        now = datetime.now()
        file_name = now.strftime("%Y%m%d_%H%M%S") + ".md"
        file_path = SAVE_DIR / file_name

        md_text = f"""# DeepSeek 对话记录
**记录时间**：{now.strftime('%Y-%m-%d %H:%M:%S')}

## 👤 我的提问
{user_q}

## 🤖 DeepSeek 回答
{ai_ans}
"""
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(md_text)

        print(f"\n🎉 保存成功！文件：{file_path.resolve()}")
        print("-" * 60)

# 循环多轮对话（可选）
async def main_loop():
    while True:
        await deepseek_chat_and_save_md()
        op = input("是否继续下一轮对话？(y/n)：")
        if op.lower() != "y":
            print("程序退出")
            break

if __name__ == "__main__":
    try:
        # 单次运行
        asyncio.run(deepseek_chat_and_save_md())

        # 如需连续多轮，注释上面一行，打开下面一行
        # asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n🛑 程序手动终止")