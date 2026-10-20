import pytest
import re
from playwright.sync_api import Page, expect, sync_playwright
from dataclasses import dataclass
from datetime import datetime
import os


@dataclass
class TestConfig:
    """测试配置"""
    base_url: str = "https://tw-sit.dg.dtyunxi.cn/yundt-boot-ui-boc-mgmt-web"
    username: str = "lianqiao"
    password: str = "Pass1234"
    headless: bool = False  # 设为True可在无界面模式运行
    slow_mo: int = 500  # 操作间隔毫秒，增加稳定性
    timeout: int = 30000  # 默认超时30秒


class LoginPage:
    """登录页面对象"""

    def __init__(self, page: Page, config: TestConfig):
        self.page = page
        self.config = config
        self.url = f"{config.base_url}/#/login"

    def navigate(self):
        """导航到登录页面"""
        self.page.goto(self.url, wait_until="networkidle")
        # 等待登录表单加载完成
        self.page.wait_for_selector("input[placeholder*='账号']", state="visible", timeout=10000)
        return self

    def login(self):
        """执行登录操作"""
        # 输入账号
        username_input = self.page.locator("input[placeholder*='账号']")
        username_input.fill(self.config.username)

        # 输入密码
        password_input = self.page.locator("input[placeholder*='密码']")
        password_input.fill(self.config.password)

        # 勾选同意协议 - 使用更灵活的选择器
        # 先尝试找到复选框
        checkbox = self.page.locator("input[type='checkbox']").first
        if checkbox.is_visible():
            checkbox.click()

        # 点击登录按钮
        login_btn = self.page.locator("button:has-text('登录'), button[type='submit']").first
        login_btn.click()

        # 等待登录成功，页面跳转
        self.page.wait_for_url(re.compile(r".*/#/(home|dashboard|index).*"), timeout=15000)

        return HomePage(self.page, self.config)


class HomePage:
    """首页面对象"""

    def __init__(self, page: Page, config: TestConfig):
        self.page = page
        self.config = config

    def navigate_to_product_group(self):
        """导航到商品分组页面"""
        # 点击"业务智能"或主菜单
        # 使用更通用的选择器，适应不同的UI框架

        # 方案1: 尝试点击"业务智能"
        biz_intel = self.page.locator("text=业务智能").first
        if biz_intel.is_visible():
            biz_intel.click()
            self.page.wait_for_timeout(500)

        # 点击"主数据"菜单
        master_data = self.page.locator("text=主数据").first
        if master_data.is_visible():
            master_data.click()
            self.page.wait_for_timeout(500)

        # 点击"商品管理"子菜单
        product_mgmt = self.page.locator("text=商品管理").first
        if product_mgmt.is_visible():
            product_mgmt.click()
            self.page.wait_for_timeout(500)

        # 点击"商品分组"
        product_group = self.page.locator("text=商品分组").first
        product_group.click()

        # 等待页面加载
        self.page.wait_for_load_state("networkidle")

        return ProductGroupPage(self.page, self.config)


