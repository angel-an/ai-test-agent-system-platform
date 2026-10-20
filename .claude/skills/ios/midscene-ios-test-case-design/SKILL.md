---
name: midscene-ios-test-case-design
description: Midscene iOS 测试用例设计规范和脚本模式模板。涵盖 iOS 专属 API 调用模式、页面对象模型、数据验证、断言策略、XCTest 集成等完整设计方法。
---

# Midscene iOS 测试用例设计

## 触发条件

当用户需要"设计 iOS 测试用例"、"编写 iOS 测试脚本"、"了解 Midscene iOS API 用法"、"制定 iOS 测试模式"时加载此 skill。

## 与 Android 的关键区别

| 维度 | Android | iOS |
|------|---------|-----|
| 设备连接 | adb + USB | WebDriverAgent（HTTP） |
| 设备类 | AndroidDevice | IOSDevice |
| Agent 类 | AndroidAgent | IOSAgent |
| 导入路径 | @midscene/android | @midscene/ios |
| 设备发现 | getConnectedDevices() | 手动指定 baseUrl |
| 手势 | Tap/Scroll/Pinch/Pull | Tap/Scroll/Pinch/DragAndDrop |
| 应用管理 | launch(activity) | launch(bundleId/URL Scheme) |
| 系统按键 | AndroidBackButton/HomeButton/RecentAppsButton | IOSHomeButton/IOSAppSwitcher |

## Midscene iOS 核心 API 速查

### 1. IOSDevice 构造

```typescript
import { IOSDevice, IOSAgent } from '@midscene/ios';

// 创建设备连接
const page = new IOSDevice({
  baseUrl: 'http://localhost:8100',  // WDA 服务地址
});

// 创建 Agent
const agent = new IOSAgent(page, {
  generateReport: true,
  reportFileName: 'ios-test-report',
  aiActContext: '这是一个 iOS App 的自动化测试。如果有权限弹窗，点击同意。语言为中文。',
});
```

### 2. aiAct — 执行操作（核心）

```typescript
// 执行自然语言描述的操作序列
await agent.aiAct('在搜索框中输入 "iPhone 15"，点击搜索按钮');
await agent.aiAct('向下滚动页面到商品列表底部');
await agent.aiAct('点击第一个商品进入详情页');
await agent.aiAct('点击 "加入购物车" 按钮');
```

iOS 专属动作空间（Action Space）：

- `Tap` — 点击元素
- `DoubleClick` — 双击元素
- `Input` — 输入文本，支持 `replace` / `typeOnly` / `clear` 模式。支持可选参数 `autoDismissKeyboard`
- `Scroll` — 以元素为起点或从屏幕中央向上/下/左/右滚动，支持滚动到顶/底/左/右
- `DragAndDrop` — 从一个元素拖拽到另一个元素
- `KeyboardPress` — 按下指定键位
- `LongPress` — 长按目标元素，可选自定义时长
- `Pinch` — 双指缩放手势（scale > 1 放大，scale < 1 缩小）
- `ClearInput` — 清空输入框内容
- `Launch` — 打开网页、Bundle ID 或 URL Scheme
- `Terminate` — 通过 Bundle ID 关闭正在运行的 iOS 应用
- `RunWdaRequest` — 直接调用 WebDriverAgent REST 接口
- `IOSHomeButton` — 执行 iOS 系统 Home 操作
- `IOSAppSwitcher` — 打开 iOS 多任务视图

### 3. aiQuery — 提取页面数据

```typescript
// 提取结构化数据
const items = await agent.aiQuery(
  '{itemTitle: string, price: number, rating: string}[], 列出商品列表中的名称、价格和评分'
);

// 提取单个值
const buttonText = await agent.aiString('这个按钮上的文字是什么');
const count = await agent.aiNumber('当前页面显示几个商品');
const isPresent = await agent.aiBoolean('页面上是否有搜索筛选按钮');
```

### 4. aiAssert — 断言验证

```typescript
// AI 驱动的可视化断言
await agent.aiAssert('页面顶部存在搜索框');
await agent.aiAssert('搜索结果列表中至少有 5 个商品');
await agent.aiAssert('"加入购物车" 按钮处于可点击状态');
```

### 5. aiWaitFor — 等待条件

