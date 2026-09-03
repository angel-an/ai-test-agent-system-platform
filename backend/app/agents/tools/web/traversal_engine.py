"""
Page Traversal Engine - 全功能遍历测试引擎

支持三级遍历策略：
1. 菜单级 (Menu) - 遍历所有一级/二级菜单
2. 功能级 (Function) - 遍历菜单下的功能入口（按钮、链接、Tab）
3. 表单级 (Form) - 遍历表单内的字段、操作（增删改查）

适用场景：
- 营销云全功能遍历测试
- 回归测试：快速验证所有页面可访问
- 新环境冒烟测试：验证部署完整性

特性：
- 智能去重：基于 URL + 页面标题去重
- 危险操作保护：自动跳过删除、批量操作等
- 弹窗处理：自动处理确认弹窗
- 截图记录：每个访问页面自动截图
- 异常恢复：页面加载失败自动回退

使用示例：
    config = TraversalConfig(
        start_url="https://example.com/home",
        auth_config=AuthConfig(...),
        max_depth=3,
        traversal_levels=["menu", "function", "form"]
    )

    engine = TraversalEngine(config)
    result = await engine.traverse(page)
    print(f"遍历完成: {result.visited_count} 页面, {result.error_count} 错误")
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Set, Callable, Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page, BrowserContext, TimeoutError as PwTimeout

from .smart_auth import SmartAuthRouter, AuthConfig, AuthResult

logger = logging.getLogger(__name__)


class TraversalLevel(Enum):
    """遍历级别"""
    MENU = "menu"         # 菜单级
    FUNCTION = "function"  # 功能级（按钮、链接、Tab）
    FORM = "form"         # 表单级（字段、增删改查）


class ElementType(Enum):
    """元素类型"""
    MENU = "menu"
    SUBMENU = "submenu"
    BUTTON = "button"
    LINK = "link"
    TAB = "tab"
    TABLE = "table"
    FORM = "form"
    MODAL = "modal"
    UNKNOWN = "unknown"


@dataclass
class TraversalConfig:
    """遍历配置"""
    # 起始页面
    start_url: str = ""
    home_url: str = ""  # 登录后的首页

    # 认证配置
    auth_config: Optional[AuthConfig] = None

    # 遍历深度和级别
    max_depth: int = 3
    traversal_levels: List[TraversalLevel] = field(
        default_factory=lambda: [TraversalLevel.MENU, TraversalLevel.FUNCTION]
    )

    # 菜单选择器（可根据项目定制）
    menu_selectors: List[str] = field(default_factory=lambda: [
        "aside .el-menu-item",           # Element UI
        "aside .el-submenu",              # Element UI submenu
        ".ant-menu-item",                 # Ant Design
        ".ant-menu-submenu",              # Ant Design submenu
        "[class*='menu-item']",           # 通用菜单
        "[class*='nav-item']",            # 导航项
        "nav a",                          # 导航链接
    ])

    # 功能元素选择器
    function_selectors: List[str] = field(default_factory=lambda: [
        "button:not([disabled])",        # 可用按钮
        "a[href]",                        # 链接
        ".ant-tabs-tab",                  # Ant Design Tabs
        ".el-tabs__item",                 # Element UI Tabs
        "[role='tab']",                   # ARIA tab
        "[class*='tab']",                 # 通用 Tab
        ".action-btn",                    # 操作按钮
        "[class*='btn']",                 # 通用按钮
    ])

    # 表单元素选择器
    form_selectors: List[str] = field(default_factory=lambda: [
        "input:not([type='hidden'])",     # 输入框
        "select",                         # 下拉框
        "textarea",                       # 文本域
        ".ant-form-item",                 # Ant Design 表单项
        ".el-form-item",                  # Element UI 表单项
        "[class*='form-item']",           # 通用表单项
    ])

    # 危险操作跳过规则
    skip_patterns: List[str] = field(default_factory=lambda: [
        "logout", "退出", "登出",
        "delete", "删除", "移除", "清空",
        "batch.*delete", "批量删除",
        "export.*all", "全部导出",
        "reset", "重置", "恢复出厂",
        "unbind", "解绑", "注销",
    ])

    # 截图配置
    screenshot_dir: Path = Path("screenshots")
    screenshot_on_visit: bool = True
    screenshot_on_error: bool = True

    # 超时配置（优化：减少等待时间）
    page_timeout: int = 15000      # 页面加载超时15秒（原为30秒）
    element_timeout: int = 3000    # 元素等待超时3秒（原为5秒）
    wait_after_click: int = 500    # 点击后等待500ms（原为2000ms）
    wait_after_navigation: int = 1000  # 导航后等待1秒（原为3秒）

    # 性能优化选项
    enable_resource_interception: bool = True  # 启用资源拦截（禁用图片/CSS/字体）
    enable_screenshot: bool = True  # 是否启用截图（可关闭以加速）
    use_domcontentloaded: bool = True  # 使用 domcontentloaded 替代 networkidle

    # 输出配置
    output_file: Optional[Path] = None
    output_format: str = "json"  # json / html / markdown

    # 并发控制（优化：支持有限并发）
    max_concurrent: int = 2  # 允许2个并发（原为1，在安全的页面可并行）

    def should_skip(self, text: str, url: str = "") -> bool:
        """检查是否应该跳过该元素"""
        import re
        check_text = f"{text} {url}".lower()
        for pattern in self.skip_patterns:
            if re.search(pattern, check_text, re.IGNORECASE):
                return True
        return False


@dataclass
class VisitedPage:
    """已访问页面记录"""
    url: str
    title: str
    depth: int
    parent: Optional[str] = None
    screenshot: Optional[str] = None
    elements_found: int = 0
    elements_clicked: int = 0
    errors: List[str] = field(default_factory=list)
    visit_time: float = field(default_factory=time.time)
    duration_ms: int = 0

    @property
    def is_error(self) -> bool:
        return len(self.errors) > 0


@dataclass
class TraversalResult:
    """遍历结果"""
    visited_pages: List[VisitedPage] = field(default_factory=list)
    total_elements: int = 0
    clicked_elements: int = 0
    skipped_elements: int = 0
    error_count: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    @property
    def visited_count(self) -> int:
        return len(self.visited_pages)

    @property
    def duration_seconds(self) -> float:
        end = self.end_time or time.time()
        return end - self.start_time

    def to_dict(self) -> dict:
        return {
            "summary": {
                "visited_pages": self.visited_count,
                "total_elements": self.total_elements,
                "clicked_elements": self.clicked_elements,
                "skipped_elements": self.skipped_elements,
                "error_count": self.error_count,
                "duration_seconds": self.duration_seconds,
            },
            "pages": [
                {
                    "url": p.url,
                    "title": p.title,
                    "depth": p.depth,
                    "elements_found": p.elements_found,
                    "elements_clicked": p.elements_clicked,
                    "errors": p.errors,
                    "screenshot": p.screenshot,
                    "duration_ms": p.duration_ms,
                }
                for p in self.visited_pages
            ]
        }

    def save(self, output_file: Path):
        """保存结果到文件"""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2))
        logger.info(f"遍历结果已保存: {output_file}")


class TraversalEngine:
    """
    全功能遍历引擎

    遍历策略：
    1. 先认证登录
    2. 从首页开始，收集所有菜单项
    3. 逐个点击菜单，记录访问的页面
    4. 在每个页面收集功能按钮/链接/Tabs
    5. 逐个点击功能元素（跳过危险操作）
    6. 在表单页面测试字段交互
    """

    def __init__(self, config: TraversalConfig):
        self.config = config
        self.result = TraversalResult()
        self.visited_urls: Set[str] = set()
        self.visited_keys: Set[str] = set()  # url + title 组合去重
        self.auth_router: Optional[SmartAuthRouter] = None

        if config.auth_config:
            self.auth_router = SmartAuthRouter(config.auth_config)

        # 确保截图目录存在
        self.config.screenshot_dir.mkdir(parents=True, exist_ok=True)

    async def traverse(self, page: Page) -> TraversalResult:
        """
        执行全功能遍历

        Args:
            page: Playwright Page 对象

        Returns:
            TraversalResult: 遍历结果
        """
        logger.info("=" * 60)
        logger.info("开始全功能遍历测试")
        logger.info(f"起始 URL: {self.config.start_url}")
        logger.info(f"遍历级别: {[l.value for l in self.config.traversal_levels]}")
        logger.info(f"最大深度: {self.config.max_depth}")
        logger.info("=" * 60)

        try:
            # 1. 认证登录
            if self.auth_router:
                auth_result = await self.auth_router.authenticate(page)
                if not auth_result.success:
                    logger.error(f"认证失败: {auth_result.message}")
                    self.result.error_count += 1
                    return self.result
                logger.info("✅ 认证成功")

            # 2. 导航到起始页面
            await self._navigate_to(page, self.config.start_url or self.config.home_url)

            # 3. 开始遍历
            await self._traverse_level(
                page,
                level=TraversalLevel.MENU,
                depth=0,
                parent_url=page.url
            )

        except Exception as e:
            logger.error(f"遍历过程异常: {e}", exc_info=True)
            self.result.error_count += 1

        finally:
            self.result.end_time = time.time()
            logger.info("=" * 60)
            logger.info("遍历完成")
            logger.info(f"访问页面: {self.result.visited_count}")
            logger.info(f"发现元素: {self.result.total_elements}")
            logger.info(f"点击元素: {self.result.clicked_elements}")
            logger.info(f"跳过元素: {self.result.skipped_elements}")
            logger.info(f"错误数: {self.result.error_count}")
            logger.info(f"耗时: {self.result.duration_seconds:.1f} 秒")
            logger.info("=" * 60)

            # 保存结果
            if self.config.output_file:
                self.result.save(self.config.output_file)

        return self.result

    async def _traverse_level(self, page: Page, level: TraversalLevel, depth: int, parent_url: str):
        """递归遍历指定级别"""
        if depth >= self.config.max_depth:
            logger.debug(f"达到最大深度 {self.config.max_depth}，停止遍历")
            return

        if level not in self.config.traversal_levels:
            return

        current_url = page.url
        current_title = await page.title()

        # 记录当前页面
        await self._record_page(page, depth, parent_url)

        # 根据级别收集元素
        if level == TraversalLevel.MENU:
            elements = await self._collect_menu_elements(page)
        elif level == TraversalLevel.FUNCTION:
            elements = await self._collect_function_elements(page)
        elif level == TraversalLevel.FORM:
            elements = await self._collect_form_elements(page)
        else:
            elements = []

        logger.info(f"级别 {level.value} | 深度 {depth} | 发现 {len(elements)} 个元素")

        # 逐个处理元素
        for element_info in elements:
            await self._process_element(page, element_info, depth, level)

    async def _collect_menu_elements(self, page: Page) -> List[Dict]:
        """收集菜单元素"""
        elements = []

        for selector in self.config.menu_selectors:
            try:
                items = await page.query_selector_all(selector)
                for item in items:
                    try:
                        if not await item.is_visible():
                            continue

                        text = await item.text_content()
                        text = text.strip() if text else ""

                        # 跳过无文本或已访问的
                        if not text:
                            continue

                        # 检查是否需要跳过
                        if self.config.should_skip(text):
                            logger.debug(f"跳过菜单: {text}")
                            self.result.skipped_elements += 1
                            continue

                        elements.append({
                            "element": item,
                            "type": ElementType.MENU,
                            "text": text,
                            "selector": selector,
                        })
                    except Exception:
                        continue
            except Exception:
                continue

        self.result.total_elements += len(elements)
        return elements

    async def _collect_function_elements(self, page: Page) -> List[Dict]:
        """收集功能元素（按钮、链接、Tabs）"""
        elements = []
        seen_texts = set()

        for selector in self.config.function_selectors:
            try:
                items = await page.query_selector_all(selector)
                for item in items:
                    try:
                        if not await item.is_visible():
                            continue

                        text = await item.text_content()
                        text = text.strip() if text else ""

                        # 去重
                        if text in seen_texts:
                            continue
                        seen_texts.add(text)

                        # 跳过危险操作
                        if self.config.should_skip(text):
                            logger.debug(f"跳过功能: {text}")
                            self.result.skipped_elements += 1
                            continue

                        # 判断元素类型
                        element_type = self._detect_element_type(selector, text)

                        elements.append({
                            "element": item,
                            "type": element_type,
                            "text": text,
                            "selector": selector,
                        })
                    except Exception:
                        continue
            except Exception:
                continue

        self.result.total_elements += len(elements)
        return elements

    async def _collect_form_elements(self, page: Page) -> List[Dict]:
        """收集表单元素"""
        elements = []

        for selector in self.config.form_selectors:
            try:
                items = await page.query_selector_all(selector)
                for item in items:
                    try:
                        if not await item.is_visible():
                            continue

                        # 获取元素信息
                        tag_name = await item.evaluate("el => el.tagName.toLowerCase()")
                        input_type = await item.get_attribute("type") or "text"
                        placeholder = await item.get_attribute("placeholder") or ""
                        name = await item.get_attribute("name") or ""

                        elements.append({
                            "element": item,
                            "type": ElementType.FORM,
                            "text": placeholder or name or tag_name,
                            "selector": selector,
                            "tag": tag_name,
                            "input_type": input_type,
                        })
                    except Exception:
                        continue
            except Exception:
                continue

        self.result.total_elements += len(elements)
        return elements

    async def _process_element(self, page: Page, element_info: Dict, depth: int, current_level: TraversalLevel):
        """处理单个元素"""
        element = element_info["element"]
        text = element_info.get("text", "")
        element_type = element_info.get("type", ElementType.UNKNOWN)

        try:
            # 截图记录（点击前）- 只在启用截图时执行
            if self.config.screenshot_on_visit and self.config.enable_screenshot:
                screenshot_path = self.config.screenshot_dir / f"before_click_{depth}_{text[:20]}.png"
                await page.screenshot(path=str(screenshot_path))

            # 点击元素
            logger.info(f"点击 [{element_type.value}] {text}")

            # 检查是否会打开新窗口
            popup_promise = page.wait_for_event("popup", timeout=2000)

            try:
                await element.click(timeout=5000)
            except Exception:
                # 可能被遮挡，尝试 JavaScript 点击
                await element.evaluate("el => el.click()")

            # 等待页面稳定
            await page.wait_for_timeout(self.config.wait_after_click)

            # 检查新窗口
            try:
                popup = await popup_promise
                if popup:
                    logger.info(f"检测到新窗口: {popup.url}")
                    await popup.close()
                    return
            except Exception:
                pass  # 没有新窗口

            # 检查 URL 是否变化
            new_url = page.url
            new_title = await page.title()
            page_key = f"{new_url}#{new_title}"

            if page_key not in self.visited_keys and new_url != "about:blank":
                self.visited_keys.add(page_key)
                self.result.clicked_elements += 1

                # 记录访问
                await self._record_page(page, depth + 1, page.url)

                # 递归遍历下一级
                next_level = self._get_next_level(current_level)
                if next_level:
                    await self._traverse_level(page, next_level, depth + 1, new_url)

                # 返回上级页面
                if new_url != page.url:
                    await page.go_back()
                    await page.wait_for_timeout(self.config.wait_after_navigation)

        except Exception as e:
            logger.warning(f"处理元素失败 [{text}]: {e}")
            # 截图记录错误（只在启用截图时执行）
            if self.config.screenshot_on_error and self.config.enable_screenshot:
                error_path = self.config.screenshot_dir / f"error_{depth}_{text[:20]}_{int(time.time())}.png"
                try:
                    await page.screenshot(path=str(error_path))
                except Exception:
                    pass

    def _get_next_level(self, current: TraversalLevel) -> Optional[TraversalLevel]:
        """获取下一个遍历级别"""
        levels = self.config.traversal_levels
        try:
            idx = levels.index(current)
            if idx + 1 < len(levels):
                return levels[idx + 1]
        except ValueError:
            pass
        return None

    def _detect_element_type(self, selector: str, text: str) -> ElementType:
        """检测元素类型"""
        selector_lower = selector.lower()
        text_lower = text.lower()

        if "tab" in selector_lower or "tab" in text_lower:
            return ElementType.TAB
        elif "button" in selector_lower:
            return ElementType.BUTTON
        elif "table" in selector_lower:
            return ElementType.TABLE
        elif "modal" in selector_lower or "dialog" in selector_lower:
            return ElementType.MODAL
        elif "a[" in selector_lower or "href" in selector_lower:
            return ElementType.LINK
        else:
            return ElementType.UNKNOWN

    async def _record_page(self, page: Page, depth: int, parent_url: str):
        """记录页面访问"""
        url = page.url
        title = await page.title()
        page_key = f"{url}#{title}"

        if page_key in self.visited_keys:
            return

        self.visited_keys.add(page_key)
        self.visited_urls.add(url)

        # 截图
        screenshot = None
        if self.config.screenshot_on_visit:
            screenshot_name = f"page_{len(self.result.visited_pages):03d}_{depth}_{title[:30]}.png"
            screenshot_path = self.config.screenshot_dir / screenshot_name
            try:
                await page.screenshot(path=str(screenshot_path))
                screenshot = str(screenshot_path)
            except Exception:
                pass

        visited = VisitedPage(
            url=url,
            title=title,
            depth=depth,
            parent=parent_url,
            screenshot=screenshot,
        )

        self.result.visited_pages.append(visited)
        logger.info(f"📄 记录页面 [{depth}] {title[:40]} | {url[:60]}")

    async def _navigate_to(self, page: Page, url: str):
        """导航到指定页面（高性能版：使用 domcontentloaded 替代 networkidle）"""
        if not url:
            return

        logger.info(f"导航到: {url}")
        try:
            # 性能优化：根据配置选择等待策略
            wait_until = "domcontentloaded" if self.config.use_domcontentloaded else "networkidle"
            await page.goto(url, wait_until=wait_until, timeout=self.config.page_timeout)
            # 只在非零时等待
            if self.config.wait_after_navigation > 0:
                await page.wait_for_timeout(self.config.wait_after_navigation)
        except PwTimeout:
            logger.warning(f"页面加载超时: {url}")
        except Exception as e:
            logger.warning(f"导航失败: {url} - {e}")


# =============================================================================
# 预设配置（针对具体项目）
# =============================================================================

def create_peets_traversal_config(
    token: Optional[str] = None,
    username: str = "lianqiao_peets",
    password: str = "Lq123456",
    env: str = "staging",
    output_dir: Path = Path("traversal_results")
) -> TraversalConfig:
    """
    创建 Peets 营销云遍历配置

    Args:
        token: 预获取的 Token（可选）
        username: 用户名（验证码模式使用）
        password: 密码（验证码模式使用）
        env: 环境 (staging/prod)
        output_dir: 输出目录

    Returns:
        TraversalConfig
    """
    base_url = "https://console-stg-internal.peets.cn" if env == "staging" else "https://console.peets.cn"

    auth_config = AuthConfig(
        login_url=f"{base_url}/dtcloud-console-web-pc/#/login",
        target_url=f"{base_url}/dtcloud-console-web-pc/",
        username=username,
        password=password,
        token=token,
        token_param_name="accessToken",
        token_storage_keys=["token", "accessToken", "Authorization", "auth_token"],
        captcha_length=4,
        captcha_selector="img.indexStyle_imgStyle-Vra-Jpgm",
        captcha_input_selector="input#login_validateCode",
        captcha_ocr_enabled=True,
        auto_detect_mode=True,
        preferred_mode=AuthMode.TOKEN_URL if token else AuthMode.CAPTCHA,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    return TraversalConfig(
        start_url=f"{base_url}/dtcloud-console-web-pc/",
        home_url=f"{base_url}/dtcloud-console-web-pc/",
        auth_config=auth_config,
        max_depth=3,
        traversal_levels=[TraversalLevel.MENU, TraversalLevel.FUNCTION, TraversalLevel.FORM],
        menu_selectors=[
            ".ant-menu-item",
            ".ant-menu-submenu",
            ".ant-menu-submenu-title",
            "[class*='menu-item']",
            "[class*='submenu']",
        ],
        function_selectors=[
            "button:not([disabled])",
            ".ant-tabs-tab",
            "[class*='tab']",
            "a[href]",
            ".ant-btn",
            "[class*='btn']",
        ],
        skip_patterns=[
            "logout", "退出", "登出",
            "delete", "删除", "移除",
            "batch.*delete", "批量删除",
            "export.*all", "全部导出",
            "reset", "重置",
            "unbind", "解绑", "注销",
        ],
        screenshot_dir=output_dir / f"screenshots_{timestamp}",
        output_file=output_dir / f"traversal_result_{timestamp}.json",
        page_timeout=30000,
        wait_after_click=2000,
        wait_after_navigation=3000,
    )


def create_xiaoyangshengjian_traversal_config(
    token: Optional[str] = None,
    username: str = "",
    password: str = "",
    base_url: str = "",
    output_dir: Path = Path("traversal_results")
) -> TraversalConfig:
    """
    创建小杨生煎营销云遍历配置

    Args:
        token: 预获取的 Token
        username: 用户名
        password: 密码
        base_url: 系统基础 URL
        output_dir: 输出目录

    Returns:
        TraversalConfig
    """
    auth_config = AuthConfig(
        login_url=f"{base_url}/login",
        target_url=f"{base_url}/",
        username=username,
        password=password,
        token=token,
        preferred_mode=AuthMode.TOKEN_URL if token else AuthMode.CAPTCHA,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    return TraversalConfig(
        start_url=f"{base_url}/",
        home_url=f"{base_url}/",
        auth_config=auth_config,
        max_depth=3,
        traversal_levels=[TraversalLevel.MENU, TraversalLevel.FUNCTION, TraversalLevel.FORM],
        screenshot_dir=output_dir / f"xy_screenshots_{timestamp}",
        output_file=output_dir / f"xy_traversal_{timestamp}.json",
    )


# =============================================================================
# 便捷函数
# =============================================================================

async def traverse_page(
    page: Page,
    start_url: str,
    token: Optional[str] = None,
    username: str = "",
    password: str = "",
    max_depth: int = 3,
    output_dir: Path = Path("traversal_results")
) -> TraversalResult:
    """
    快速遍历页面

    Args:
        page: Playwright Page
        start_url: 起始 URL
        token: 预获取 Token
        username: 用户名（验证码模式）
        password: 密码（验证码模式）
        max_depth: 最大遍历深度
        output_dir: 输出目录

    Returns:
        TraversalResult
    """
    auth_config = AuthConfig(
        login_url=start_url,
        target_url=start_url,
        username=username,
        password=password,
        token=token,
        preferred_mode=AuthMode.TOKEN_URL if token else AuthMode.CAPTCHA,
    )

    config = TraversalConfig(
        start_url=start_url,
        auth_config=auth_config,
        max_depth=max_depth,
        screenshot_dir=output_dir / "screenshots",
        output_file=output_dir / "traversal_result.json",
    )

    engine = TraversalEngine(config)
    return await engine.traverse(page)