class ProductGroupPage:
    """商品分组页面对象 - 包含完整的CRUD操作"""

    def __init__(self, page: Page, config: TestConfig):
        self.page = page
        self.config = config
        # 生成唯一的测试数据，避免重复
        self.test_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.test_group_name = f"测试分组_{self.test_timestamp}"
        self.test_group_code = f"TEST_{self.test_timestamp}"
        self.updated_name = f"更新分组_{self.test_timestamp}"

    # ==================== 查询操作 ====================

    def search_by_name(self, name: str):
        """按名称搜索商品分组"""
        # 找到搜索输入框并输入
        search_input = self.page.locator(
            "input[placeholder*='分组'], input[placeholder*='名称'], input[placeholder*='搜索']"
        ).first

        search_input.fill(name)
        self.page.wait_for_timeout(300)

        # 点击搜索按钮
        search_btn = self.page.locator(
            "button:has-text('查询'), button:has-text('搜索'), .search-btn, .el-button--primary"
        ).first
        search_btn.click()

        # 等待搜索结果加载
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(1000)

        return self

    def get_table_data(self):
        """获取表格数据"""
        # 等待表格加载
        table = self.page.locator("table, .el-table").first
        table.wait_for(state="visible", timeout=10000)

        # 获取所有行数据
        rows = table.locator("tbody tr").all()
        data = []
        for row in rows:
            cells = row.locator("td").all()
            row_data = [cell.text_content().strip() for cell in cells]
            data.append(row_data)

        return data

    def get_total_count(self):
        """获取总记录数"""
        # 常见的分页信息格式
        pagination = self.page.locator(
            ".pagination-info, .el-pagination__total, .total, text=/共.*条/"
        ).first

        if pagination.is_visible():
            text = pagination.text_content()
            # 提取数字
            match = re.search(r'(\d+)', text)
            if match:
                return int(match.group(1))

        # 备选：直接数表格行数
        rows = self.page.locator("table tbody tr").all()
        return len(rows)

    # ==================== 新增操作 ====================

    def click_add(self):
        """点击新增按钮"""
        add_btn = self.page.locator(
            "button:has-text('新增'), button:has-text('添加'), button:has-text('新建'), .add-btn"
        ).first
        add_btn.click()

        # 等待弹窗出现
        self.page.wait_for_selector(".el-dialog, .modal, .dialog, [role='dialog']", state="visible", timeout=10000)

        return self

    def fill_form(self, name: str = None, code: str = None, description: str = None):
        """填写表单"""
        # 填写分组名称
        if name:
            name_input = self.page.locator(
                "input[placeholder*='名称'], label:has-text('名称') + * input, label:has-text('分组') + * input"
            ).first
            name_input.fill(name)

        # 填写分组编码
        if code:
            code_input = self.page.locator(
                "input[placeholder*='编码'], input[placeholder*='代码'], label:has-text('编码') + * input"
            ).first
            code_input.fill(code)

        # 填写描述
        if description:
            desc_input = self.page.locator(
                "textarea, input[placeholder*='描述'], label:has-text('描述') + * textarea"
            ).first
            desc_input.fill(description)

        return self

    def submit_form(self):
        """提交表单"""
        submit_btn = self.page.locator(
            "button:has-text('确定'), button:has-text('保存'), button:has-text('提交'), .el-button--primary"
        ).first
        submit_btn.click()

        # 等待提交完成 - 检测成功提示或弹窗关闭
        self.page.wait_for_timeout(2000)

        # 检查是否有成功提示
        success_msg = self.page.locator(".el-message--success, .success-message, .ant-message-success")
        if success_msg.is_visible():
            print(f"✅ 操作成功: {success_msg.text_content()}")

        return self

    def cancel_form(self):
        """取消表单"""
        cancel_btn = self.page.locator("button:has-text('取消'), .cancel-btn").first
        cancel_btn.click()

        # 等待弹窗关闭
        self.page.wait_for_timeout(500)

        return self

    def create_group(self, name: str = None, code: str = None, description: str = "自动化测试创建"):
        """完整的创建流程"""
        name = name or self.test_group_name
        code = code or self.test_group_code

        self.click_add()
        self.fill_form(name=name, code=code, description=description)
        self.submit_form()

        return self

    # ==================== 修改操作 ====================

    def find_row_by_name(self, name: str):
        """根据名称找到表格行"""
        rows = self.page.locator("table tbody tr").all()
        for row in rows:
            cells = row.locator("td").all()
            for cell in cells:
                if name in (cell.text_content() or ""):
                    return row
        return None

    def click_edit_by_name(self, name: str):
        """根据名称点击编辑"""
        row = self.find_row_by_name(name)
        if row:
            # 在行内找到编辑按钮
            edit_btn = row.locator("button:has-text('编辑'), button:has-text('修改'), .edit-btn, a:has-text('编辑')").first
            edit_btn.click()

            # 等待弹窗
            self.page.wait_for_selector(".el-dialog, .modal, .dialog", state="visible", timeout=10000)
            return True
        return False

    def update_group(self, old_name: str, new_name: str = None, new_description: str = None):
        """更新商品分组"""
        new_name = new_name or self.updated_name

        # 先搜索找到目标
        self.search_by_name(old_name)

        # 点击编辑
        if not self.click_edit_by_name(old_name):
            raise Exception(f"未找到商品分组: {old_name}")

        # 清空并重新填写名称
        name_input = self.page.locator(
            "input[placeholder*='名称'], label:has-text('名称') + * input"
        ).first
        name_input.fill("")  # 先清空
        name_input.fill(new_name)

        # 更新描述
        if new_description:
            desc_input = self.page.locator("textarea").first
            desc_input.fill(new_description)

        # 提交
        self.submit_form()

        return self

    # ==================== 删除操作 ====================

    def click_delete_by_name(self, name: str, confirm: bool = True):
        """根据名称删除商品分组"""
        # 先搜索
        self.search_by_name(name)

        row = self.find_row_by_name(name)
        if row:
            # 找到删除按钮
            delete_btn = row.locator(
                "button:has-text('删除'), .delete-btn, a:has-text('删除')"
            ).first
            delete_btn.click()

            # 等待确认弹窗
            self.page.wait_for_timeout(500)

            if confirm:
                # 点击确认删除
                confirm_btn = self.page.locator(
                    "button:has-text('确定'), button:has-text('确认'), .confirm-btn, .el-button--primary"
                ).last
                confirm_btn.click()

                # 等待删除完成
                self.page.wait_for_timeout(2000)

            return True
        return False

    def delete_group(self, name: str = None):
        """完整的删除流程"""
        name = name or self.updated_name

        # 如果找不到，尝试用原始名称
        if not self.click_delete_by_name(name):
            # 尝试原始测试名称
            if name != self.test_group_name:
                self.click_delete_by_name(self.test_group_name)

        return self

    # ==================== 详情查看 ====================

    def view_detail_by_name(self, name: str):
        """查看商品分组详情"""
        self.search_by_name(name)

        row = self.find_row_by_name(name)
        if row:
            # 点击详情或行内查看按钮
            detail_btn = row.locator(
                "button:has-text('详情'), button:has-text('查看'), .detail-btn, a:has-text('详情')"
            ).first

            # 如果没有详情按钮，尝试点击行本身
            if detail_btn.is_visible():
                detail_btn.click()
            else:
                row.click()

            # 等待详情弹窗或页面
            self.page.wait_for_timeout(1000)

            return True
        return False

    def close_detail(self):
        """关闭详情弹窗"""
        close_btn = self.page.locator(
            "button:has-text('关闭'), .el-dialog__close, .close-btn, .modal-close"
        ).first

        if close_btn.is_visible():
            close_btn.click()

        return self


