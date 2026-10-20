---
name: midscene-android-script-generation
description: 从测试需求/测试用例自动生成 Midscene Android 测试脚本。涵盖自然语言需求转脚本、用例生成、模板选择、批量生成和生产执行的完整流程。
---

# Midscene Android 脚本生成与执行

## 触发条件

当用户需要"生成测试脚本"、"编写自动化代码"、"从需求生成脚本"、"执行测试"时加载此 skill。

## 从测试需求生成脚本 — 四步法

### 第一步：理解测试场景

从用户描述中提取关键要素：
- 被测 App 包名 / Activity 名称
- 前置条件（是否需要登录、特定数据状态）
- 操作步骤（点击、输入、滑动等）
- 验证点（数据校验、UI 状态、页面跳转）

### 第二步：选择脚本模板

看测试场景的类型，选择适当的模板骨架：

**A. 功能冒烟测试模板**
```typescript
// src/tests/smoke.test.ts
import { beforeAll, afterAll, it } from 'vitest';
import { AndroidAgent, AndroidDevice, getConnectedDevices } from '@midscene/android';

let page: AndroidDevice;
let agent: AndroidAgent;

beforeAll(async () => {
  const [device] = await getConnectedDevices();
  page = new AndroidDevice(device.udid);
  agent = new AndroidAgent(page, {
    reportFileName: 'smoke-test',
    aiActContext: '如果有任何弹窗提示，点击确认或关闭。语言为中文。',
  });
  await page.connect();
  await page.launch('com.example.app/.SplashActivity');
  await agent.aiWaitFor('应用首页已加载', { timeoutMs: 20000 });
});

afterAll(async () => {
  await page.disconnect();
});

it('首页加载验证', async () => {
  await agent.aiAssert('首页已完全加载，所有核心元素可见');
});
```

**B. 业务场景测试模板**
```typescript
// src/tests/business-scenario.test.ts
import { beforeAll, afterAll, describe, it } from 'vitest';
import { AndroidAgent, AndroidDevice, getConnectedDevices } from '@midscene/android';
import 'dotenv/config';

let page: AndroidDevice;
let agent: AndroidAgent;

beforeAll(async () => {
  const [device] = await getConnectedDevices();
  page = new AndroidDevice(device.udid);
  agent = new AndroidAgent(page, {
    reportFileName: 'business-scenario',
    aiActContext: '电商测试环境，如有弹窗点击关闭，保持中文界面。',
  });
  await page.connect();
  await page.launch('com.example.app/.MainActivity');
  // 等待首页加载
  await agent.aiWaitFor('应用程序首页已加载完成', { timeoutMs: 20000 });
});

afterAll(async () => {
  await page.disconnect();
});

describe('完整购物流程', () => {
  it('步骤1: 搜索商品', async () => {
    await agent.aiAct('点击首页顶部的搜索框');
    await agent.aiAct('在搜索框中输入 "手机"');
    await agent.aiAct('点击键盘上的搜索按钮');
    await agent.aiWaitFor('搜索结果列表已显示', { timeoutMs: 15000 });
  });

  it('步骤2: 筛选商品', async () => {
    await agent.aiAct('点击"价格从低到高"排序选项');
    await agent.aiWaitFor('排序后的结果已刷新', { timeoutMs: 10000 });
    await agent.aiAssert('搜索结果已按价格排序');
  });

  it('步骤3: 查看商品详情', async () => {
    await agent.aiAct('点击第一个商品进入详情页');
    await agent.aiWaitFor('商品详情页面已加载', { timeoutMs: 15000 });
    await agent.aiAssert('商品名称、价格、描述均正常显示');
  });

  it('步骤4: 加入购物车', async () => {
    await agent.aiAct('点击"加入购物车"按钮');
    await agent.aiWaitFor('加入购物车成功提示已出现', { timeoutMs: 10000 });
  });

  it('步骤5: 结算验证', async () => {
    await agent.aiAct('点击购物车图标进入购物车页面');
    await agent.aiWaitFor('购物车页面已加载', { timeoutMs: 10000 });
    await agent.aiAct('点击"结算"按钮');
    await agent.aiWaitFor('订单确认页面已加载', { timeoutMs: 10000 });
    await agent.aiAssert('订单金额、商品信息显示正确');
  });
});
```

