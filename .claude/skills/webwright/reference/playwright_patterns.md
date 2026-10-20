# Playwright 编程模式参考

本文档提供 Webwright 中使用的标准 Playwright 编程模式。所有 final_script.py 和探索脚本应遵循以下模式。

## 浏览器启动骨架

### 标准启动模式（Chromium）

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


async def main():
    async with async_playwright() as p:
        # 使用 Firefox 可避免某些站点的 ERR_HTTP2_PROTOCOL_ERROR
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 1800},
            ignore_https_errors=True,
        )
        page = await context.new_page()

        try:
            # === 执行步骤写在这里 ===
            pass
        except PwTimeout as e:
            logger.error(f"操作超时: {e}")
            await page.screenshot(path=SCREENSHOTS / "error_timeout.png")
            raise
        except Exception as e:
            logger.error(f"脚本执行失败: {e}", exc_info=True)
            await page.screenshot(path=SCREENSHOTS / "error_failure.png")
            raise
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
```

### Firefox 启动（遇到 ERR_HTTP2_PROTOCOL_ERROR 时切换）

```python
browser = await p.firefox.launch(headless=True)
# 其他配置与 Chromium 相同
```

## 智能认证模式（新增）

### 使用 SmartAuthRouter 自动处理认证

```python
# 导入智能认证模块（从项目路径）
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "app" / "agents" / "tools" / "web"))
from smart_auth import SmartAuthRouter, AuthConfig, AuthMode

# 配置认证
auth_config = AuthConfig(
    login_url="https://example.com/login",
    target_url="https://example.com/dashboard",
    username="admin",
    password="123456",
    token="eyJhbG...",  # 预获取的 Token（可选）
    preferred_mode=AuthMode.TOKEN_URL,  # 优先使用 Token 方式
    captcha_ocr_enabled=True,  # 启用 OCR 自动识别验证码
    captcha_length=4,  # 验证码位数
)

# 执行智能认证
auth_router = SmartAuthRouter(auth_config)
auth_result = await auth_router.authenticate(page)

if auth_result.success:
    logger.info(f"认证成功，使用模式: {auth_result.mode.value}")
else:
    logger.error(f"认证失败: {auth_result.message}")
```

### 认证模式优先级

1. **TOKEN_URL** — URL 携带 Token 参数直接访问（最高优先级，无感知）
2. **TOKEN_STORAGE** — 预注入 Token 到 localStorage/Cookie/sessionStorage
3. **CAPTCHA** — 验证码登录（支持 OCR 自动识别）
4. **MANUAL** — 人工介入（截图等待输入）

### 环境自动检测

```python
from smart_auth import auto_detect_auth_mode

# 自动检测页面是否需要验证码
mode = await auto_detect_auth_mode(page, "https://example.com/login")
# 返回 AuthMode.TOKEN_URL（无验证码）或 AuthMode.CAPTCHA（有验证码）
```

## Token 管理（新增）

### 使用 TokenManager 自动获取和刷新 Token

```python
from token_manager import TokenManager, LoginConfig, create_token_manager_for_peets

# 方式1: 通用配置
tm = TokenManager(
    login_config=LoginConfig(
        login_api="https://example.com/api/login",
        username="admin",
        password="123456",
        token_path="data.token",
    ),
    storage_path=Path("tokens/auth.json"),
    auto_refresh=True,  # 自动刷新
)

# 获取有效 Token（自动处理过期和刷新）
token = await tm.get_token()

# 方式2: Peets 项目专用
tm = create_token_manager_for_peets(
    username="lianqiao_peets",
    password="Lq123456",
    env="staging",  # staging / prod
)
token = await tm.get_token()
```

### 多环境 Token 管理

```python
from token_manager import MultiEnvTokenManager

manager = MultiEnvTokenManager(base_dir="tokens")

# 注册环境
manager.register_env("dev", LoginConfig(...))
manager.register_env("staging", LoginConfig(...))