```typescript
// 等待 UI 状态变化
await agent.aiWaitFor('搜索结果已加载完成');
await agent.aiWaitFor('页面上的 loading 动画消失');

// 设置超时（默认 10s）
await agent.aiWaitFor('支付成功页面出现', { timeoutMs: 15000 });
```

### 6. aiLocate — 获取元素位置

```typescript
const location = await agent.aiLocate('页面上搜索框的位置');
// 返回 { x: number, y: number, width: number, height: number }
```

## 测试用例设计模式

### 模式一：独立脚本模式（快速验证）

适合：功能点验证、单步调试、手动控制流

```typescript
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

async function main() {
  const page = new IOSDevice({
    baseUrl: 'http://localhost:8100',
  });
  const agent = new IOSAgent(page, {
    reportFileName: `quick-test-${Date.now()}`,
  });

  await page.connect();
  await sleep(2000);

  // 启动 App（通过 Bundle ID）
  await page.launch('com.example.app');
  await agent.aiWaitFor('App 首页已加载', { timeoutMs: 15000 });

  // === 测试步骤 ===
  await agent.aiAct('点击登录按钮');
  await agent.aiAct('在用户名输入框中输入 "test_user"');
  await agent.aiAct('在密码输入框中输入 "password123"');
  await agent.aiAct('点击 "登录" 按钮');
  await agent.aiWaitFor('登录成功，首页已加载', { timeoutMs: 15000 });

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
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

let page: IOSDevice;
let agent: IOSAgent;

beforeAll(async () => {
  page = new IOSDevice({
    baseUrl: 'http://localhost:8100',
  });
  agent = new IOSAgent(page, {
    reportFileName: `login-test-${Date.now()}`,
    aiActContext: 'iOS 测试环境，测试登录功能。如有权限弹窗点击同意。语言为中文。',
  });
  await page.connect();
  await page.launch('com.example.app');
  await agent.aiWaitFor('App 首页已加载', { timeoutMs: 20000 });
});

afterAll(async () => {
  await page.disconnect();
});

describe('登录功能测试', () => {
  it('应能正常启动 App 并显示登录页', async () => {
    await agent.aiAssert('页面上显示登录入口（登录按钮或引导页）');
  });

  it('应能正确输入用户名密码并登录', async () => {
    await agent.aiAct('点击登录按钮进入登录页');
    await agent.aiWaitFor('登录页面已完整加载', { timeoutMs: 10000 });
    await agent.aiAct('在用户名输入框中输入 "test_user"');
    await agent.aiAct('在密码输入框中输入 "password123"');
    await agent.aiAct('点击 "登录" 按钮');
    await agent.aiWaitFor('登录成功，页面跳转到首页', { timeoutMs: 20000 });
    await agent.aiAssert('当前页面是登录后的首页');
  });
});
```

### 模式三：页面对象模式（POM — 推荐）

适合：大型项目、多页面、团队协作

```typescript
// src/pages/login.page.ts
export class LoginPage {
  constructor(private agent: any) {}

  async navigate() {
    // 页面启动在 beforeAll 中通过 page.launch 完成
  }

  async waitForPageLoaded() {
    await this.agent.aiWaitFor('登录页面已完全加载', { timeoutMs: 15000 });
  }

  async enterCredentials(username: string, password: string) {
    await this.agent.aiAct(`在用户名输入框中输入 "${username}"`);
    await this.agent.aiAct(`在密码输入框中输入 "${password}"`);
  }

  async clickLogin() {
    await this.agent.aiAct('点击 "登录" 按钮');
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

// src/pages/home.page.ts
export class HomePage {
  constructor(private agent: any) {}

  async waitForPageLoaded() {
    await this.agent.aiWaitFor('首页已加载，核心元素可见', { timeoutMs: 15000 });
  }

  async searchProduct(keyword: string) {
    await this.agent.aiAct(`点击搜索框输入 "${keyword}" 并搜索`);
    await this.agent.aiWaitFor('搜索结果已显示', { timeoutMs: 15000 });
  }

  async getRecommendedProducts() {
    return this.agent.aiQuery(
      '{name: string, price: number}[], 列出首页推荐商品名称和价格'
    );
  }
}
```

### 模式四：数据驱动测试

