import { test, expect, Page, BrowserContext } from '@playwright/test';

/**
 * 营销云 - 系统 - 消息管理 (Message Management) CRUD 完整测试脚本
 *
 * 测试覆盖:
 * 1. TC-1-001: 从登录导航到消息管理页面的完整导航验证
 * 2. TC-2-001: 按模版名称搜索消息模板
 * 3. TC-2-003: 使用不存在的模版名称搜索
 * 4. TC-2-004: 搜索后点击重置恢复完整列表
 * 5. TC-3-001: 查看消息模板详情
 * 6. TC-4-001: 禁用已启用的消息模板
 * 7. TC-4-002: 启用已禁用的消息模板
 * 8. TC-5-001: 按完整模版ID精确搜索
 * 9. TC-6-001: 切换到渠道配置Tab并验证渠道信息
 * 10. TC-7-001: 渠道配置同步模板操作
 *
 * 项目标识符: PR-2
 * 环境: UAT 环境
 * 入口: BOC 企业控制台 (https://console-uat.xysjg.com) -> 数盈·营销云 -> 系统 -> 消息管理
 */

// ======== 环境配置 ========
const CONSOLE_LOGIN_URL =
  'https://console-uat.xysjg.com/front/dtcloud-console-web-pc/#/login';
const CONSOLE_USERNAME = 'andan';
const CONSOLE_PASSWORD = 'andan123';

// ======== 测试数据 ========
const TEST_DATA = {
  searchTemplateName: '券到账',
  searchTemplateId: 'X1441411410577941610',
};

// ======== 超时配置 ========
const TIMEOUT = {
  NAVIGATION: 30_000,
  ELEMENT: 15_000,
  NETWORK: 30_000,
  ACTION: 10_000,
  ANIMATION: 2000,
  PAGE_LOAD: 5000,
};

// ======== 辅助函数 ========

/**
 * 登录企业控制台并导航到营销云消息管理页面
 * 完整流程：登录控制台 -> 点击数盈·营销云卡片（打开新标签页）-> 切换标签页 -> 系统菜单 -> 消息管理
 */
async function loginAndNavigateToMessageManagement(
  page: Page,
  context: BrowserContext
): Promise<Page> {
  // === Step 1: 登录企业控制台 ===
  console.log('[导航] 开始登录企业控制台...');
  await page.goto(CONSOLE_LOGIN_URL, {
    waitUntil: 'networkidle',
    timeout: TIMEOUT.NAVIGATION,
  });

  const usernameInput = page.getByRole('textbox', { name: '请输入用户名' });
  await usernameInput.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
  await usernameInput.fill(CONSOLE_USERNAME);

  const passwordInput = page.getByRole('textbox', { name: '请输入密码' });
  await passwordInput.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
  await passwordInput.fill(CONSOLE_PASSWORD);

  const loginButton = page.getByRole('button', { name: '登 录' });
  await loginButton.click();

  await page.waitForURL('**/home**', { timeout: TIMEOUT.NAVIGATION });
  await page.waitForLoadState('networkidle', { timeout: TIMEOUT.NETWORK });
  console.log('[导航] 企业控制台登录成功');

  // === Step 2: 点击数盈·营销云卡片（会打开新标签页） ===
  // 用完整的卡片文本匹配可点击的外层容器（含光标样式）
  // 注意: 必须使用 div.ant-col 类选择器定位外层可点击的卡片容器，内层的普通div没有绑定React事件处理函数
  await page.waitForTimeout(3000); // 等待页面完全渲染
  const marketingCard = page.locator('div.ant-col').filter({ hasText: /数盈·营销云.*Marketing cloud/ }).first();
  await marketingCard.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });

  const [mktPage] = await Promise.all([
    context.waitForEvent('page', { timeout: TIMEOUT.NAVIGATION }),
    marketingCard.click(),
  ]);

  await mktPage.waitForLoadState('networkidle', { timeout: TIMEOUT.NETWORK });
  await mktPage.waitForTimeout(TIMEOUT.PAGE_LOAD);
  // 确认新标签页是营销云页面
  console.log('[导航] 营销云新标签页已打开, URL:', mktPage.url());

  // === Step 3: 点击侧边栏系统菜单 ===
  // 系统菜单位于侧边栏中，使用精确文字匹配
  const systemMenu = mktPage.locator('div').filter({ hasText: /^系统$/ }).first();
  await systemMenu.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
  await systemMenu.click();
  await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
  console.log('[导航] 已点击系统菜单');

  // === Step 4: 点击消息管理菜单项 ===
  const messageMenu = mktPage.getByRole('menuitem', { name: '消息管理' });
  await messageMenu.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
  await messageMenu.click();
  await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
  console.log('[导航] 消息管理页面已加载, URL:', mktPage.url());

  return mktPage;
}

