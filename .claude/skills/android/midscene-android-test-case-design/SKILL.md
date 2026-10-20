---
name: midscene-android-test-case-design
description: Midscene Android 测试用例设计规范和脚本模式模板。涵盖 API 调用模式、页面对象模型、数据验证、断言策略、YAML 工作流等完整设计方法。
---

# Midscene Android 测试用例设计

## 触发条件

当用户需要"设计测试用例"、"编写测试脚本"、"了解 Midscene API 用法"、"制定测试模式"时加载此 skill。

## Midscene 核心 API 速查

### 1. aiAct — 执行操作（核心）

```typescript
// 执行自然语言描述的操作序列
await agent.aiAct('在搜索框中输入 "Headphones"，点击搜索按钮');
await agent.aiAct('向下滚动页面');
await agent.aiAct('点击第一个商品进入详情页');
await agent.aiAct('点击 "添加到购物车" 按钮');
```

aiAct 会自动规划 AI 动作序列，支持的 Android 原生手势：Tap、DoubleClick、Input、Scroll、DragAndDrop、KeyboardPress、LongPress、PullGesture、Pinch、ClearInput、Launch、Terminate、RunAdbShell、AndroidBackButton、AndroidHomeButton、AndroidRecentAppsButton

### 2. aiQuery — 提取页面数据

```typescript
// 提取结构化数据
const items = await agent.aiQuery(
  '{itemTitle: string, price: number}[], 列出搜索结果的商品名称和价格'
);

// 提取单个值
const buttonText = await agent.aiString('这个按钮上的文字是什么');
const count = await agent.aiNumber('搜索结果是几个商品?');
const isPresent = await agent.aiBoolean('页面上是否有筛选按钮');
```

### 3. aiAssert — 断言验证

```typescript
// AI 驱动的可视化断言
await agent.aiAssert('页面顶部存在搜索框');
await agent.aiAssert('搜索结果列表中至少有 5 个商品');
await agent.aiAssert('"添加到购物车" 按钮处于可点击状态');
await agent.aiAssert('当前页面 URL（或 Activity）包含 "detail"');
```

### 4. aiWaitFor — 等待条件

```typescript
// 等待 UI 状态变化（内部使用截图对比）
await agent.aiWaitFor('搜索结果已加载完成');
await agent.aiWaitFor('页面上的 loading 动画消失');

// 设置超时（默认 10s）
await agent.aiWaitFor('支付成功页面出现', { timeoutMs: 15000 });
```

### 5. aiLocate — 获取元素位置

```typescript
const location = await agent.aiLocate('页面上搜索框的位置');
// 返回 { x: number, y: number, width: number, height: number }
```

## 测试用例设计模式

### 模式一：独立脚本模式（快速验证）

适合：功能点验证、单步调试

```typescript
import { AndroidAgent, AndroidDevice, getConnectedDevices } from '@midscene/android';
import 'dotenv/config';

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

async function main() {
  const devices = await getConnectedDevices();
  const page = new AndroidDevice(devices[0].udid, { autoDismissKeyboard: false });

  const agent = new AndroidAgent(page);
  await page.connect();

  // 启动应用或打开网页
  await page.launch('com.example.app/.MainActivity');
  // 或打开网页: await page.launch('https://example.com');

  await sleep(3000);

  // === 测试步骤 ===
  await agent.aiAct('点击登录按钮');
  await agent.aiWaitFor('登录页面已加载');
  await agent.aiAct('在用户名输入框中输入 "test_user"');
  await agent.aiAct('在密码输入框中输入 "password123"');
  await agent.aiAct('点击登录按钮');
  await agent.aiWaitFor('登录成功，首页已加载');

  // 验证
  await agent.aiAssert('页面上显示用户名 "test_user"');

  // 提取数据
  const userName = await agent.aiString('当前登录用户名是什么');
  console.log('登录用户:', userName);

  await page.disconnect();
}
main();
```

### 模式二：Vitest 测试模式（BDD 风格）

适合：回归测试、持续集成

```typescript
// src/tests/login.test.ts
import { describe, it, beforeAll, afterAll } from 'vitest';
import { AndroidAgent, AndroidDevice, getConnectedDevices } from '@midscene/android';
import 'dotenv/config';

let page: AndroidDevice;
let agent: AndroidAgent;

beforeAll(async () => {
  const devices = await getConnectedDevices();
  page = new AndroidDevice(devices[0].udid, { autoDismissKeyboard: false });
  agent = new AndroidAgent(page, {
    reportFileName: `login-test-${Date.now()}`,
  });
  await page.connect();
});

afterAll(async () => {
  await page.disconnect();
});

describe('登录功能测试', () => {
  it('应能正常启动应用并显示登录页', async () => {
    await page.launch('com.example.app/.MainActivity');
    await agent.aiWaitFor('登录页面已完整加载', { timeoutMs: 15000 });
    await agent.aiAssert('页面上有登录按钮');
  });

  it('应能正确输入用户名密码并登录', async () => {
    await agent.aiAct('在用户名输入框中输入 "test_user"');
    await agent.aiAct('在密码输入框中输入 "password123"');
    await agent.aiAct('点击登录按钮');
    await agent.aiWaitFor('登录成功，页面跳转到首页', { timeoutMs: 20000 });
    await agent.aiAssert('当前页面是登录后的首页');
  });

  it('登录后应显示用户信息', async () => {
    const userName = await agent.aiString('页面上显示的当前用户名是什么');
    expect(userName).toBe('test_user');
  });
});
```

