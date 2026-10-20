---
name: midscene-ios-script-generation
description: 从测试需求/测试用例自动生成 Midscene iOS 测试脚本。涵盖自然语言需求转脚本、模板选择、Vitest 集成、执行调试的完整流程。
---

# Midscene iOS 脚本生成与执行

## 触发条件

当用户需要"生成 iOS 测试脚本"、"编写 iOS 自动化代码"、"从需求生成 iOS 脚本"、"执行 iOS 测试"时加载此 skill。

## 从测试需求生成脚本 — 四步法

### 第一步：理解测试场景

从用户描述中提取关键要素：

| 要素 | 说明 | 示例 |
|------|------|------|
| App 标识 | Bundle ID | `com.example.app` |
| 前置条件 | 是否需要登录、特定数据状态 | 已登录状态、商品已在购物车 |
| 操作步骤 | 点击、输入、滑动、手势 | 点击Tab→搜索→加入购物车 |
| 验证点 | 数据校验、UI 状态、页面跳转 | 金额正确、页面标题变化 |
| iOS 特有交互 | 键盘收起、系统弹窗、URL Scheme | Face ID 弹窗、推送通知 |

### 第二步：选择脚本模板

#### A. 功能冒烟测试模板

```typescript
// src/tests/smoke.test.ts
import { beforeAll, afterAll, it } from 'vitest';
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

let page: IOSDevice;
let agent: IOSAgent;

beforeAll(async () => {
  page = new IOSDevice({ baseUrl: 'http://localhost:8100' });
  agent = new IOSAgent(page, {
    reportFileName: 'smoke-test',
    aiActContext: 'iOS 冒烟测试。如有系统权限弹窗（通知/定位），点击"允许"。语言为中文。',
  });
  await page.connect();
  await page.launch('com.example.app');
  await agent.aiWaitFor('App 首页已加载', { timeoutMs: 20000 });
});

afterAll(async () => {
  await page.disconnect();
});

it('首页加载验证', async () => {
  await agent.aiAssert('首页已完全加载，所有核心元素（导航栏、Tab Bar、内容区域）可见');
});

it('Tab Bar 切换验证', async () => {
  await agent.aiAct('点击底部 Tab Bar 的第二个标签');
  await agent.aiWaitFor('第二个标签页已加载', { timeoutMs: 10000 });
  await agent.aiAssert('第二个标签页内容正常显示');
});
```

#### B. 业务场景测试模板

```typescript
// src/tests/business-scenario.test.ts
import { beforeAll, afterAll, describe, it } from 'vitest';
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

let page: IOSDevice;
let agent: IOSAgent;

beforeAll(async () => {
  page = new IOSDevice({ baseUrl: 'http://localhost:8100' });
  agent = new IOSAgent(page, {
    reportFileName: 'business-scenario',
    aiActContext: 'iOS 电商 App 测试环境。如有权限弹窗点击"允许"。语言为中文。',
  });
  await page.connect();
  await page.launch('com.example.app');
  await agent.aiWaitFor('应用程序首页已加载完成', { timeoutMs: 20000 });
});

afterAll(async () => {
  await page.disconnect();
});

describe('完整购物流程', () => {
  it('步骤1: 搜索商品', async () => {
    await agent.aiAct('点击首页顶部搜索框');
    await agent.aiAct('在搜索框中输入 "iPhone 15"');
    await agent.aiAct('收起键盘，点击搜索按钮');
    await agent.aiWaitFor('搜索结果列表已显示', { timeoutMs: 15000 });
    await agent.aiAssert('搜索结果中包含 "iPhone 15" 相关商品');
  });

  it('步骤2: 筛选与排序', async () => {
    await agent.aiAct('点击"排序"按钮，选择"价格从低到高"');
    await agent.aiWaitFor('排序后的结果已刷新', { timeoutMs: 10000 });
    await agent.aiAssert('搜索结果已按价格从低到高排序');
  });

  it('步骤3: 查看商品详情', async () => {
    await agent.aiAct('点击第一个商品进入详情页');
    await agent.aiWaitFor('商品详情页面已加载', { timeoutMs: 15000 });
    await agent.aiAssert('商品名称、价格、描述均正常显示');
    const productName = await agent.aiString('这个商品的名称是什么');
    console.log('商品名称:', productName);
  });

  it('步骤4: 加入购物车', async () => {
    await agent.aiAct('点击"加入购物车"按钮');
    await agent.aiWaitFor('加入购物车成功提示已出现', { timeoutMs: 10000 });
  });

  it('步骤5: 结算验证', async () => {
    await agent.aiAct('点击底部 Tab Bar 的购物车图标');
    await agent.aiWaitFor('购物车页面已加载', { timeoutMs: 10000 });
    await agent.aiAct('点击"结算"按钮');
    await agent.aiWaitFor('订单确认页面已加载', { timeoutMs: 10000 });
    await agent.aiAssert('订单金额、商品信息显示正确');
  });
});
```

#### C. iOS 专属手势测试模板

```typescript
// src/tests/gesture.test.ts
import { beforeAll, afterAll, it } from 'vitest';
import { IOSDevice, IOSAgent } from '@midscene/ios';

let page: IOSDevice;
let agent: IOSAgent;

beforeAll(async () => {
  page = new IOSDevice({ baseUrl: 'http://localhost:8100' });
  agent = new IOSAgent(page, { reportFileName: 'gesture-test' });
  await page.connect();
  await page.launch('com.example.app');
});

afterAll(async () => {
  await page.disconnect();
});

it('下拉刷新测试', async () => {
  await agent.aiAct('在页面顶部向下拉，执行下拉刷新操作');
  await agent.aiWaitFor('刷新动画已出现并完成', { timeoutMs: 10000 });
});

it('拖动排序测试', async () => {
  await agent.aiAct('长按第一个列表项进入编辑模式');
  await agent.aiAct('将第一个列表项拖动到第三个位置');
  await agent.aiWaitFor('列表顺序已更新', { timeoutMs: 5000 });
});

it('双指缩放测试', async () => {
  await agent.aiAct('在图片区域执行双指放大操作');
  await agent.aiWaitFor('图片已放大', { timeoutMs: 5000 });
  await agent.aiAssert('图片被放大后显示更清晰的细节');
});
```