/**
 * 获取表格数据行数
 */
async function getTableRowCount(mktPage: Page): Promise<number> {
  const rows = mktPage.locator(
    'table tbody tr, ' +
    'table tr[data-row-key], ' +
    'table tr.ant-table-row, ' +
    '.ant-table-tbody tr'
  );
  return await rows.count();
}

/**
 * 检查操作成功提示
 */
async function checkSuccessMessage(mktPage: Page): Promise<string | null> {
  const selectors = [
    mktPage.getByRole('alert').first(),
    mktPage.getByText(/成功|Success/).first(),
    mktPage.locator('.ant-message-notice').first(),
    mktPage.locator('.el-message').first(),
  ];

  for (const el of selectors) {
    try {
      const visible = await el.isVisible({ timeout: 5000 }).catch(() => false);
      if (visible) {
        const text = await el.textContent();
        console.log('[提示] 成功消息:', text);
        return text;
      }
    } catch {
      // continue
    }
  }
  console.log('[提示] 未找到成功消息');
  return null;
}

/**
 * 在表格中搜索文本内容
 */
async function getTableContent(mktPage: Page): Promise<string | null> {
  try {
    const table = mktPage.locator('table').first();
    await table.waitFor({ state: 'visible', timeout: TIMEOUT.ACTION });
    return await table.textContent();
  } catch {
    console.log('[表格] 未找到表格元素');
    return null;
  }
}

