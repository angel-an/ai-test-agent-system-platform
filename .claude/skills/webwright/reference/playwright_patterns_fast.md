# Playwright 高性能编程模式参考

本文档提供 Webwright 中使用的**高性能** Playwright 编程模式。所有 final_script.py 和探索脚本应遵循以下模式，以最小化执行时间。

## 核心原则：速度优先

1. **最小等待** — 用 `domcontentloaded` 替代 `networkidle`，移除固定 `wait_for_timeout`
2. **资源拦截** — 拦截图片/CSS/字体/媒体，只加载必要资源
3. **会话复用** — 通过 `storage_state` 保存/恢复登录态，避免重复登录
4. **按需截图** — 只在关键验证点和失败时截图，不要每步都截图
5. **并行执行** — 独立测试场景使用 `asyncio.gather` 并发执行

---

## 浏览器启动骨架（高性能版）

### 标准启动模式（Chromium + 资源拦截）

```python
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PwTimeout

# 配置区域
RUN_DIR = Path(__file__).parent
SCREENSHOTS = RUN_DIR / "screenshots"
LOG_FILE = RUN_DIR / "final_script_log.txt"
AUTH_STATE = RUN_DIR / "auth_state.json"  # 登录态缓存

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
    blocked_extensions = [
        "**.jpg", "**.jpeg", "**.png", "**.gif", "**.svg", "**.webp",
        "**.css", "**.woff", "**.woff2", "**.ttf", "**.eot",
        "**.mp4", "**.mp3", "**.webm", "**.ogg",
    ]
    for pattern in blocked_extensions:
        await page.route(pattern, lambda route: route.abort())
    logger.info("资源拦截已启用: 图片/CSS/字体/媒体")


# === 高性能：快速登录（带缓存）===
async def fast_login(page, context, url, username, password):
    """快速登录：优先使用缓存的登录态"""
    # 1. 尝试使用缓存的登录态
    if AUTH_STATE.exists():
        logger.info("检测到缓存的登录态，尝试直接加载...")
        try:
            # 已经通过 new_context(storage_state=...) 加载了
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            # 快速验证是否已登录（检查页面特征元素）
            try:
                await page.wait_for_selector(".ant-layout, .el-layout, nav, aside, .menu", timeout=5000)
                logger.info("✅ 使用缓存登录态成功，跳过登录流程")
                return True
            except PwTimeout:
                logger.info("缓存登录态已过期，需要重新登录")
                AUTH_STATE.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"缓存登录态加载失败: {e}")
            AUTH_STATE.unlink(missing_ok=True)

    # 2. 执行登录流程
    logger.info("执行登录流程...")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_selector("input[type='password'], input[placeholder*='密码'], input[name*='password']", timeout=10000)

    # 填写用户名
    username_input = page.locator("input[placeholder*='用户名'], input[name*='username'], input[name*='user'], input#username").first
    if await username_input.count() > 0:
        await username_input.fill(username)

    # 填写密码
    password_input = page.locator("input[type='password']").first
    if await password_input.count() > 0:
        await password_input.fill(password)

    # 点击登录
    login_btn = page.locator("button:has-text('登录'), button:has-text('Login'), button[type='submit']").first
    if await login_btn.count() > 0:
        await login_btn.click()
        await page.wait_for_load_state("domcontentloaded", timeout=30000)

    # 3. 保存登录态供下次使用
    await context.storage_state(path=AUTH_STATE)
    logger.info("✅ 登录成功，已保存登录态")
    return True


async def main():
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
            # === 执行步骤写在这里 ===
            pass
        except PwTimeout as e:
            logger.error(f"操作超时: {e}")
            await page.screenshot(path=SCREENSHOTS / "error_timeout.png", full_page=False)
            raise
        except Exception as e:
            logger.error(f"脚本执行失败: {e}", exc_info=True)
            await page.screenshot(path=SCREENSHOTS / "error_failure.png", full_page=False)
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### Firefox 启动（遇到 ERR_HTTP2_PROTOCOL_ERROR 时切换）

```python
browser = await p.firefox.launch(
    headless=True,
    args=["--no-sandbox"]
)
# 其他配置与 Chromium 相同
```

---

## 高性能导航模式

### 快速页面加载（推荐）

```python
# ✅ 高性能：只等待 DOM 加载完成，不等待所有网络请求
await page.goto(url, wait_until="domcontentloaded", timeout=30000)