```typescript
// src/tests/checkout.data-driven.test.ts
const testCases = [
  { name: '正常商品', product: 'iPhone 15', quantity: 1, expectedTotal: 6999 },
  { name: '大量购买', product: 'AirPods', quantity: 5, expectedTotal: 6495 },
  { name: '缺货商品', product: 'Vision Pro', quantity: 1, expectedTotal: null },
];

describe.each(testCases)('结算流程 - $name', ({ product, quantity, expectedTotal }) => {
  it('应能正确加入购物车并结算', async () => {
    await agent.aiAct(`在搜索框中输入 "${product}" 并搜索`);
    await agent.aiWaitFor('搜索结果已显示');
    await agent.aiAct('将商品添加到购物车');
    await agent.aiAct('进入购物车页面');
    await agent.aiAct('点击结算按钮');

    if (expectedTotal === null) {
      await agent.aiAssert('页面显示"商品缺货"或"无法购买"的提示');
    } else {
      const total = await agent.aiNumber('订单总金额是多少');
      // AI 提取的数值可能存在模型偏差，做近似比较
      expect(Math.abs(total - expectedTotal)).toBeLessThan(expectedTotal * 0.1);
    }
  });
});
```

### 模式五：URL Scheme / Web 页面测试

iOS 支持通过 URL Scheme 或直接打开网页：

```typescript
// 打开网页（Safari 或 WebView）
await page.launch('https://example.com');

// 通过 URL Scheme 唤起 App
await page.launch('myapp://some/path');

// 通过 Bundle ID 启动 App
await page.launch('com.apple.mobilesafari');  // 启动 Safari
```

## AI 提示词最佳实践

### aiAct 操作描述原则（iOS 专属）

```
✅ 好的 iOS 描述：
"点击导航栏右侧的添加按钮"
"向下滑动到页面底部"
"在搜索框中输入 'iPhone 15 Pro'"
"长按图标进入编辑模式"
"双指缩小图片查看缩略图"

❌ 不好的 iOS 描述：
"点那个按钮"（不明确）
"输入内容"（缺少目标位置和内容）
"操作一下"（完全模糊）
```

### iOS 特有场景提示

```typescript
// 处理系统弹窗
await agent.aiAct('点击 "允许" 按钮授予通知权限');
await agent.aiAct('点击 "使用App时允许" 进行定位授权');

// 处理 iOS 键盘
await agent.aiAct('在搜索框中输入 "test"，然后收起键盘');

// 处理 Tab Bar 切换
await agent.aiAct('点击底部 Tab Bar 中的 "我的" 标签');
```

### aiAssert 断言原则

```
✅ 好的 iOS 断言：
"导航栏标题显示为 '商品详情'"
"底部 Tab Bar 的购物车图标上显示数字角标"
"页面使用 iOS 原生风格的列表布局"
"搜索框处于可编辑状态"

❌ 不好的断言：
"页面没问题"（太模糊）
"一切正常"（不可验证）
```

## aiActContext — 全局行为约束

```typescript
const agent = new IOSAgent(page, {
  aiActContext: `
    这是一个 iOS 电商 App 的自动化测试。
    如果有 iOS 系统权限弹窗（定位、通知、相机等），点击"允许"或"使用App时允许"。
    如果出现网络超时提示，等待 3 秒后重试当前操作。
    所有操作前请确认页面已完全加载。
    语言环境为中文。
    App 使用 UIKit 原生界面。
  `,
});
```

## 坑点与注意事项

- iOS 没有 adb 这种设备管理工具，通过 WDA 的 HTTP API 控制设备
- aiQuery 返回的结构化数据受模型能力影响，复杂 Schema 建议先在小范围内验证
- aiAssert 是视觉验证，适合布局和可见性判断，精确数值验证建议配合 aiQuery 提取后做断言
- aiWaitFor 默认超时 10s，对 WebView 加载较重的页面建议设置 timeoutMs: 15000 以上
- iOS 键盘弹出可能会挡住输入框，使用 `Input` 的 `autoDismissKeyboard` 参数
- 启动 App 用 `page.launch('com.example.app')`（Bundle ID），打开网页用 `page.launch('https://...')`
- WDA 不支持同时连接多台 iOS 设备，如需多设备测试需要启动多个 WDA 实例（不同端口）
- 不要在每个测试文件中重复创建 page/agent 实例，建议在 setup 文件中统一管理