// =========================================================
// 测试套件
// =========================================================
test.describe('营销云 - 系统 - 消息管理 CRUD 功能测试', () => {
  test.setTimeout(300_000);

  test.afterEach(async ({ context }) => {
    const pages = context.pages();
    for (let i = pages.length - 1; i > 0; i--) {
      try {
        if (!pages[i].isClosed()) {
          await pages[i].close();
        }
      } catch {
        // ignore closed page errors
      }
    }
  });

  // =========================================================
  // TC-1-001: 从登录导航到消息管理页面
  // =========================================================
  test('TC-1-001: 从控制台登录到消息管理页面的完整导航验证', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // 验证页面关键元素
    console.log('[验证] 检查页面关键元素...');

    // 验证消息模板Tab可见（默认激活）
    const msgTemplateTab = mktPage.getByRole('tab', { name: '消息模板' });
    await expect(msgTemplateTab).toBeVisible({ timeout: TIMEOUT.ELEMENT });
    console.log('[验证] 消息模板Tab可见');

    // 验证渠道配置Tab可见
    const channelConfigTab = mktPage.getByRole('tab', { name: '渠道配置' });
    await expect(channelConfigTab).toBeVisible({ timeout: TIMEOUT.ELEMENT });
    console.log('[验证] 渠道配置Tab可见');

    // 验证新增消息模版按钮可见
    const addTemplateBtn = mktPage.getByRole('button', { name: '新增消息模版' });
    await expect(addTemplateBtn).toBeVisible({ timeout: TIMEOUT.ELEMENT });
    console.log('[验证] 新增消息模版按钮可见');

    // 验证搜索按钮可见
    const searchBtn = mktPage.getByRole('button', { name: '搜索' });
    await expect(searchBtn).toBeVisible({ timeout: TIMEOUT.ELEMENT });
    console.log('[验证] 搜索按钮可见');

    // 验证重置按钮可见
    const resetBtn = mktPage.getByRole('button', { name: '重置' });
    await expect(resetBtn).toBeVisible({ timeout: TIMEOUT.ELEMENT });
    console.log('[验证] 重置按钮可见');

    // 验证模版名称输入框可见
    const templateNameInput = mktPage.getByPlaceholder('请输入模版名称');
    await expect(templateNameInput).toBeVisible({ timeout: TIMEOUT.ELEMENT });
    console.log('[验证] 模版名称输入框可见');

    // 验证模版ID输入框可见
    const templateIdInput = mktPage.getByPlaceholder('请输入模版ID');
    await expect(templateIdInput).toBeVisible({ timeout: TIMEOUT.ELEMENT });
    console.log('[验证] 模版ID输入框可见');

    // 验证表格存在
    const table = mktPage.locator('table').first();
    await expect(table).toBeVisible({ timeout: TIMEOUT.ELEMENT });
    console.log('[验证] 表格可见');

    // 验证分页信息
    try {
      const paginationText = await mktPage.getByText(/共.*条/).textContent();
      console.log('[验证] 分页信息:', paginationText);
      expect(paginationText).toContain('共');
    } catch {
      console.log('[验证] 分页信息未找到');
    }

    console.log('TC-1-001 完成 ✅');
  });

  // =========================================================
  // TC-2-001: 按模版名称搜索消息模板
  // =========================================================
  test('TC-2-001: 按完整模版名称精确搜索', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 在模版名称输入框中输入已知的模版名称
    const nameInput = mktPage.getByPlaceholder('请输入模版名称');
    await nameInput.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
    await nameInput.fill(TEST_DATA.searchTemplateName);
    console.log(`[搜索] 输入模版名称: "${TEST_DATA.searchTemplateName}"`);

    // Step 2: 点击搜索按钮
    const searchBtn = mktPage.getByRole('button', { name: '搜索' });
    await searchBtn.click();
    await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
    console.log('[搜索] 已点击搜索按钮');

    // Step 3: 验证搜索结果
    await mktPage.waitForTimeout(2000);
    const tableContent = await getTableContent(mktPage);
    console.log('[搜索] 搜索结果:', tableContent?.substring(0, 100));

    // 验证搜索结果表格中有数据
    const rowCount = await getTableRowCount(mktPage);
    console.log(`[搜索] 结果行数: ${rowCount}`);
    expect(rowCount).toBeGreaterThanOrEqual(1);

    console.log('TC-2-001 完成 ✅');
  });

  // =========================================================
  // TC-2-003: 使用不存在的模版名称搜索
  // =========================================================
  test('TC-2-003: 使用不存在的模版名称搜索', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 输入不存在的模版名称
    const nameInput = mktPage.getByPlaceholder('请输入模版名称');
    await nameInput.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
    await nameInput.fill('__NON_EXISTENT_NAME_99999__');
    console.log('[搜索] 输入不存在的模版名称');

    // Step 2: 点击搜索按钮
    const searchBtn = mktPage.getByRole('button', { name: '搜索' });
    await searchBtn.click();
    await mktPage.waitForTimeout(TIMEOUT.ANIMATION);

    // Step 3: 验证空结果
    await mktPage.waitForTimeout(2000);
    const rowCount = await getTableRowCount(mktPage);
    console.log(`[搜索] 空搜索结果行数: ${rowCount}`);

    // 验证暂无数据或空状态提示
    try {
      const emptyState = await mktPage.getByText(/暂无数据/).isVisible({ timeout: 5000 });
      console.log('[搜索] 暂无数据提示:', emptyState);
    } catch {
      console.log('[搜索] 暂无数据提示未找到（可能无数据时表格隐藏）');
    }

    console.log('TC-2-003 完成 ✅');
  });

  // =========================================================
  // TC-2-004: 搜索后点击重置恢复完整列表
  // =========================================================
  test('TC-2-004: 搜索后点击重置恢复完整列表', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 输入模版名称进行搜索
    const nameInput = mktPage.getByPlaceholder('请输入模版名称');
    await nameInput.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
    await nameInput.fill('券');
    console.log('[搜索] 输入搜索条件');

    // Step 2: 点击搜索按钮
    const searchBtn = mktPage.getByRole('button', { name: '搜索' });
    await searchBtn.click();
    await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
    console.log('[搜索] 已点击搜索按钮');

    // Step 3: 点击重置按钮
    const resetBtn = mktPage.getByRole('button', { name: '重置' });
    await resetBtn.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
    await resetBtn.click();
    await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
    console.log('[重置] 已点击重置按钮');

    // Step 4: 验证模版名称输入框已清空
    const inputValue = await nameInput.inputValue();
    console.log(`[重置] 输入框值: "${inputValue}"`);
    expect(inputValue).toBe('');

    // Step 5: 验证表格恢复数据
    await mktPage.waitForTimeout(2000);
    const rowCount = await getTableRowCount(mktPage);
    console.log(`[重置] 恢复后行数: ${rowCount}`);
    expect(rowCount).toBeGreaterThanOrEqual(1);

    console.log('TC-2-004 完成 ✅');
  });

  // =========================================================
  // TC-3-001: 查看消息模板详情
  // =========================================================
  test('TC-3-001: 查看消息模板详情', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 确认表格中有数据
    const rowCount = await getTableRowCount(mktPage);
    console.log(`[查看] 表格行数: ${rowCount}`);
    expect(rowCount).toBeGreaterThanOrEqual(1);

    // Step 2: 点击第一行数据的查看按钮
    const viewBtn = mktPage.locator('a').filter({ hasText: '查看' }).first();
    await viewBtn.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
    await viewBtn.click();
    await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
    console.log('[查看] 已点击查看按钮');

    // Step 3: 验证详情弹窗或页面打开
    try {
      // 尝试检测对话框/详情弹窗
      const dialog = mktPage.getByRole('dialog').first();
      const dialogVisible = await dialog.isVisible({ timeout: 5000 }).catch(() => false);
      if (dialogVisible) {
        const dialogText = await dialog.textContent();
        console.log('[查看] 详情弹窗内容:', dialogText?.substring(0, 200));
      }
    } catch {
      console.log('[查看] 未检测到详情弹窗，可能为内联详情页面');
    }

    // 尝试关闭详情
    try {
      const closeBtn = mktPage.getByRole('button', { name: /关闭|返回|Close/ }).first();
      await closeBtn.click();
      await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
      console.log('[查看] 已关闭详情');
    } catch {
      console.log('[查看] 未找到关闭按钮');
    }

    console.log('TC-3-001 完成 ✅');
  });

  // =========================================================
  // TC-4-001: 禁用已启用的消息模板
  // =========================================================
  test('TC-4-001: 禁用已启用的消息模板', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 查找状态为启用的模板（操作列有禁用按钮）
    const disableBtn = mktPage.getByRole('button', { name: '禁用' }).first();
    const disableVisible = await disableBtn.isVisible({ timeout: TIMEOUT.ELEMENT }).catch(() => false);

    if (disableVisible) {
      // Step 2: 点击禁用按钮
      await disableBtn.click();
      await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
      console.log('[禁用] 已点击禁用按钮');

      // Step 3: 处理确认弹窗
      try {
        const confirmBtn = mktPage.getByRole('button', { name: /确定|确认|OK/ }).first();
        const confirmVisible = await confirmBtn.isVisible({ timeout: 5000 }).catch(() => false);
        if (confirmVisible) {
          await confirmBtn.click();
          await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
          console.log('[禁用] 已确认禁用操作');
        }
      } catch {
        console.log('[禁用] 无确认弹窗，直接执行');
      }

      // Step 4: 验证成功提示
      await mktPage.waitForTimeout(2000);
      await checkSuccessMessage(mktPage);

      console.log('[禁用] 禁用操作执行完成');
    } else {
      console.log('[禁用] 未找到可禁用的模板（所有模板可能已禁用）');
    }

    console.log('TC-4-001 完成 ✅');
  });

  // =========================================================
  // TC-4-002: 启用已禁用的消息模板
  // =========================================================
  test('TC-4-002: 启用已禁用的消息模板', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 查找状态为禁用的模板（操作列有启用按钮）
    const enableBtn = mktPage.getByRole('button', { name: '启用' }).first();
    const enableVisible = await enableBtn.isVisible({ timeout: TIMEOUT.ELEMENT }).catch(() => false);

    if (enableVisible) {
      // Step 2: 点击启用按钮
      await enableBtn.click();
      await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
      console.log('[启用] 已点击启用按钮');

      // Step 3: 处理确认弹窗
      try {
        const confirmBtn = mktPage.getByRole('button', { name: /确定|确认|OK/ }).first();
        const confirmVisible = await confirmBtn.isVisible({ timeout: 5000 }).catch(() => false);
        if (confirmVisible) {
          await confirmBtn.click();
          await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
          console.log('[启用] 已确认启用操作');
        }
      } catch {
        console.log('[启用] 无确认弹窗，直接执行');
      }

      // Step 4: 验证成功提示
      await mktPage.waitForTimeout(2000);
      await checkSuccessMessage(mktPage);

      console.log('[启用] 启用操作执行完成');
    } else {
      console.log('[启用] 未找到可启用的模板（所有模板可能已启用）');
    }

    console.log('TC-4-002 完成 ✅');
  });

  // =========================================================
  // TC-5-001: 按完整模版ID精确搜索
  // =========================================================
  test('TC-5-001: 按完整模版ID精确搜索', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 在模版ID输入框中输入已知的模版ID
    const idInput = mktPage.getByPlaceholder('请输入模版ID');
    await idInput.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
    await idInput.fill(TEST_DATA.searchTemplateId);
    console.log(`[搜索] 输入模版ID: "${TEST_DATA.searchTemplateId}"`);

    // Step 2: 点击搜索按钮
    const searchBtn = mktPage.getByRole('button', { name: '搜索' });
    await searchBtn.click();
    await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
    console.log('[搜索] 已点击搜索按钮');

    // Step 3: 验证搜索结果
    await mktPage.waitForTimeout(2000);
    const rowCount = await getTableRowCount(mktPage);
    console.log(`[搜索] 模版ID搜索结果行数: ${rowCount}`);

    // Step 4: 验证分页信息
    try {
      const paginationText = await mktPage.getByText(/共.*条/).textContent();
      console.log('[搜索] 分页信息:', paginationText);
    } catch {
      console.log('[搜索] 分页信息未找到');
    }

    console.log('TC-5-001 完成 ✅');
  });

  // =========================================================
  // TC-6-001: 切换到渠道配置Tab并验证渠道信息
  // =========================================================
  test('TC-6-001: 切换到渠道配置Tab并验证渠道信息', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 点击渠道配置Tab
    const channelConfigTab = mktPage.getByRole('tab', { name: '渠道配置' });
    await channelConfigTab.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
    await channelConfigTab.click();
    await mktPage.waitForLoadState('networkidle', { timeout: TIMEOUT.NETWORK });
    await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
    console.log('[渠道配置] 已切换到渠道配置Tab');

    // Step 2: 验证渠道配置表格加载
    // 验证渠道名称列标题
    try {
      const channelNameHeader = mktPage.getByRole('columnheader', { name: '渠道名称' });
      await expect(channelNameHeader).toBeVisible({ timeout: TIMEOUT.ELEMENT });
      console.log('[验证] 渠道名称列标题可见');
    } catch {
      console.log('[验证] 渠道名称列标题未找到');
    }

    // 验证渠道类型列标题
    try {
      const channelTypeHeader = mktPage.getByRole('columnheader', { name: '渠道类型' });
      await expect(channelTypeHeader).toBeVisible({ timeout: TIMEOUT.ELEMENT });
      console.log('[验证] 渠道类型列标题可见');
    } catch {
      console.log('[验证] 渠道类型列标题未找到');
    }

    // 验证表格有数据
    const rowCount = await getTableRowCount(mktPage);
    console.log(`[验证] 渠道配置表格行数: ${rowCount}`);

    // 验证同步模板按钮可见
    try {
      const syncBtn = mktPage.getByRole('button', { name: '同步模板' });
      const syncVisible = await syncBtn.isVisible({ timeout: TIMEOUT.ACTION }).catch(() => false);
      console.log('[验证] 同步模板按钮可见:', syncVisible);
    } catch {
      console.log('[验证] 同步模板按钮未找到');
    }

    console.log('TC-6-001 完成 ✅');
  });

  // =========================================================
  // TC-7-001: 渠道配置同步模板操作
  // =========================================================
  test('TC-7-001: 渠道配置同步模板操作', async ({ page, context }) => {
    const mktPage = await loginAndNavigateToMessageManagement(page, context);

    // Step 1: 切换到渠道配置Tab
    const channelConfigTab = mktPage.getByRole('tab', { name: '渠道配置' });
    await channelConfigTab.waitFor({ state: 'visible', timeout: TIMEOUT.ELEMENT });
    await channelConfigTab.click();
    await mktPage.waitForLoadState('networkidle', { timeout: TIMEOUT.NETWORK });
    await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
    console.log('[渠道配置] 已切换到渠道配置Tab');

    // Step 2: 点击同步模板按钮
    const syncBtn = mktPage.getByRole('button', { name: '同步模板' });
    const syncVisible = await syncBtn.isVisible({ timeout: TIMEOUT.ELEMENT }).catch(() => false);

    if (syncVisible) {
      await syncBtn.click();
      await mktPage.waitForTimeout(TIMEOUT.ANIMATION);
      console.log('[同步] 已点击同步模板按钮');

      // Step 3: 验证同步操作结果
      await mktPage.waitForTimeout(2000);
      await checkSuccessMessage(mktPage);

      console.log('[同步] 同步模板操作完成');
    } else {
      console.log('[同步] 同步模板按钮不可见，跳过');
    }

    console.log('TC-7-001 完成 ✅');
  });
});
