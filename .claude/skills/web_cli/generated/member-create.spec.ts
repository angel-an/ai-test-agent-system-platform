import { test, expect, Page } from '@playwright/test';

const BASE_URL = 'https://console-uat.xysjg.com/front/dtcloud-console-web-pc/#/login';
const USERNAME = 'andan';
const PASSWORD = 'andan123';

/**
 * 登录并导航到会员管理页面
 */
async function loginAndNavigateToMemberList(page: Page) {
  // 1. 打开登录页
  await page.goto(BASE_URL);
  await page.waitForLoadState('networkidle');

  // 2. 登录
  await page.getByRole('textbox', { name: '请输入用户名' }).fill(USERNAME);
  await page.getByRole('textbox', { name: '请输入密码' }).fill(PASSWORD);
  await page.getByRole('button', { name: '登 录' }).click();
  await page.waitForLoadState('networkidle');

  // 3. 点击"数盈·营销云"进入营销云
  await page.getByText('数盈·营销云').first().click();
  await page.waitForLoadState('networkidle');

  // 4. 点击"用户"菜单
  await page.getByText('用户').first().click();
  await page.waitForLoadState('networkidle');

  // 5. 点击"会员管理"
  await page.locator('menuitem').filter({ hasText: '会员管理' }).click();
  await page.waitForLoadState('networkidle');
}

test.describe('会员管理-新建会员', () => {

  test.beforeEach(async ({ page }) => {
    await loginAndNavigateToMemberList(page);
  });

  test('点击新建按钮跳转', async ({ page }) => {
    // 点击"新建"按钮
    await page.getByRole('button', { name: '新建' }).click();
    await page.waitForLoadState('networkidle');

    // 断言：新建按钮不可见（说明已离开列表页），或者出现新建表单
    // 方式一：检查新建按钮不再可见
    await expect(page.getByRole('button', { name: '新建' })).not.toBeVisible();

    // 方式二：检查页面 URL 或内容发生变化（如新增表单字段出现）
    // 出现"手机号"或"姓名"等输入框即表示进入新建页面
    const phoneInput = page.getByRole('textbox', { name: /手机号/i });
    const nameInput = page.getByRole('textbox', { name: /姓名/i });

    // 断言至少出现其中一个输入框（根据实际 UI 弹窗/页面调整）
    await expect(phoneInput.or(nameInput).first()).toBeVisible({ timeout: 5000 });
  });

  test('成功创建会员（简化版）', async ({ page }) => {
    // 点击"新建"按钮
    await page.getByRole('button', { name: '新建' }).click();
    await page.waitForLoadState('networkidle');

    // 生成唯一的手机号：前缀 199 + 时间戳末尾8位
    const phoneNumber = `199${Date.now().toString().slice(-8)}`;

    // 填写手机号
    const phoneInput = page.getByRole('textbox', { name: /手机号/i });
    await expect(phoneInput).toBeVisible({ timeout: 5000 });
    await phoneInput.fill(phoneNumber);

    // 填写姓名
    const nameInput = page.getByRole('textbox', { name: /姓名/i });
    if (await nameInput.isVisible()) {
      await nameInput.fill('测试用户张三');
    }

    // 点击提交/保存按钮（根据实际按钮文案调整）
    const saveButton = page.getByRole('button', { name: /保存|提交|确定|确 定/i });
    if (await saveButton.isVisible()) {
      await saveButton.click();
    } else {
      // 若找不到标准保存按钮，尝试点击表单中第一个可点击的提交类按钮
      await page.getByRole('button').filter({ hasText: /保存|提交/ }).first().click();
    }

    await page.waitForLoadState('networkidle');

    // 断言：出现成功提示
    await expect(page.getByText(/成功|新建成功|创建成功|保存成功/i).first()).toBeVisible({ timeout: 10000 });
  });

});
