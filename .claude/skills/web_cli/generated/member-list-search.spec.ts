import { test, expect, Page } from '@playwright/test';

/**
 * 营销云 - 会员管理-列表查询与搜索 完整测试脚本
 *
 * 测试覆盖:
 * 1. TC-MLS-001: 默认加载列表 - 验证搜索区域和列表渲染
 * 2. TC-MLS-002: 按手机号码搜索 - 搜索有结果的记录
 * 3. TC-MLS-003: 重置搜索 - 搜索后重置恢复默认列表
 * 4. TC-MLS-004: 搜索无结果 - 搜索不存在的号码显示空状态
 * 5. TC-MLS-005: 分页页码切换 - 翻页验证
 * 6. TC-MLS-006: 每页条数切换 - 修改 pageSize
 *
 * 项目标识符: PR-1
 * 子功能: 会员管理-列表查询与搜索
 * 入口: 数盈·营销云 PC 端
 * 登录地址: https://console-uat.xysjg.com/front/dtcloud-console-web-pc/#/login
 */

// ======== 环境配置 ========
const LOGIN_URL = 'https://console-uat.xysjg.com/front/dtcloud-console-web-pc/#/login';
const USERNAME = 'andan';
const PASSWORD = 'andan123';

/**
 * 测试数据常量
 */
const TEST_PHONE_EXISTING = '13800138000';
const TEST_PHONE_NONEXISTENT = '99999999999';

// ======== 辅助函数 ========

/**
 * 登录并导航到会员管理页面
 * 该函数在 beforeEach 中调用，确保每个测试用例独立
 */
async function loginAndNavigateToMemberList(page: Page) {
  // Step 1: goto login page
  await page.goto(LOGIN_URL, { waitUntil: 'networkidle', timeout: 60_000 });

  // Step 2: fill username
  const usernameInput = page.getByRole('textbox', { name: '请输入用户名' });
  await usernameInput.waitFor({ state: 'visible', timeout: 15_000 });
  await usernameInput.fill(USERNAME);

  // Step 3: fill password
  const passwordInput = page.getByRole('textbox', { name: '请输入密码' });
  await passwordInput.waitFor({ state: 'visible', timeout: 10_000 });
  await passwordInput.fill(PASSWORD);

  // Step 4: click login button
  const loginButton = page.getByRole('button', { name: '登 录' });
  await loginButton.click();
  await page.waitForLoadState('networkidle', { timeout: 30_000 });

  // Step 5: click 数盈·营销云 entry
  const marketingCloudEntry = page.getByText('数盈·营销云').first();
  await marketingCloudEntry.waitFor({ state: 'visible', timeout: 20_000 });
  await marketingCloudEntry.click();
  await page.waitForLoadState('networkidle', { timeout: 30_000 });

  // Step 6: click 用户 menu
  const userMenu = page.getByText('用户').first();
  await userMenu.waitFor({ state: 'visible', timeout: 15_000 });
  await userMenu.click();

  // Step 7: click 会员管理 sub-menu
  const memberMenu = page.locator('menuitem').filter({ hasText: '会员管理' });
  await memberMenu.waitFor({ state: 'visible', timeout: 15_000 });
  await memberMenu.click();
  await page.waitForLoadState('networkidle', { timeout: 30_000 });
}

// ======== 测试套件 ========