### 模式三：页面对象模式（POM - 推荐）

适合：大型项目、多页面、团队协作

```typescript
// src/pages/login.page.ts
export class LoginPage {
  constructor(private agent: any) {}

  async navigate() {
    // 由 beforeEach 中调用 page.launch 完成
  }

  async waitForPageLoaded() {
    await this.agent.aiWaitFor('登录页面已完全加载', { timeoutMs: 15000 });
  }

  async enterCredentials(username: string, password: string) {
    await this.agent.aiAct(`在用户名输入框中输入 "${username}"`);
    await this.agent.aiAct(`在密码输入框中输入 "${password}"`);
  }

  async clickLogin() {
    await this.agent.aiAct('点击登录按钮');
  }

  async login(username: string, password: string) {
    await this.enterCredentials(username, password);
    await this.clickLogin();
    await this.agent.aiWaitFor('登录成功，页面已跳转', { timeoutMs: 20000 });
  }

  async getErrorMessage(): Promise<string> {
    return this.agent.aiString('页面上显示的登录错误提示信息');
  }
}
```

### 模式四：数据驱动测试

```typescript
// src/tests/checkout.data-driven.test.ts
const testCases = [
  { name: '正常商品', sku: 'SKU001', quantity: 1, expectedTotal: 99.0 },
  { name: '大量购买', sku: 'SKU001', quantity: 10, expectedTotal: 990.0 },
  { name: '商品缺货', sku: 'SKU999', quantity: 1, expectedTotal: null },
];

describe.each(testCases)('结算流程 - $name', ({ sku, quantity, expectedTotal }) => {
  it('应能正确加入购物车并结算', async () => {
    await agent.aiAct(`在搜索框中输入 "${sku}" 并搜索`);
    await agent.aiWaitFor('搜索结果已显示');
    await agent.aiAct('将商品添加到购物车');
    await agent.aiAct('进入购物车页面');
    await agent.aiAct('点击结算按钮');

    if (expectedTotal === null) {
      await agent.aiAssert('页面显示"商品缺货"或"无法购买"的提示');
    } else {
      const total = await agent.aiNumber('订单总金额是多少');
      expect(total).toBe(expectedTotal);
    }
  });
});
```

## AI 提示词最佳实践

### aiAct 操作描述原则

```
✅ 好的描述：
"在用户名输入框中输入 'admin'"
"向下滚动到页面底部"
"点击页面右上角的设置图标"

❌ 不好的描述：
"输入 admin"（缺少目标位置）
"滚动"（不明确方向和距离）
"点那个东西"（不明确）
```

### aiQuery 结构化返回原则

```typescript
// ✅ 给出明确的 JSON Schema
await agent.aiQuery(
  '{name: string, price: number, rating: number}[], 列出所有商品名称、价格和评分'
);

// ✅ 用中文自然语言描述提取目标
await agent.aiQuery(
  '{items: {name: string, stock: string}[], totalPages: number}, 获取搜索结果中所有商品的名称、库存状态及总页数'
);
```

### aiAssert 断言原则

```
✅ 好的断言：
"页面上显示 '登录成功' 的提示文字"
"购物车图标右上角显示数字 1"
"提交按钮处于可点击状态"
"页面标题为 '订单确认'"

❌ 不好的断言：
"页面没问题"（太模糊）
"元素存在"（不明确什么元素）
```

## aiActContext — 全局行为约束

在创建 Agent 时传入 aiActContext，让 AI 理解全局操作上下文：

```typescript
const agent = new AndroidAgent(page, {
  aiActContext: `
    这是一个电商 App 的自动化测试。
    如果有权限弹窗、定位请求、更新提示，点击拒绝或关闭。
    如果出现网络超时提示，等待 3 秒后重试当前操作。
    所有操作前请确认页面已完全加载。
    语言环境为中文。
  `,
});
```

## 坑点与注意事项

- aiQuery 返回的结构化数据受模型能力影响，复杂 Schema 建议先在小范围内验证
- aiAssert 是视觉验证，适合布局和可见性判断，精确数值验证建议配合 aiQuery 提取后做断言
- aiWaitFor 默认超时 10s，对网络请求较重的页面建议设置 timeoutMs: 15000 以上
- 不要在 aiAct 中使用过于复杂的多步操作，建议每步一个 aiAct 调用
- 启动 App 用 `page.launch('package/.Activity')`，打开网页用 `page.launch('https://...')`
- 页面对象模式中，不要混用 page 和 agent 的方法引用，agent 操作依赖截图定位
