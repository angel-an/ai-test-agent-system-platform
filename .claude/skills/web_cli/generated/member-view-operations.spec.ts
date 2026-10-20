import { test, expect } from '@playwright/test';

/**
 * 营销云 - 会员管理-会员查看与操作 完整测试脚本
 *
 * 测试覆盖:
 * 1. TC-MV-001: 会员查看详情
 * 2. TC-MV-002: 冻结会员
 * 3. TC-MV-003: 注销会员
 * 4. TC-MV-004: 取消冻结操作
 *
 * 项目标识符: PR-3
 * 子功能: 会员管理-会员查看与操作
 * 入口: 数盈·营销云 (https://console-uat.xysjg.com)
 */

// ======== 环境配置 ========
const LOGIN_URL =
  'https://console-uat.xysjg.com/front/dtcloud-console-web-pc/#/login';
const USERNAME = 'andan';
const PASSWORD = 'andan123';

// ======== 辅助函数 ========

/**
 * 登录并导航到会员管理列表页面
 * 此函数在每个 beforeEach 中被调用，确保每个测试独立
 */
async function loginAndNavigateToMemberList(page: import('@playwright/test').Page) {
  // 1. 打开登录页
  await page.goto(LOGIN_URL, { waitUntil: 'networkidle', timeout: 30_000 });

  // 2. 登录
  const usernameInput = page.getByRole('textbox', { name: '请输入用户名' });
  await usernameInput.waitFor({ state: 'visible', timeout: 15_000 });
  await usernameInput.fill(USERNAME);

  const passwordInput = page.getByRole('textbox', { name: '请输入密码' });
  await passwordInput.waitFor({ state: 'visible', timeout: 10_000 });
  await passwordInput.fill(PASSWORD);

  const loginButton = page.getByRole('button', { name: '登 录' });
  await loginButton.click();

  // 等待登录完成并跳转
  await page.waitForLoadState('networkidle', { timeout: 30_000 });
  await page.waitForTimeout(3000);

  // 3. 点击"数盈·营销云"入口
  const marketingCloudEntry = page.getByText('数盈·营销云').first();
  await marketingCloudEntry.waitFor({ state: 'visible', timeout: 15_000 });
  await marketingCloudEntry.click();
  await page.waitForLoadState('networkidle', { timeout: 30_000 });
  await page.waitForTimeout(2000);

  // 4. 点击"用户"菜单
  const userMenu = page.getByText('用户').first();
  await userMenu.waitFor({ state: 'visible', timeout: 15_000 });
  await userMenu.click();
  await page.waitForTimeout(1000);

  // 5. 点击"会员管理"菜单
  const memberMenu = page.locator('menuitem').filter({ hasText: '会员管理' });
  await memberMenu.waitFor({ state: 'visible', timeout: 15_000 });
  await memberMenu.click();
  await page.waitForLoadState('networkidle', { timeout: 30_000 });
  await page.waitForTimeout(2000);
}

// ======== 测试套件 ========