# 获取指定环境的 Token
token = await manager.get_token("staging")
```

## 验证码 OCR 识别（新增）

### 使用 CaptchaOCR 自动识别验证码

```python
from captcha_ocr import CaptchaOCR, recognize_captcha

# 方式1: 快速识别
result = recognize_captcha("captcha.png")
print(result)  # "ABCD"

# 方式2: 指定引擎
ocr = CaptchaOCR(preferred_engine="ddddocr")
result = ocr.recognize("captcha.png")

# 方式3: 多引擎投票（提高准确率）
ocr = CaptchaOCR(vote_mode=True, vote_engines=["ddddocr", "paddleocr"])
result = ocr.recognize("captcha.png")
```

### 支持的 OCR 引擎

| 引擎 | 特点 | 安装命令 |
|------|------|---------|
| ddddocr | 轻量高效，验证码专用 | `pip install ddddocr` |
| paddleocr | 准确率高，模型较大 | `pip install paddleocr` |
| easyocr | 多语言支持 | `pip install easyocr` |
| tesseract | 传统 OCR，需系统安装 | `pip install pytesseract` |

## 全功能遍历（新增）

### 使用 TraversalEngine 遍历所有页面

```python
from traversal_engine import TraversalEngine, TraversalConfig, TraversalLevel

config = TraversalConfig(
    start_url="https://example.com/home",
    auth_config=AuthConfig(...),  # 认证配置
    max_depth=3,
    traversal_levels=[TraversalLevel.MENU, TraversalLevel.FUNCTION, TraversalLevel.FORM],
    skip_patterns=["logout", "delete", "退出"],  # 危险操作跳过
)

engine = TraversalEngine(config)
result = await engine.traverse(page)

print(f"访问页面: {result.visited_count}")
print(f"错误数: {result.error_count}")

# 保存结果
result.save(Path("traversal_result.json"))
```

### 遍历级别

| 级别 | 说明 | 收集元素 |
|------|------|---------|
| MENU | 菜单级 | 一级/二级菜单、导航项 |
| FUNCTION | 功能级 | 按钮、链接、Tabs |
| FORM | 表单级 | 输入框、下拉框、文本域 |

## 元素定位模式

### 定位器优先级

选择器的优先级从高到低排列：

1. **ID 选择器** — `page.locator("#username")` 最稳定
2. **data-testid 属性** — `page.locator("[data-testid='login-button']")` 最推荐
3. **name 属性** — `page.locator("input[name='email']")` 适用于表单
4. **aria-label / role** — `page.get_by_role("button", name="提交")`
5. **文本内容** — `page.get_by_text("登录")` 或 `page.locator("text=登录")`
6. **CSS class** — `page.locator(".btn-primary")` 谨慎使用，class 可能动态变化
7. **XPath** — `page.locator("//button[contains(text(), '提交')]")` 只在其他方法无效时使用

### 常用操作模式

```python
# 导航
await page.goto("https://example.com/login", wait_until="networkidle", timeout=30000)
await page.wait_for_load_state("domcontentloaded")

# 等待元素出现后再操作
await page.wait_for_selector("input#username", timeout=10000)
await page.fill("input#username", "admin")

# 点击
await page.wait_for_selector("button#submit", state="visible", timeout=10000)
await page.click("button#submit")

# 下拉选择
await page.select_option("select#country", "CN")

# 获取元素文本
text = await page.text_content("h1.page-title")
logger.info(f"页面标题: {text}")

# 检查元素是否存在
count = await page.locator("div.error-message").count()
if count > 0:
    logger.warning("页面出现错误提示")

# 等待页面跳转
await page.wait_for_url("**/dashboard", timeout=10000)