test.describe('会员管理-列表查询与搜索', () => {
  test.beforeEach(async ({ page }) => {
    await loginAndNavigateToMemberList(page);
  });

  /**
   * TC-MLS-001: 默认加载列表
   *
   * 验证进入会员管理页面后:
   * - 搜索区域可见（手机号码输入框、搜索按钮、重置按钮、新建按钮）
   * - 列表表格已渲染并包含数据行
   */
  test('默认加载列表', async ({ page }) => {
    // 等待页面完全加载
    await page.waitForTimeout(2000);

    // 验证搜索区域元素可见
    const phoneInput = page.getByRole('textbox', { name: '手机号码' });
    await expect(phoneInput).toBeVisible({ timeout: 10_000 });

    const searchButton = page.getByRole('button', { name: '搜索' });
    await expect(searchButton).toBeVisible();

    const resetButton = page.getByRole('button', { name: '重置' });
    await expect(resetButton).toBeVisible();

    const createButton = page.getByRole('button', { name: '新建' });
    await expect(createButton).toBeVisible();

    // 验证表格包含数据行（至少有一行数据）
    const tableRows = page.locator('table tbody tr');
    const rowCount = await tableRows.count();
    expect(rowCount).toBeGreaterThan(0);
  });

  /**
   * TC-MLS-002: 按手机号码搜索
   *
   * 输入手机号码并搜索:
   * - 填写已有手机号 "13800138000"
   * - 点击搜索按钮
   * - 验证结果表格包含该手机号码
   */
  test('按手机号码搜索', async ({ page }) => {
    await page.waitForTimeout(2000);

    // 填写手机号码
    const phoneInput = page.getByRole('textbox', { name: '手机号码' });
    await phoneInput.waitFor({ state: 'visible', timeout: 10_000 });
    await phoneInput.fill(TEST_PHONE_EXISTING);

    // 点击搜索
    const searchButton = page.getByRole('button', { name: '搜索' });
    await searchButton.click();
    await page.waitForLoadState('networkidle', { timeout: 20_000 });
    await page.waitForTimeout(2000);

    // 验证搜索结果中包含该手机号码
    const tableBody = page.locator('table tbody');
    await expect(tableBody).toContainText(TEST_PHONE_EXISTING, { timeout: 10_000 });

    // 验证至少有一行结果
    const rows = page.locator('table tbody tr');
    const rowCount = await rows.count();
    expect(rowCount).toBeGreaterThan(0);
  });

  /**
   * TC-MLS-003: 重置搜索
   *
   * 搜索后点击重置:
   * - 输入手机号并搜索
   * - 点击重置按钮
   * - 验证手机号码输入框清空
   * - 验证列表恢复默认状态（多行数据）
   */
  test('重置搜索', async ({ page }) => {
    await page.waitForTimeout(2000);

    // 填写手机号码并搜索
    const phoneInput = page.getByRole('textbox', { name: '手机号码' });
    await phoneInput.waitFor({ state: 'visible', timeout: 10_000 });
    await phoneInput.fill(TEST_PHONE_EXISTING);

    const searchButton = page.getByRole('button', { name: '搜索' });
    await searchButton.click();
    await page.waitForLoadState('networkidle', { timeout: 20_000 });
    await page.waitForTimeout(2000);

    // 点击重置
    const resetButton = page.getByRole('button', { name: '重置' });
    await resetButton.click();
    await page.waitForLoadState('networkidle', { timeout: 20_000 });
    await page.waitForTimeout(2000);

    // 验证搜索框已清空
    await expect(phoneInput).toHaveValue('', { timeout: 10_000 });

    // 验证列表恢复默认状态（多行数据）
    const rows = page.locator('table tbody tr');
    const rowCount = await rows.count();
    expect(rowCount).toBeGreaterThan(0);
  });

  /**
   * TC-MLS-004: 搜索无结果
   *
   * 搜索不存在的手机号:
   * - 填写不存在的号码 "99999999999"
   * - 点击搜索
   * - 验证 "暂无数据" 空状态提示出现
   */
  test('搜索无结果', async ({ page }) => {
    await page.waitForTimeout(2000);

    // 填写不存在的手机号码
    const phoneInput = page.getByRole('textbox', { name: '手机号码' });
    await phoneInput.waitFor({ state: 'visible', timeout: 10_000 });
    await phoneInput.fill(TEST_PHONE_NONEXISTENT);

    // 点击搜索
    const searchButton = page.getByRole('button', { name: '搜索' });
    await searchButton.click();
    await page.waitForLoadState('networkidle', { timeout: 20_000 });
    await page.waitForTimeout(2000);

    // 验证 "暂无数据" 空状态提示出现
    const emptyStateText = page.getByText('暂无数据');
    await expect(emptyStateText).toBeVisible({ timeout: 10_000 });
  });

  /**
   * TC-MLS-005: 分页页码切换
   *
   * 验证分页功能:
   * - 点击第 2 页 → 验证数据变化
   * - 点击第 3 页 → 验证数据变化
   * - 点击第 4 页 → 验证数据变化
   */
  test('分页页码切换', async ({ page }) => {
    await page.waitForTimeout(2000);

    // 逐页点击并验证
    const pageNumbers = ['2', '3', '4'];
    for (const pageNum of pageNumbers) {
      // 定位页码按钮
      const pageButton = page.locator('.ant-pagination-item').filter({ hasText: pageNum });
      await pageButton.waitFor({ state: 'visible', timeout: 10_000 });
      await pageButton.click();
      await page.waitForLoadState('networkidle', { timeout: 20_000 });
      await page.waitForTimeout(2000);

      // 验证当前页码激活
      await expect(pageButton).toHaveClass(/ant-pagination-item-active/, { timeout: 5_000 });

      // 验证表格仍有数据
      const rows = page.locator('table tbody tr');
      const rowCount = await rows.count();
      expect(rowCount).toBeGreaterThan(0);
    }
  });

  /**
   * TC-MLS-006: 每页条数切换
   *
   * 修改每页显示条数:
   * - 打开 pageSize 下拉选择框
   * - 选择 20 条/页
   * - 验证页面显示更多数据行
   */
  test('每页条数切换', async ({ page }) => {
    await page.waitForTimeout(2000);

    // 打开每页条数选择器（Ant Design 分页组件）
    const pageSizeSelector = page.locator('.ant-select').filter({ hasText: /10|条\/页/ }).first();
    await pageSizeSelector.waitFor({ state: 'visible', timeout: 10_000 });
    await pageSizeSelector.click();
    await page.waitForTimeout(1000);

    // 从下拉菜单中选择 20 条/页
    const option20 = page.getByRole('option', { name: /20/ }).first();
    await option20.waitFor({ state: 'visible', timeout: 5_000 });
    await option20.click();
    await page.waitForLoadState('networkidle', { timeout: 20_000 });
    await page.waitForTimeout(2000);

    // 验证选择器已显示 20 条/页
    await expect(pageSizeSelector).toContainText('20', { timeout: 5_000 });

    // 验证表格行数增加（应大于默认的 10 条）
    const rows = page.locator('table tbody tr');
    const rowCount = await rows.count();
    expect(rowCount).toBeGreaterThan(10);
  });
});