#### D. 独立运行脚本模板（非 Vitest）

```typescript
// src/scripts/ios-auto.ts
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

const WDA_URL = process.env.WDA_BASE_URL || 'http://localhost:8100';
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

async function main() {
  console.log('连接 WDA...');
  const page = new IOSDevice({ baseUrl: WDA_URL });
  const agent = new IOSAgent(page, {
    reportFileName: `auto-run-${Date.now()}`,
    aiActContext: 'iOS 自动化运行。如有权限弹窗点击"允许"。',
  });

  await page.connect();
  await sleep(2000);

  try {
    // 启动 App
    await page.launch('com.example.app');
    await agent.aiWaitFor('App 已启动', { timeoutMs: 20000 });

    // 执行测试逻辑
    await agent.aiAct('点击首页的"立即购买"按钮');
    await agent.aiWaitFor('购买页面已加载', { timeoutMs: 10000 });

    // 获取页面信息
    const pageInfo = await agent.aiQuery('{title: string, price: string, description: string}, 获取商品标题、价格和描述');
    console.log('页面信息:', pageInfo);

    console.log('测试完成');
  } catch (err) {
    console.error('测试失败:', err);
  } finally {
    await page.disconnect();
  }
}

main().catch(console.error);
```

### 第三步：填充测试用例数据

将用户的测试描述转化为具体的 aiAct / aiQuery / aiAssert 调用。

**需求举例**：用户说"测试 iOS App 的注册功能，输入新手机号应能收到验证码"

**生成的脚本**：
```typescript
it('新手机号注册应能收到验证码', async () => {
  await agent.aiAct('点击"注册"按钮进入注册页面');
  await agent.aiWaitFor('注册页面已加载', { timeoutMs: 10000 });
  await agent.aiAct('在手机号输入框中输入 "13800138000"');
  await agent.aiAct('点击"获取验证码"按钮');
  await agent.aiWaitFor('验证码发送成功提示已出现', { timeoutMs: 10000 });
  await agent.aiAssert('页面上显示"验证码已发送"的提示文字');
});
```

### 从 Bundle ID 启动 App

`page.launch(BUNDLE_ID)` 支持两种类型的 App：

| 类型 | Bundle ID 来源 | 示例 | 说明 |
|------|---------------|------|------|
| 系统内置 App | 已知固定 | `com.apple.mobilesafari` | 无需开发者账号，随时可测 |
| 第三方 App | `xcrun simctl listapps`(模拟器) / `ideviceinstaller -l`(真机) | `com.xiaohongshu.app` | 需已安装到设备上 |

常用系统 App Bundle ID:
- Safari: `com.apple.mobilesafari`
- 设置: `com.apple.Preferences`
- 相机: `com.apple.camera`
- 照片: `com.apple.mobileslideshow`
- 地图: `com.apple.Maps`
- 备忘录: `com.apple.mobilenotes`

### 第四步：执行脚本

```bash
# 方式一：独立脚本执行（不带测试框架）
npx tsx src/scripts/ios-auto.ts

# 方式二：Vitest 执行（推荐，带报告生成）
npx vitest run src/tests/login.test.ts

# 方式三：批量执行所有测试
npx vitest run

# 方式四：Watch 模式（开发调试时）
npx vitest -- src/tests/login.test.ts
```

## 测试脚本生成规范

### 生成原则

| 维度 | 原则 | 说明 |
|------|------|------|
| 原子性 | 每个 aiAct 只做一件事 | "输入用户名密码并点击登录" 拆分为三步 |
| 可观测 | 每个操作后必有等待或断言 | aiAct 后必须有 aiWaitFor 或 aiAssert |
| 独立性 | 每个测试用例可独立运行 | 不依赖其他用例的执行结果 |
| 数据驱动 | 测试数据与脚本分离 | 使用 describe.each 或外部数据文件 |
| 鲁棒性 | 设置合理的超时 | 所有等待都指定 timeoutMs |

### 文件命名规范

```
src/tests/
├── login.test.ts              # 登录模块
├── register.test.ts           # 注册模块
├── search.test.ts             # 搜索功能
├── checkout.test.ts           # 结算流程
├── personal-center.test.ts    # 个人中心
├── gesture.test.ts            # 手势操作
└── smoke.test.ts              # 冒烟测试汇总
```

## 坑点与注意事项

- iOS 键盘弹出可能导致点击目标偏移，建议在 Input 后手动处理键盘收起
- WDA 连接超时：先确认 `curl http://localhost:8100/status` 能正常返回
- Vitest 默认 5s 超时，务必在 vitest.config.ts 中设置 testTimeout 到 120000
- iOS 模拟器首次启动 App 较慢，适当增加 aiWaitFor 的超时时间
- 真机测试需要 USB 连接或通过 iproxy 转发，网络延迟比模拟器大
- WDA 不支持多线程同时操作同一设备，测试建议串行执行
- 启动 App 的 Bundle ID 可以通过 `ideviceinstaller -l`（真机）或 `xcrun simctl listapps`（模拟器）查看
- 多个测试文件共享 page/agent 实例需要在 vitest 的 globalSetup 或 setupFiles 中配置
