"""
Smart Authentication Router - 智能认证路由模块

支持三种认证模式自动切换：
1. token_url: URL 携带 Token 参数直接访问（最高优先级，无感知）
2. token_storage: 预注入 Token 到 localStorage/Cookie/sessionStorage
3. captcha: 验证码登录（fallback，支持 OCR 自动识别）

适用场景：
- 开发环境（验证码关闭）→ token_url 或 token_storage
- 测试/验收环境（验证码开启）→ captcha + OCR 自动识别
- Token 过期自动刷新
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Tuple

from playwright.async_api import Page, BrowserContext, TimeoutError as PwTimeout

logger = logging.getLogger(__name__)


class AuthMode(Enum):
    """认证模式"""
    TOKEN_URL = "token_url"           # URL 携带 Token
    TOKEN_STORAGE = "token_storage"   # 存储注入 Token
    CAPTCHA = "captcha"               # 验证码登录
    MANUAL = "manual"                 # 人工介入


class AuthStatus(Enum):
    """认证状态"""
    SUCCESS = "success"
    FAILED = "failed"
    NEED_CAPTCHA = "need_captcha"
    TOKEN_EXPIRED = "token_expired"
    NETWORK_ERROR = "network_error"


@dataclass
class AuthConfig:
    """认证配置"""
    # 基础配置
    login_url: str = ""                    # 登录页面 URL
    target_url: str = ""                   # 目标页面 URL（用于验证登录成功）
    username: str = ""
    password: str = ""

    # Token 配置
    token: Optional[str] = None            # 预获取的 Token
    token_key: str = "accessToken"         # Token 在存储中的 key 名
    token_param_name: str = "accessToken"  # URL 参数名
    token_storage_keys: list = field(default_factory=lambda: [
        "token", "accessToken", "Authorization", "auth_token", "x-token", "userToken"
    ])

    # 验证码配置
    captcha_length: int = 4                # 验证码位数
    captcha_selector: str = ""             # 验证码图片选择器
    captcha_input_selector: str = ""       # 验证码输入框选择器
    captcha_api_url: Optional[str] = None    # 验证码获取 API（用于 OCR）
    captcha_ocr_enabled: bool = True         # 是否启用 OCR 自动识别

    # 环境检测
    auto_detect_mode: bool = True          # 自动检测环境（是否有验证码）
    preferred_mode: AuthMode = AuthMode.TOKEN_URL  # 优先使用的模式

    # 重试配置
    max_retries: int = 3
    retry_delay: int = 2

    # 持久化
    token_file: Optional[Path] = None      # Token 保存文件路径

    def get_url_with_token(self) -> str:
        """获取携带 Token 的 URL"""
        if not self.token:
            return self.target_url or self.login_url
        separator = "&" if "?" in (self.target_url or self.login_url) else "?"
        return f"{self.target_url or self.login_url}{separator}{self.token_param_name}={self.token}"


@dataclass
class AuthResult:
    """认证结果"""
    status: AuthStatus
    mode: AuthMode
    message: str
    token: Optional[str] = None
    step: int = 0
    screenshots: list = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.status == AuthStatus.SUCCESS


class SmartAuthRouter:
    """
    智能认证路由器

    自动选择最佳认证方式，按优先级：
    1. URL Token（免登录，无感知）
    2. Storage Token（预注入，免登录）
    3. 验证码 OCR（自动识别）
    4. 人工介入（截图等待输入）

    使用示例：
        auth = SmartAuthRouter(config)
        result = await auth.authenticate(page)
        if result.success:
            print(f"认证成功，使用模式: {result.mode}")
    """

    def __init__(self, config: AuthConfig):
        self.config = config
        self._ocr_engine = None  # 延迟初始化

    async def authenticate(self, page: Page, start_step: int = 1) -> AuthResult:
        """
        执行智能认证

        按优先级尝试各种认证方式，直到成功或耗尽所有选项。

        Args:
            page: Playwright Page 对象
            start_step: 起始步骤编号（用于截图命名）

        Returns:
            AuthResult: 认证结果
        """
        step = start_step
        modes_to_try = self._get_modes_priority()

        logger.info("=" * 60)
        logger.info(f"开始智能认证 | 优先模式: {self.config.preferred_mode.value}")
        logger.info(f"尝试顺序: {[m.value for m in modes_to_try]}")
        logger.info("=" * 60)

        for mode in modes_to_try:
            logger.info(f"\n尝试认证模式: {mode.value}")

            try:
                if mode == AuthMode.TOKEN_URL:
                    result = await self._try_token_url(page, step)
                elif mode == AuthMode.TOKEN_STORAGE:
                    result = await self._try_token_storage(page, step)
                elif mode == AuthMode.CAPTCHA:
                    result = await self._try_captcha_login(page, step)
                elif mode == AuthMode.MANUAL:
                    result = await self._try_manual_login(page, step)
                else:
                    continue

                if result.success:
                    logger.info(f"✅ 认证成功！使用模式: {mode.value}")
                    # 保存 Token 供后续使用
                    if result.token and self.config.token_file:
                        self._save_token(result.token)
                    return result
                else:
                    logger.warning(f"❌ 模式 {mode.value} 失败: {result.message}")
                    step = result.step

            except Exception as e:
                logger.error(f"模式 {mode.value} 异常: {e}")
                step += 1
                continue

        # 所有模式都失败
        return AuthResult(
            status=AuthStatus.FAILED,
            mode=AuthMode.MANUAL,
            message="所有认证模式均失败",
            step=step
        )

    def _get_modes_priority(self) -> list:
        """获取按优先级排序的认证模式列表"""
        all_modes = [AuthMode.TOKEN_URL, AuthMode.TOKEN_STORAGE,
                     AuthMode.CAPTCHA, AuthMode.MANUAL]

        # 将首选模式移到最前面
        if self.config.preferred_mode in all_modes:
            all_modes.remove(self.config.preferred_mode)
            all_modes.insert(0, self.config.preferred_mode)

        # 过滤不可用的模式
        available = []
        for mode in all_modes:
            if mode == AuthMode.TOKEN_URL and not self.config.token:
                continue
            if mode == AuthMode.TOKEN_STORAGE and not self.config.token:
                continue
            available.append(mode)

        return available if available else [AuthMode.CAPTCHA, AuthMode.MANUAL]

    async def _try_token_url(self, page: Page, step: int) -> AuthResult:
        """尝试 URL Token 认证"""
        if not self.config.token:
            return AuthResult(
                status=AuthStatus.FAILED,
                mode=AuthMode.TOKEN_URL,
                message="未提供 Token",
                step=step
            )

        url = self.config.get_url_with_token()
        logger.info(f"Step {step}: URL Token 认证 | 导航到: {url[:80]}...")

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            # 检查是否仍在登录页
            current_url = page.url
            if "/login" in current_url.lower():
                logger.warning("Token 认证失败，仍在登录页面")
                return AuthResult(
                    status=AuthStatus.TOKEN_EXPIRED,
                    mode=AuthMode.TOKEN_URL,
                    message="Token 可能已过期",
                    step=step + 1
                )

            logger.info("✅ URL Token 认证成功")
            return AuthResult(
                status=AuthStatus.SUCCESS,
                mode=AuthMode.TOKEN_URL,
                message="URL Token 认证成功",
                token=self.config.token,
                step=step + 1
            )

        except PwTimeout:
            return AuthResult(
                status=AuthStatus.NETWORK_ERROR,
                mode=AuthMode.TOKEN_URL,
                message="页面加载超时",
                step=step
            )

    async def _try_token_storage(self, page: Page, step: int) -> AuthResult:
        """尝试 Storage Token 认证"""
        if not self.config.token:
            return AuthResult(
                status=AuthStatus.FAILED,
                mode=AuthMode.TOKEN_STORAGE,
                message="未提供 Token",
                step=step
            )

        logger.info(f"Step {step}: Storage Token 认证")

        try:
            # 先导航到登录页（同域要求）
            await page.goto(self.config.login_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1000)

            # 注入 Token 到多种存储
            token = self.config.token
            keys = self.config.token_storage_keys

            await page.evaluate(f"""(token, keys) => {{
                // localStorage & sessionStorage
                for (const key of keys) {{
                    localStorage.setItem(key, token);
                    sessionStorage.setItem(key, token);
                }}
                // 同时设置常见的 auth 对象
                try {{
                    const authData = {{ token: token, accessToken: token }};
                    localStorage.setItem('auth', JSON.stringify(authData));
                    sessionStorage.setItem('auth', JSON.stringify(authData));
                }} catch (e) {{}}
                return 'Token 已注入到 ' + keys.length + ' 个 key';
            }}""", token, keys)

            # 设置 Cookie
            domain = self._extract_domain(self.config.login_url)
            await page.context.add_cookies([
                {{
                    'name': 'token',
                    'value': token,
                    'domain': domain,
                    'path': '/'
                }},
                {{
                    'name': 'Authorization',
                    'value': f'Bearer {{token}}',
                    'domain': domain,
                    'path': '/'
                }}
            ])

            logger.info("Token 已注入到 localStorage/sessionStorage/Cookie")

            # 导航到目标页验证
            target = self.config.target_url or self.config.login_url.replace('/login', '/')
            await page.goto(target, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            current_url = page.url
            if "/login" in current_url.lower():
                logger.warning("Storage Token 认证失败，仍在登录页面")
                return AuthResult(
                    status=AuthStatus.TOKEN_EXPIRED,
                    mode=AuthMode.TOKEN_STORAGE,
                    message="Token 可能已过期或无效",
                    step=step + 1
                )

            logger.info("✅ Storage Token 认证成功")
            return AuthResult(
                status=AuthStatus.SUCCESS,
                mode=AuthMode.TOKEN_STORAGE,
                message="Storage Token 认证成功",
                token=token,
                step=step + 1
            )

        except Exception as e:
            return AuthResult(
                status=AuthStatus.FAILED,
                mode=AuthMode.TOKEN_STORAGE,
                message=f"Storage Token 认证异常: {e}",
                step=step
            )

    async def _try_captcha_login(self, page: Page, step: int) -> AuthResult:
        """尝试验证码登录（支持 OCR 自动识别）"""
        logger.info(f"Step {step}: 验证码登录")

        try:
            # 导航到登录页
            await page.goto(self.config.login_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # 填写用户名密码
            await self._fill_credentials(page, step)
            step += 2

            # 获取并识别验证码
            captcha_value = await self._solve_captcha(page, step)
            if not captcha_value:
                return AuthResult(
                    status=AuthStatus.NEED_CAPTCHA,
                    mode=AuthMode.CAPTCHA,
                    message="验证码识别失败",
                    step=step
                )
            step += 1

            # 填写验证码并登录
            await self._fill_captcha_and_login(page, captcha_value, step)
            step += 1

            # 等待登录结果
            await page.wait_for_timeout(5000)

            # 检查登录结果
            current_url = page.url
            if "/login" in current_url.lower():
                # 检查是否有错误提示
                error_text = await self._get_error_message(page)
                return AuthResult(
                    status=AuthStatus.FAILED,
                    mode=AuthMode.CAPTCHA,
                    message=f"登录失败: {error_text or '未知错误'}",
                    step=step
                )

            # 尝试提取 Token
            token = await self._extract_token_from_page(page)
            if token:
                self._save_token(token)

            logger.info("✅ 验证码登录成功")
            return AuthResult(
                status=AuthStatus.SUCCESS,
                mode=AuthMode.CAPTCHA,
                message="验证码登录成功",
                token=token,
                step=step
            )

        except Exception as e:
            return AuthResult(
                status=AuthStatus.FAILED,
                mode=AuthMode.CAPTCHA,
                message=f"验证码登录异常: {e}",
                step=step
            )

    async def _try_manual_login(self, page: Page, step: int) -> AuthResult:
        """人工介入登录（截图等待输入）"""
        logger.info(f"Step {step}: 人工介入登录")

        try:
            await page.goto(self.config.login_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # 截图保存
            screenshot_path = f"login_manual_step_{{step}}.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"登录页面截图已保存: {{screenshot_path}}")
            logger.info("请手动完成登录后，按回车继续...")

            # 等待用户输入（在实际实现中可以通过文件轮询或信号）
            # 这里返回需要人工介入的状态
            return AuthResult(
                status=AuthStatus.NEED_CAPTCHA,
                mode=AuthMode.MANUAL,
                message="需要人工完成登录",
                step=step + 1
            )

        except Exception as e:
            return AuthResult(
                status=AuthStatus.FAILED,
                mode=AuthMode.MANUAL,
                message=f"人工介入异常: {e}",
                step=step
            )

    async def _fill_credentials(self, page: Page, step: int):
        """填写用户名密码"""
        logger.info(f"Step {step}: 填写用户名")
        await page.wait_for_selector("input#login_username, input[name='username'], input[placeholder*='用户名']", timeout=10000)
        await page.fill("input#login_username, input[name='username'], input[placeholder*='用户名']", self.config.username)

        logger.info(f"Step {step + 1}: 填写密码")
        await page.fill("input#login_password, input[name='password'], input[placeholder*='密码']", self.config.password)

    async def _solve_captcha(self, page: Page, step: int) -> Optional[str]:
        """解决验证码（OCR 或人工）"""
        logger.info(f"Step {step}: 获取验证码")

        # 1. 尝试 OCR 自动识别
        if self.config.captcha_ocr_enabled:
            try:
                captcha_value = await self._ocr_captcha(page)
                if captcha_value and len(captcha_value) == self.config.captcha_length:
                    logger.info(f"OCR 识别结果: {captcha_value}")
                    return captcha_value
            except Exception as e:
                logger.warning(f"OCR 识别失败: {e}")

        # 2. 回退到截图+人工（文件轮询方式）
        return await self._manual_captcha_input(page, step)

    async def _ocr_captcha(self, page: Page) -> Optional[str]:
        """使用 OCR 识别验证码"""
        try:
            # 导入 OCR 引擎（延迟加载）
            from .captcha_ocr import CaptchaOCR

            ocr = CaptchaOCR()

            # 获取验证码图片
            captcha_img = await page.query_selector(
                self.config.captcha_selector or
                "img.indexStyle_imgStyle-Vra-Jpgm, img[class*='captcha'], img[class*='verify']"
            )

            if not captcha_img:
                logger.warning("未找到验证码图片元素")
                return None

            # 截图保存
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                captcha_path = f.name

            await captcha_img.screenshot(path=captcha_path)
            logger.info(f"验证码图片已保存: {captcha_path}")

            # OCR 识别
            result = ocr.recognize(captcha_path)

            # 清理临时文件
            Path(captcha_path).unlink(missing_ok=True)

            return result

        except ImportError:
            logger.warning("OCR 模块未安装，跳过自动识别")
            return None

    async def _manual_captcha_input(self, page: Page, step: int) -> Optional[str]:
        """人工输入验证码（通过文件轮询）"""
        captcha_file = Path("captcha_input.txt")
        timeout = 120  # 秒

        logger.info(f"等待人工输入验证码到文件: {captcha_file} (超时: {timeout}s)")

        start_time = time.time()
        while time.time() - start_time < timeout:
            if captcha_file.exists():
                captcha_value = captcha_file.read_text().strip()
                if captcha_value and len(captcha_value) == self.config.captcha_length:
                    captcha_file.unlink(missing_ok=True)
                    return captcha_value
            await asyncio.sleep(1)

        return None

    async def _fill_captcha_and_login(self, page: Page, captcha_value: str, step: int):
        """填写验证码并点击登录"""
        logger.info(f"Step {step}: 填写验证码: {captcha_value}")

        input_selector = (self.config.captcha_input_selector or
                         "input#login_validateCode, input[name='verifyCode'], input[placeholder*='验证码']")

        await page.click(input_selector)
        await page.wait_for_timeout(300)
        await page.locator(input_selector).press_sequentially(captcha_value, delay=100)
        await page.wait_for_timeout(1000)

        # 点击登录按钮
        logger.info(f"Step {step}: 点击登录按钮")
        login_btn = await page.query_selector("button.ant-btn-primary, button[type='submit'], button:has-text('登录')")
        if login_btn:
            await login_btn.click()
        else:
            await page.click("button:has-text('登录')")

    async def _extract_token_from_page(self, page: Page) -> Optional[str]:
        """从页面提取 Token"""
        try:
            # 从 localStorage 获取
            token = await page.evaluate("""() => {
                const keys = ['token', 'accessToken', 'Authorization', 'auth_token'];
                for (const key of keys) {
                    const val = localStorage.getItem(key) || sessionStorage.getItem(key);
                    if (val) return val;
                }
                // 尝试解析 auth 对象
                try {
                    const auth = localStorage.getItem('auth') || sessionStorage.getItem('auth');
                    if (auth) {
                        const parsed = JSON.parse(auth);
                        return parsed.token || parsed.accessToken;
                    }
                } catch (e) {}
                return null;
            }""")

            if token:
                logger.info(f"从页面提取到 Token: {token[:20]}...")
                return token

        except Exception as e:
            logger.warning(f"提取 Token 失败: {e}")

        return None

    async def _get_error_message(self, page: Page) -> Optional[str]:
        """获取页面错误提示"""
        try:
            # 常见错误提示选择器
            selectors = [
                ".ant-message-error",
                ".ant-notification-notice-description",
                "[class*='error']",
                "[class*='Error']",
                ".el-message--error"
            ]

            for selector in selectors:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        text = await element.text_content()
                        if text and text.strip():
                            return text.strip()
                except:
                    continue

        except Exception:
            pass

        return None

    def _save_token(self, token: str):
        """保存 Token 到文件"""
        if self.config.token_file:
            self.config.token_file.write_text(token)
            logger.info(f"Token 已保存到: {self.config.token_file}")

    def _load_token(self) -> Optional[str]:
        """从文件加载 Token"""
        if self.config.token_file and self.config.token_file.exists():
            return self.config.token_file.read_text().strip()
        return None

    @staticmethod
    def _extract_domain(url: str) -> str:
        """从 URL 提取域名"""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc
        # 移除端口
        domain = domain.split(':')[0]
        # 添加通配符前缀
        if domain.count('.') >= 2:
            return '.' + '.'.join(domain.split('.')[1:])
        return domain


# =============================================================================
# 便捷函数
# =============================================================================

async def smart_login(
    page: Page,
    login_url: str,
    username: str,
    password: str,
    token: Optional[str] = None,
    target_url: Optional[str] = None,
    **kwargs
) -> AuthResult:
    """
    便捷登录函数

    Args:
        page: Playwright Page
        login_url: 登录页 URL
        username: 用户名
        password: 密码
        token: 可选的预获取 Token
        target_url: 登录后目标页面
        **kwargs: 其他配置参数

    Returns:
        AuthResult: 认证结果

    示例：
        result = await smart_login(
            page,
            login_url="https://example.com/login",
            username="admin",
            password="123456",
            token="eyJhbGci..."
        )
    """
    config = AuthConfig(
        login_url=login_url,
        username=username,
        password=password,
        token=token,
        target_url=target_url,
        **kwargs
    )

    router = SmartAuthRouter(config)
    return await router.authenticate(page)


async def auto_detect_auth_mode(page: Page, login_url: str) -> AuthMode:
    """
    自动检测认证模式（检查页面是否有验证码）

    Args:
        page: Playwright Page
        login_url: 登录页 URL

    Returns:
        AuthMode: 检测到的认证模式
    """
    try:
        await page.goto(login_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        # 检查是否有验证码元素
        captcha_selectors = [
            "img[class*='captcha']",
            "img[class*='verify']",
            "img.indexStyle_imgStyle",
            "input#login_validateCode",
            "input[placeholder*='验证码']"
        ]

        for selector in captcha_selectors:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                logger.info(f"检测到验证码元素: {selector}")
                return AuthMode.CAPTCHA

        logger.info("未检测到验证码，使用 Token 模式")
        return AuthMode.TOKEN_URL

    except Exception as e:
        logger.warning(f"自动检测失败: {e}，默认使用验证码模式")
        return AuthMode.CAPTCHA
