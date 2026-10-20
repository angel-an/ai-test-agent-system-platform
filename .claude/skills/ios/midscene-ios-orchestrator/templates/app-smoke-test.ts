import { beforeAll, afterAll, describe, it } from 'vitest';
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

const WDA_BASE_URL = process.env.WDA_BASE_URL || 'http://localhost:8100';
const APP_BUNDLE_ID = process.env.APP_BUNDLE_ID || 'com.apple.mobilesafari';
const SLEEP_AFTER_CONNECT = 2000;

let page: IOSDevice;
let agent: IOSAgent;

/**
 * 替换 APP_BUNDLE_ID 为你自己的 iOS App Bundle ID。
 * 替换 aiActContext 中的 App 名称和行为描述。
 * 替换每个 it 中的 aiAct/aiWaitFor/aiAssert 描述为你自己的场景。
 */
beforeAll(async () => {
  page = new IOSDevice({ baseUrl: WDA_BASE_URL });
  agent = new IOSAgent(page, {
    reportFileName: 'app-smoke-test',
    aiActContext: `iOS App 自动化测试。
    被测应用 Bundle ID: ${APP_BUNDLE_ID}。
    如有权限弹窗点击"允许"或"好"。
    语言为中文。`,
  });

  await page.connect();
  await new Promise(r => setTimeout(r, SLEEP_AFTER_CONNECT));
  await page.launch(APP_BUNDLE_ID);
});

afterAll(async () => {
  await page.disconnect();
});

describe('App 核心功能冒烟测试', () => {

  it('App 启动验证', async () => {
    await agent.aiWaitFor('App 首页已完全加载，核心界面元素可见', {
      timeoutMs: 20000,
    });
    await agent.aiAssert('App 首页完整显示');
  });

  it('核心交互操作', async () => {
    // === 替换为你的 App 的核心操作 ===
    // 示例: 点击按钮、输入文字、跳转页面等
    await agent.aiAct('执行一个核心用户操作');
    await agent.aiWaitFor('操作后的页面状态已更新', {
      timeoutMs: 15000,
    });
    await agent.aiAssert('页面正确显示了操作结果');
  });

  it('二级页面验证', async () => {
    // === 替换为你的 App 的二级页面操作 ===
    await agent.aiAct('导航到二级页面');
    await agent.aiWaitFor('二级页面已加载完成', {
      timeoutMs: 15000,
    });
    await agent.aiAssert('二级页面内容显示正确');
  });

  it('返回与状态保持', async () => {
    await agent.aiAct('执行返回操作（点击返回按钮或手势）');
    await agent.aiWaitFor('已返回上一页', {
      timeoutMs: 10000,
    });
    await agent.aiAssert('上一页的状态和数据保持正常');
  });

});