# 获取当前 URL
current_url = page.url
logger.info(f"当前 URL: {current_url}")
```

## final_script.py 仪器化模式

每个 final_script.py 必须包含完整的仪器化（instrumentation），确保每一步都可追踪和验证。

### 步骤编号模式

```python
# Step 1: 导航到登录页面
step = 1
logger.info(f"Step {step}: 导航到登录页面 (https://example.com/login)")
await page.goto("https://example.com/login", wait_until="networkidle")
await page.screenshot(path=SCREENSHOTS / f"final_execution_{step}_navigate.png")
logger.info(f"Step {step} 完成")
step += 1

# Step 2: 填写用户名
logger.info(f"Step {step}: 填写用户名")
await page.wait_for_selector("input#username", timeout=10000)
await page.fill("input#username", "admin")
await page.screenshot(path=SCREENSHOTS / f"final_execution_{step}_fill_username.png")
logger.info(f"Step {step} 完成")
step += 1
```

### 验证步骤模式

```python
# 验证步骤：检查预期结果
logger.info(f"Step {step}: 验证登录成功")
try:
    await page.wait_for_selector("text=欢迎回来", timeout=5000)
    welcome_text = await page.text_content("text=欢迎回来")
    logger.info(f"验证通过: 看到 '{welcome_text}'")
    await page.screenshot(path=SCREENSHOTS / f"final_execution_{step}_verify_success.png")
except PwTimeout:
    logger.error("验证失败: 未找到 '欢迎回来' 文本")
    await page.screenshot(path=SCREENSHOTS / f"final_execution_{step}_verify_failed.png")
    raise AssertionError("登录验证失败")
logger.info(f"Step {step} 完成")
step += 1
```

## 日志格式

### 日志配置

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
```

### 日志级别约定

- `INFO` — 正常步骤执行：开始、完成、中间状态
- `WARNING` — 非致命异常：元素不存在但可继续、重试操作
- `ERROR` — 执行失败：超时、断言失败、异常

### 日志内容要求

每条日志应包含：
- 步骤编号
- 操作简述
- 关键数据（URL、选择器、输入值）

```
2026-06-27 10:00:01 [INFO] Step 1: 导航到登录页面 (https://example.com/login)
2026-06-27 10:00:03 [INFO] Step 1 完成
2026-06-27 10:00:03 [INFO] Step 2: 填写用户名 (input#username = admin)
2026-06-27 10:00:05 [INFO] Step 2 完成
```

### 最终输出

脚本执行完成后，必须在日志末尾打印最终数据行：

```
2026-06-27 10:00:30 [INFO] ================================================
2026-06-27 10:00:30 [INFO] 脚本执行完成 | 状态: 成功 | 总步骤: 5 | 失败: 0
2026-06-27 10:00:30 [INFO] ================================================
```

## 截图命名

### 命名规则

```
final_execution_<步骤编号>_<描述>.png
```

- 步骤编号：从 1 开始的整数
- 描述：英文小写，用下划线分隔，描述当前操作的内容

### 常用截图名称示例

| 步骤 | 文件名 | 说明 |
|------|--------|------|
| 1 | final_execution_1_navigate.png | 首次导航到页面 |
| 2 | final_execution_2_fill_username.png | 填写用户名后 |
| 3 | final_execution_3_fill_password.png | 填写密码后 |
| 4 | final_execution_4_click_submit.png | 点击提交按钮后 |
| 5 | final_execution_5_verify_dashboard.png | 验证跳转到 dashboard |
| 错误 | error_timeout.png | 超时时的错误截图 |
| 错误 | error_failure.png | 其他异常时的错误截图 |

### 错误截图

当发生异常时，在 `except` 块中立即截图，使用 `error_` 前缀：

```python
except PwTimeout as e:
    await page.screenshot(path=SCREENSHOTS / "error_timeout.png")
except Exception as e:
    await page.screenshot(path=SCREENSHOTS / "error_failure.png")
```

## 完整脚本示例

完整示例请参考 `reference/workflow.md` 中的脚本片段或 generator skill 中的模板。