# ❌ 避免：networkidle 会等待所有网络请求结束，可能很慢
# await page.goto(url, wait_until="networkidle", timeout=60000)
```

### 等待元素而非固定时间

```python
# ✅ 高性能：等待元素出现，通常只需几百毫秒
await page.wait_for_selector(".ant-table", timeout=10000)

# ❌ 避免：固定等待浪费时间
# await page.wait_for_timeout(3000)
```

### 智能等待封装

```python
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
```

---

## 高性能元素操作

### 快速点击（不等待 networkidle）

```python
# ✅ 高性能：点击后只等 DOM 稳定
await element.click()
await page.wait_for_load_state("domcontentloaded", timeout=10000)

# ❌ 避免：
# await element.click()
# await page.wait_for_load_state("networkidle", timeout=30000)
# await page.wait_for_timeout(2000)
```

### 批量操作（减少等待累积）

```python
# ✅ 高性能：批量点击多个菜单，中间不等待
async def batch_click_menus(page, menu_items):
    """批量点击菜单，只在最后等待一次"""
    for item in menu_items:
        await item.click()
    # 只在最后等待一次页面稳定
    await wait_for_stable(page)

# ❌ 避免：每个点击后都等待
# for item in menu_items:
#     await item.click()
#     await page.wait_for_timeout(2000)
#     await page.wait_for_load_state("networkidle")
```

---

## 高性能截图策略

### 按需截图（减少 I/O）

```python
# ✅ 高性能：只在关键点和失败时截图
SCREENSHOT_ON_EVERY_STEP = False  # 关闭每步截图

async def maybe_screenshot(page, desc, force=False):
    """条件截图：只在关键步骤或强制时截图"""
    if SCREENSHOT_ON_EVERY_STEP or force:
        await page.screenshot(path=SCREENSHOTS / f"{desc}.png", full_page=False)

# 关键步骤截图
await maybe_screenshot(page, "login_success", force=True)

# 普通步骤不截图
await maybe_screenshot(page, "navigated_to_page")  # 不截图
```

---

## 并行执行模式

### 多页面并发测试

```python
async def test_multiple_features(context, features):
    """并发测试多个独立功能"""
    async def test_single(feature):
        page = await context.new_page()
        try:
            await setup_route_interception(page)
            # 执行功能测试...
            result = {"feature": feature, "status": "passed"}
        except Exception as e:
            result = {"feature": feature, "status": "failed", "error": str(e)}
        finally:
            await page.close()
        return result

    # 并发执行所有功能测试（最多5个并发）
    semaphore = asyncio.Semaphore(5)
    async def bounded_test(f):
        async with semaphore:
            return await test_single(f)

    results = await asyncio.gather(*[bounded_test(f) for f in features])
    return results
```

---

## 智能认证模式（高性能版）

### 使用 SmartAuthRouter + 登录态缓存

```python
# 高性能认证：优先使用 Token/Storage，避免重复登录流程
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "app" / "agents" / "tools" / "web"))
from smart_auth import SmartAuthRouter, AuthConfig, AuthMode

auth_config = AuthConfig(
    login_url="https://example.com/login",
    target_url="https://example.com/dashboard",
    username="admin",
    password="123456",
    token="eyJhbG...",  # 预获取的 Token（最高优先级）
    preferred_mode=AuthMode.TOKEN_URL,  # 优先使用 Token 方式（无感知登录）
    captcha_ocr_enabled=True,
)

auth_router = SmartAuthRouter(auth_config)
auth_result = await auth_router.authenticate(page)

if auth_result.success:
    # 保存登录态供后续复用
    await context.storage_state(path=AUTH_STATE)
    logger.info(f"✅ 认证成功，使用模式: {auth_result.mode.value}")
```

---

## Token 管理（高性能版）

### 预获取 Token 避免登录流程

```python
from token_manager import TokenManager, LoginConfig

# 初始化 TokenManager（自动缓存和刷新）
tm = TokenManager(
    login_config=LoginConfig(
        login_api="https://example.com/api/login",
        username="admin",
        password="123456",
        token_path="data.token",
    ),
    storage_path=Path("tokens/auth.json"),
    auto_refresh=True,
)

# 获取有效 Token（自动处理过期和刷新）
token = await tm.get_token()

# 直接通过 Token 访问（跳过登录页）
await page.goto(f"https://example.com/dashboard?token={token}")
```

---

## 元素定位模式（高性能版）

### 快速定位器

```python
# ✅ 高性能：使用最精确的选择器，减少查询时间
# 1. ID 选择器（最快）
await page.locator("#username").fill("admin")