test.describe('会员管理-会员查看与操作', () => {
  test.beforeEach(async ({ page }) => {
    await loginAndNavigateToMemberList(page);
  });

  // ========== TC-MV-001: 会员查看详情 ==========
  test('TC-MV-001: 会员查看详情', async ({ page }) => {
    // 点击第一条记录的"查看"链接
    const viewLink = page.getByText('查看').first();
    await viewLink.waitFor({ state: 'visible', timeout: 10_000 });
    await viewLink.click();

    // 等待页面加载完成
    await page.waitForLoadState('networkidle', { timeout: 20_000 });
    await page.waitForTimeout(2000);

    // 断言：验证详情页面内容可见
    // 通常详情页会包含会员的信息，例如会员名称、手机号等
    const detailSection = page.locator('.member-detail, .detail-content, .detail-info').first();
    const detailVisible = await detailSection.isVisible({ timeout: 5000 }).catch(() => false);

    if (detailVisible) {
      await expect(detailSection).toBeVisible();
    } else {
      // 降级断言：验证当前 URL 包含详情相关路径或参数
      const currentUrl = page.url();
      expect(
        currentUrl.includes('detail') ||
        currentUrl.includes('view') ||
        currentUrl.includes('memberDetail') ||
        currentUrl.includes('info')
      ).toBeTruthy();
    }
  });

  // ========== TC-MV-002: 冻结会员 ==========
  test('TC-MV-002: 冻结会员', async ({ page }) => {
    // 点击第一条记录的"更多"链接
    const moreLink = page.getByRole('link', { name: '更多 图标: down' }).first();
    await moreLink.waitFor({ state: 'visible', timeout: 10_000 });
    await moreLink.click();
    await page.waitForTimeout(1000);

    // 点击"冻结"
    const freezeOption = page.getByText('冻结');
    await freezeOption.waitFor({ state: 'visible', timeout: 5_000 });
    await freezeOption.click();
    await page.waitForTimeout(500);

    // 点击确认对话框的"确认"按钮
    const confirmButton = page.getByRole('button', { name: '确认' });
    await confirmButton.waitFor({ state: 'visible', timeout: 5_000 });
    await confirmButton.click();

    // 等待操作完成
    await page.waitForTimeout(2000);

    // 断言：验证成功提示"操作成功"出现
    const successToast = page.getByText('操作成功');
    await expect(successToast).toBeVisible({ timeout: 10_000 });
  });

  // ========== TC-MV-003: 注销会员 ==========
  test('TC-MV-003: 注销会员', async ({ page }) => {
    // 点击第一条记录的"更多"链接
    const moreLink = page.getByRole('link', { name: '更多 图标: down' }).first();
    await moreLink.waitFor({ state: 'visible', timeout: 10_000 });
    await moreLink.click();
    await page.waitForTimeout(1000);

    // 点击"注销"
    const cancelOption = page.getByText('注销');
    await cancelOption.waitFor({ state: 'visible', timeout: 5_000 });
    await cancelOption.click();
    await page.waitForTimeout(500);

    // 点击确认对话框的"确认"按钮
    const confirmButton = page.getByRole('button', { name: '确认' });
    await confirmButton.waitFor({ state: 'visible', timeout: 5_000 });
    await confirmButton.click();

    // 等待操作完成
    await page.waitForTimeout(2000);

    // 断言：验证成功提示"操作成功"出现
    const successToast = page.getByText('操作成功');
    await expect(successToast).toBeVisible({ timeout: 10_000 });
  });

  // ========== TC-MV-004: 取消冻结操作 ==========
  test('TC-MV-004: 取消冻结操作', async ({ page }) => {
    // 点击第一条记录的"更多"链接
    const moreLink = page.getByRole('link', { name: '更多 图标: down' }).first();
    await moreLink.waitFor({ state: 'visible', timeout: 10_000 });
    await moreLink.click();
    await page.waitForTimeout(1000);

    // 点击"冻结"
    const freezeOption = page.getByText('冻结');
    await freezeOption.waitFor({ state: 'visible', timeout: 5_000 });
    await freezeOption.click();
    await page.waitForTimeout(500);

    // 点击确认对话框的"取消"按钮（不确认操作）
    const cancelButton = page.getByRole('button', { name: '取消' });
    await cancelButton.waitFor({ state: 'visible', timeout: 5_000 });
    await cancelButton.click();

    // 等待对话框关闭
    await page.waitForTimeout(2000);

    // 断言1：确认对话框已关闭（确认按钮不再可见）
    const confirmButton = page.getByRole('button', { name: '确认' });
    await expect(confirmButton).not.toBeVisible({ timeout: 5_000 });

    // 断言2：没有成功提示出现
    const successToast = page.getByText('操作成功');
    await expect(successToast).not.toBeVisible({ timeout: 3_000 });
  });
});
