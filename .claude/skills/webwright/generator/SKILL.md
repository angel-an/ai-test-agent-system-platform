---
name: generator
description: Use this agent when you need to generate Playwright Python scripts (final_script.py) for web automated testing in Webwright mode
tools:
  - read
  - write
  - search
  - shell
model: deepseek-chat
---

## Webwright 模式：生成 final_script.py

**本 skill 在 webwright agent 下运行。生成 Python/Playwright 脚本来实现端到端自动化测试。**

### 核心模板 — final_script.py（高性能版）

```python
"""
功能: <功能名称>
场景: <场景描述>
生成时间: <时间>
"""
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PwTimeout

# === 配置 ===
RUN_DIR = Path(__file__).parent
WORKSPACE = RUN_DIR.parent.parent  # final_runs/ 的上级
SCREENSHOTS = RUN_DIR / "screenshots"
LOG_FILE = RUN_DIR / "final_script_log.txt"
AUTH_STATE = RUN_DIR / "auth_state.json"  # 登录态缓存文件

SCREENSHOTS.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# === 高性能：资源拦截 ===
async def setup_route_interception(page):
    """拦截非必要资源，加速页面加载"""
    blocked_patterns = [
        "**.jpg", "**.jpeg", "**.png", "**.gif", "**.svg", "**.webp",
        "**.css", "**.woff", "**.woff2", "**.ttf", "**.eot",
        "**.mp4", "**.mp3", "**.webm", "**.ogg",
    ]
    for pattern in blocked_patterns:
        await page.route(pattern, lambda route: route.abort())
    logger.info("资源拦截已启用: 图片/CSS/字体/媒体")


# === 高性能：快速登录（带缓存）===
async def fast_login(page, context, url, username, password):
    """快速登录：优先使用缓存的登录态，避免重复登录流程"""
    # 1. 尝试使用缓存的登录态
    if AUTH_STATE.exists():
        logger.info("检测到缓存的登录态，尝试直接加载...")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            # 快速验证是否已登录（检查页面特征元素）
            try:
                await page.wait_for_selector(
                    ".ant-layout, .el-layout, nav, aside, .menu, [class*='menu']",
                    timeout=5000
                )
                logger.info("✅ 使用缓存登录态成功，跳过登录流程")
                return True
            except PwTimeout:
                logger.info("缓存登录态已过期，需要重新登录")
                AUTH_STATE.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"缓存登录态加载失败: {e}")
            AUTH_STATE.unlink(missing_ok=True)

    # 2. 执行登录流程（最小化等待）
    logger.info("执行登录流程...")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)

    # 填写用户名和密码
    try:
        await page.locator("input[placeholder*='用户名'], input[name*='username'], input#username").first.fill(username)
        await page.locator("input[type='password']").first.fill(password)
        await page.locator("button:has-text('登录'), button:has-text('Login'), button[type='submit']").first.click()
        await page.wait_for_load_state("domcontentloaded", timeout=30000)
    except Exception as e:
        logger.error(f"登录操作失败: {e}")
        raise

    # 3. 保存登录态供下次使用
    await context.storage_state(path=AUTH_STATE)
    logger.info("✅ 登录成功，已保存登录态")
    return True


# === 高性能：智能等待 ===
async def wait_for_stable(page, timeout=10000):
    """等待页面稳定：先等 DOM，再检查 loading 元素"""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except:
        pass
    # 检查并等待 loading 消失（最多3秒）
    try:
        loading = page.locator(".ant-spin, .el-loading, .loading, [class*='loading']").first
        if await loading.count() > 0 and await loading.is_visible():
            await loading.wait_for(state="hidden", timeout=3000)
    except:
        pass


async def main():
    """主执行流程"""
    async with async_playwright() as p:
        # 高性能浏览器启动参数
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-features=TranslateUI",
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
            ]
        )

        # 尝试加载缓存的登录态
        context_opts = {
            "viewport": {"width": 1280, "height": 1800},
            "ignore_https_errors": True,
        }
        if AUTH_STATE.exists():
            context_opts["storage_state"] = str(AUTH_STATE)

        context = await browser.new_context(**context_opts)
        page = await context.new_page()

        # 启用资源拦截
        await setup_route_interception(page)

        try:
            # === 执行步骤 ===

            # Step <N>: <操作说明>
            logger.info("Step <N>: 开始执行 <操作说明>")

            # 快速登录（自动使用缓存）
            await fast_login(page, context, "<目标URL>", "<用户名>", "<密码>")

            # 快速导航到目标页面
            await page.goto("<目标URL>", wait_until="domcontentloaded", timeout=15000)
            await wait_for_stable(page)

            # 按需截图（只在关键步骤截图）
            await page.screenshot(
                path=SCREENSHOTS / "final_execution_<N>_<描述>.png",
                full_page=False
            )
            logger.info("Step <N> 完成")

            # === 以下添加更多步骤 ===

            # Step <N+1>: ...
            # ...

            logger.info("=" * 50)
            logger.info("脚本执行成功完成")
            logger.info("=" * 50)

        except PwTimeout as e:
            logger.error(f"操作超时: {e}")
            await page.screenshot(
                path=SCREENSHOTS / "error_timeout.png",
                full_page=False
            )
            raise
        except Exception as e:
            logger.error(f"脚本执行失败: {e}", exc_info=True)
            await page.screenshot(
                path=SCREENSHOTS / "error_failure.png",
                full_page=False
            )
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### 生成规则

1. **每一行代码都要有相应的 action** — 导航、输入、点击、验证各一步
2. **关键步骤必须截图** — 导航后、表单填入后、点击后、验证结果（非关键步骤可跳过截图）
3. **使用 wait_for_load_state("domcontentloaded")** — 确保页面 DOM 加载后截图，避免 networkidle 等待过慢
4. **异常处理** — 超时、元素不存在等情况必须捕获并截图
5. **日志记录** — 每个步骤开始和完成都要记录
6. **视口** — 必须设置 viewport 1280x1800；禁止 full_page=True
7. **性能优先** — 遵循高性能模式：
   - 使用 `domcontentloaded` 替代 `networkidle`
   - 移除不必要的 `wait_for_timeout` 固定等待
   - 添加资源拦截（`page.route` 禁用图片/CSS/字体）
   - 使用 `storage_state` 缓存登录态
   - 只在关键步骤和失败时截图

### 常用 Playwright API 速查

| 操作 | 代码 | 说明 |
|------|------|------|
| 导航 | `await page.goto(url)` | 打开 URL |
| 等待加载 | `await page.wait_for_load_state("networkidle")` | 等待网络空闲 |
| 点击 | `await page.click(selector)` | 点击元素 |
| 输入 | `await page.fill(selector, text)` | 填写输入框 |
| 选择 | `await page.select_option(selector, value)` | 下拉选择 |
| 截图 | `await page.screenshot(path=...)` | 页面截图 |
| 获取文本 | `await page.text_content(selector)` | 元素文本 |
| 等待元素 | `await page.wait_for_selector(selector)` | 等待元素出现 |
| 判断可见 | `await page.is_visible(selector)` | 元素可见性 |

### 保存方式

生成脚本时使用 `save_web_test_script(script_content=...)` 保存到数据库。
同时将脚本写入 `final_runs/run_<id>/final_script.py` 以便立即执行。