**C. 多设备并行测试模板**
```typescript
// src/tests/parallel.test.ts
import { AndroidAgent, AndroidDevice, getConnectedDevices } from '@midscene/android';

async function runTestOnDevice(udid: string) {
  const page = new AndroidDevice(udid);
  const agent = new AndroidAgent(page, {
    reportFileName: `test-${udid}-${Date.now()}`,
  });
  await page.connect();
  await page.launch('com.example.app/.MainActivity');
  await agent.aiAct('点击登录按钮完成登录流程');
  // ... 测试逻辑
  await page.disconnect();
  console.log(`设备 ${udid} 测试完成`);
}

async function main() {
  const devices = await getConnectedDevices();
  console.log(`发现 ${devices.length} 台设备`);
  await Promise.all(devices.map(d => runTestOnDevice(d.udid)));
}

main().catch(console.error);
```

### 第三步：填充测试用例数据

将用户的测试描述转化为具体的 aiAct / aiQuery / aiAssert 调用。

**需求举例**：用户说"测试登录功能，使用正确的用户名和密码应该能登录成功"
**生成的脚本**：
```typescript
it('正确凭据应能登录成功', async () => {
  await agent.aiAct('在用户名输入框中输入 "admin"');
  await agent.aiAct('在密码输入框中输入 "password123"');
  await agent.aiAct('点击"登录"按钮');
  await agent.aiWaitFor('登录成功后的首页已加载', { timeoutMs: 20000 });
  await agent.aiAssert('页面右上角显示用户头像或用户名');
  const loggedInUser = await agent.aiString('当前登录的用户名是什么');
  expect(loggedInUser).toContain('admin');
});
```

### 第四步：执行脚本

```bash
# 方式一：独立脚本执行
npx tsx src/scripts/demo.ts

# 方式二：Vitest 执行（推荐）
npx vitest run src/tests/login.test.ts

# 方式三：批量执行所有测试
npx vitest run

# 方式四：Watch 模式（开发时）
npx vitest -- src/tests/login.test.ts
```

## 测试脚本生成规范

### 生成原则

| 维度 | 原则 | 说明 |
|------|------|------|
| 原子性 | 每个 aiAct 只做一件事 | 不要写"输入用户名密码并点击登录"，拆分为三步 |
| 可观测 | 每个操作后必有等待或断言 | aiAct 后必须有 aiWaitFor 或 aiAssert |
| 独立性 | 每个测试用例可独立运行 | 不依赖其他用例的执行结果 |
| 数据驱动 | 测试数据与脚本分离 | 使用 describe.each 或外部数据文件 |
| 鲁棒性 | 设置合理的超时 | 所有等待都指定 timeoutMs |

### 文件命名规范

```
src/tests/
├── login.test.ts              # 登录模块
├── register.test.ts           # 注册模块
├── product-search.test.ts     # 商品搜索
├── checkout.test.ts           # 结算流程
├── personal-center.test.ts    # 个人中心
└── smoke.test.ts              # 冒烟测试汇总
```

## 坑点与注意事项

- 设备连接超时：检查 `adb devices` 确认设备状态为 `device`（非 `unauthorized`）
- 屏幕锁定导致测试失败：开发者选项中开启"屏幕保持唤醒"
- 模型 API 超时：确保模型服务可用，建议使用国内可访问的 Qwen3-VL-Plus
- 脚本超时：Vitest 默认 5s 超时，务必在 vitest.config.ts 中设置 testTimeout 到 120000
- 中文输入问题：某些输入框可能需要结合 adb 输入，如遇到中文输入失败，在 AndroidDevice 构造时传入 `autoDismissKeyboard: true`
- 启动 App 的 Activity 名称需自行确认（通过 `adb shell dumpsys window | grep mCurrentFocus` 查看）
- 每个测试文件（describe/it）中需要独立的 beforeAll/afterAll，vtest 默认顺序执行但不保证共享 agent 实例