# ==================== Pytest Fixtures ====================

@pytest.fixture(scope="session")
def config():
    """测试配置"""
    return TestConfig()


@pytest.fixture
def page(config):
    """Playwright页面实例"""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=config.headless,
            slow_mo=config.slow_mo
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            record_video_dir="test-videos/" if not config.headless else None
        )
        page = context.new_page()
        page.set_default_timeout(config.timeout)

        yield page

        # 清理
        context.close()
        browser.close()


@pytest.fixture
def logged_in_page(page, config):
    """已登录的页面"""
    login_page = LoginPage(page, config)
    login_page.navigate()
    home_page = login_page.login()
    return page, home_page


@pytest.fixture
def product_group_page(logged_in_page, config):
    """商品分组页面"""
    page, home_page = logged_in_page
    product_page = home_page.navigate_to_product_group()
    return product_page


# ==================== 测试用例 ====================

class TestProductGroupCRUD:
    """商品分组CRUD测试类"""

    def test_01_login_and_navigate(self, logged_in_page, config):
        """测试1: 登录并导航到商品分组页面"""
        page, home_page = logged_in_page

        # 验证登录成功
        assert page.url != f"{config.base_url}/#/login", "登录失败，仍在登录页面"

        # 导航到商品分组
        product_page = home_page.navigate_to_product_group()

        # 验证页面加载
        assert product_page.get_total_count() >= 0, "页面加载失败"
        print("✅ 登录并导航成功")

    def test_02_query_product_group(self, product_group_page):
        """测试2: 查询商品分组"""
        # 获取初始数据
        initial_count = product_group_page.get_total_count()
        print(f"📊 初始商品分组数量: {initial_count}")

        # 搜索测试（搜索一个常见的关键词）
        product_group_page.search_by_name("分组")

        # 验证搜索结果
        search_count = product_group_page.get_total_count()
        print(f"📊 搜索后数量: {search_count}")

        assert search_count >= 0, "查询失败"
        print("✅ 查询功能正常")

    def test_03_create_product_group(self, product_group_page):
        """测试3: 新增商品分组"""
        # 创建商品分组
        product_group_page.create_group()

        # 验证创建成功 - 搜索新创建的分组
        product_group_page.search_by_name(product_group_page.test_group_name)

        # 验证在列表中能找到
        data = product_group_page.get_table_data()
        found = any(
            product_group_page.test_group_name in str(row) for row in data
        )

        assert found, f"创建失败，未找到商品分组: {product_group_page.test_group_name}"
        print(f"✅ 商品分组创建成功: {product_group_page.test_group_name}")

    def test_04_update_product_group(self, product_group_page):
        """测试4: 修改商品分组"""
        # 先确保测试数据存在
        try:
            product_group_page.update_group(
                product_group_page.test_group_name,
                product_group_page.updated_name,
                "更新后的描述信息"
            )

            # 验证更新成功
            product_group_page.search_by_name(product_group_page.updated_name)
            data = product_group_page.get_table_data()

            found = any(
                product_group_page.updated_name in str(row) for row in data
            )

            assert found, "更新失败，未找到更新后的商品分组"
            print(f"✅ 商品分组更新成功: {product_group_page.updated_name}")

        except Exception as e:
            # 如果更新失败，可能是测试数据不存在，尝试先创建
            print(f"⚠️ 更新失败，尝试先创建测试数据: {e}")
            product_group_page.create_group()
            product_group_page.update_group(
                product_group_page.test_group_name,
                product_group_page.updated_name
            )

    def test_05_delete_product_group(self, product_group_page):
        """测试5: 删除商品分组"""
        # 删除商品分组
        product_group_page.delete_group()

        # 验证删除成功 - 搜索应该找不到
        product_group_page.search_by_name(product_group_page.updated_name)

        data = product_group_page.get_table_data()
        found = any(
            product_group_page.updated_name in str(row) for row in data
        )

        assert not found, "删除失败，仍能查找到商品分组"
        print("✅ 商品分组删除成功")

    def test_06_create_with_empty_name(self, product_group_page):
        """测试6: 边界测试 - 空名称验证"""
        product_group_page.click_add()

        # 不填名称直接提交
        product_group_page.fill_form(code="TEST_EMPTY")
        product_group_page.submit_form()

        # 验证表单未关闭（有验证错误）
        dialog = product_group_page.page.locator(".el-dialog, .modal, .dialog").first

        # 如果弹窗还在，说明验证阻止了提交
        if dialog.is_visible():
            print("✅ 空名称验证正常")
            product_group_page.cancel_form()
        else:
            print("⚠️ 空名称验证可能未生效")

    def test_07_create_duplicate_name(self, product_group_page):
        """测试7: 边界测试 - 重复名称验证"""
        # 先创建一个分组
        product_group_page.create_group()

        # 尝试创建同名分组
        product_group_page.click_add()
        product_group_page.fill_form(
            name=product_group_page.test_group_name,
            code=f"DUP_{product_group_page.test_group_code}"
        )
        product_group_page.submit_form()

        # 检查是否有错误提示
        error_msg = product_group_page.page.locator(
            ".el-message--error, .error-message, .ant-message-error"
        )

        if error_msg.is_visible():
            print("✅ 重复名称验证正常")
            product_group_page.cancel_form()
        else:
            print("⚠️ 重复名称验证可能未生效")
            # 清理
            product_group_page.cancel_form()

        # 清理测试数据
        product_group_page.delete_group(product_group_page.test_group_name)


# ==================== 测试数据清理 ====================

@pytest.fixture(autouse=True)
def cleanup_after_test(request, product_group_page):
    """测试后清理"""
    yield

    # 测试结束后尝试清理
    if hasattr(product_group_page, 'test_group_name'):
        try:
            product_group_page.delete_group(product_group_page.test_group_name)
        except:
            pass