# 2. 属性选择器（次快）
await page.locator("[data-testid='login-button']").click()

# 3. 角色选择器（ARIA 支持好）
await page.get_by_role("button", name="登录").click()

# ❌ 避免：模糊文本搜索（需要遍历所有文本节点）
# await page.locator("text=登录").first.click()
```

### 批量元素检查

```python
# ✅ 高性能：一次性检查多个元素
async def check_elements_fast(page, selectors):
    """使用 JS 批量检查元素存在性，比逐个 locator 快"""
    result = await page.evaluate("""
        (selectors) => {
            const results = {};
            for (const sel of selectors) {
                results[sel] = document.querySelectorAll(sel).length > 0;
            }
            return results;
        }
    """, selectors)
    return result

# 使用示例
exists = await check_elements_fast(page, [
    ".ant-table", ".ant-pagination", "button:has-text('新建')"
])
logger.info(f"表格: {exists['.ant-table']}, 分页: {exists['.ant-pagination']}")
```

---

## 日志格式（精简版）

### 高性能日志配置

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",  # 简化日期格式
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        # 生产环境可关闭 StreamHandler 减少 I/O
        # logging.StreamHandler()
    ]
)
```

### 日志级别约定

- `INFO` — 关键步骤：开始、完成、结果摘要
- `DEBUG` — 详细操作（只在调试时开启）
- `WARNING` — 非致命异常
- `ERROR` — 执行失败

---

## 完整高性能脚本示例

```python
"""
功能: 高性能 Web 测试示例
场景: 演示资源拦截、登录态缓存、快速导航、按需截图
"""
import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PwTimeout

RUN_DIR = Path(__file__).parent
SCREENSHOTS = RUN_DIR / "screenshots"
LOG_FILE = RUN_DIR / "final_script_log.txt"
AUTH_STATE = RUN_DIR / "auth_state.json"

SCREENSHOTS.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
logger = logging.getLogger(__name__)


async def setup_route_interception(page):
    """拦截非必要资源"""
    for pattern in ["**.png", "**.jpg", "**.jpeg", "**.gif", "**.svg", "**.css", "**.woff*", "**.ttf", "**.mp4", "**.mp3"]:
        await page.route(pattern, lambda route: route.abort())


async def fast_login(page, context, url, username, password):
    """快速登录（带缓存）"""
    if AUTH_STATE.exists():
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        try:
            await page.wait_for_selector("nav, aside, .menu, .ant-layout", timeout=5000)
            logger.info("✅ 缓存登录态有效")
            return True
        except:
            AUTH_STATE.unlink(missing_ok=True)

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await page.locator("input[type='password']").first.fill(password)
    await page.locator("input[placeholder*='用户名'], input[name*='username']").first.fill(username)
    await page.locator("button:has-text('登录')").first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=30000)
    await context.storage_state(path=AUTH_STATE)
    logger.info("✅ 登录成功，已缓存")
    return True


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        )
        ctx_opts = {"viewport": {"width": 1280, "height": 1800}, "ignore_https_errors": True}
        if AUTH_STATE.exists():
            ctx_opts["storage_state"] = str(AUTH_STATE)

        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()
        await setup_route_interception(page)

        try:
            # 快速登录
            await fast_login(page, context, "https://example.com/login", "admin", "123456")

            # 快速导航到目标页面
            await page.goto("https://example.com/target", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_selector(".ant-table", timeout=10000)

            # 关键验证点截图
            await page.screenshot(path=SCREENSHOTS / "verify_table.png", full_page=False)

            logger.info("✅ 测试完成")

        except Exception as e:
            logger.error(f"❌ 失败: {e}")
            await page.screenshot(path=SCREENSHOTS / "error.png", full_page=False)
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 性能检查清单

生成脚本后，检查以下项目：

- [ ] 使用了 `wait_until="domcontentloaded"` 而非 `networkidle`
- [ ] 没有 `wait_for_timeout(3000)` 等固定等待
- [ ] 启用了资源拦截（`page.route`）
- [ ] 使用了 `storage_state` 缓存登录态
- [ ] 截图只在关键点和失败时进行
- [ ] 浏览器启动参数包含 `--disable-dev-shm-usage` 等优化
- [ ] 元素检查使用批量 JS 评估而非逐个 locator
- [ ] 多个独立测试使用 `asyncio.gather` 并发
