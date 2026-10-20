---
name: midscene-ios-orchestrator
description: Midscene iOS 全流程自动化测试编排器。根据用户的自然语言测试需求，自动完成"计划设计→环境检查→用例设计→脚本生成→脚本执行→报告输出"的全链路自动化测试。这是主入口 skill，加载后按步骤执行即可。
---

# Midscene iOS 全流程自动化测试编排

## 触发条件

主入口 skill。当用户提出"对某个 iOS App 做自动化测试"、"帮我测试 iOS 上的某个功能"、"写一套 iOS 自动化测试脚本"、"全流程走一遍 iOS 自动化测试"时，**必须优先加载此 skill**。该 skill 会自动引用其他 4 个子 skill 完成全流程。

## 全流程概览

```
[用户输入测试场景/需求]
        │
        ▼
1️⃣ 测试计划设计
   └─ 需求分析 → 范围划定 → 功能点拆解 → 优先级排列
        │
        ▼
2️⃣ 环境检查与准备
   └─ WDA 连接 → 模型配置 → 项目结构 → 依赖安装
        │
        ▼
3️⃣ 测试用例设计
   └─ 选择设计模式 → 编写用例步骤 → 定义验证点 → 数据准备
        │
        ▼
4️⃣ 脚本生成
   └─ 选择模板 → 填充业务逻辑 → 生成 Vitest 文件
        │
        ▼
5️⃣ 脚本执行
   └─ 运行测试 → 实时观察 → 处理异常
        │
        ▼
6️⃣ 报告输出与总结
   └─ 解析 HTML 报告 → 统计结果 → 编写测试报告文档
```

## 执行步骤

### 1. 测试计划设计

**从用户描述中提取核心信息**：

| 信息维度 | 需采集的内容 | 示例 |
|---|---|---|
| App 标识 | Bundle ID 或 URL Scheme | `com.example.app` |
| 测试范围 | 功能模块列表 | 登录、首页、搜索、购物车、个人中心 |
| 测试类型 | 冒烟/回归/手势/专项 | 冒烟回归 |
| 核心场景 | 用户关键路径 | 启动App→浏览→搜索→加购→结算 |
| 验证点 | 每步的断言 | 页面跳转、数据显示、状态变更 |
| iOS 特性 | 需要测试的手势/交互 | 下拉刷新、侧滑返回、3D Touch、Face ID |

**输出格式**：Markdown 测试计划文档

````markdown
# 测试计划 - [App名称] iOS 测试

## 1. 测试范围
- 功能点列表（含优先级 P0/P1/P2）
- iOS 特有交互场景
- 覆盖场景描述

## 2. 前置条件
- iOS 模拟器/真机型号
- iOS 版本要求
- 账号/数据准备
- WDA 服务状态

## 3. 测试用例列表
| 用例ID | 场景 | 前置 | 步骤 | 预期结果 | 优先级 |
|--------|------|------|------|---------|--------|
| TC-001 | ... | ... | ... | ... | P0 |

## 4. 测试数据
- 用户名/密码
- 搜索关键词
- 商品 SKU
- Bundle ID 确认
````

### 2. 环境检查与准备

**加载 skill**: `midscene-ios-env-setup`

执行以下检查清单：

```bash
# ① 检查 WDA 连接
curl http://localhost:8100/status || echo "WDA 未启动，请先启动 WebDriverAgent"

# ② 检查 Node.js
node --version

# ③ 检查 .env 配置是否存在
cat .env 2>/dev/null || echo "需要创建 .env"

# ④ 检查依赖是否安装
ls node_modules/@midscene/ios 2>/dev/null || npm install

# ⑤ 检查 macOS 环境
sw_vers  # 确认是 macOS
```

如果项目不存在，按 `midscene-ios-env-setup` 的步骤创建完整脚手架。

### 3. 测试用例设计

**加载 skill**: `midscene-ios-test-case-design`

根据测试计划中的用例列表，为每个用例选择脚本模式：

| 测试类型 | 推荐模式 | 说明 |
|---|---|---|
| 单功能验证 | 独立脚本模式 | 快速验证单个功能点 |
| 回归测试 | Vitest BDD 模式 | 多用例组织+断言 |
| 复杂场景 | 页面对象模式 | 复用页面操作逻辑 |
| 多种数据组合 | 数据驱动模式 | 批量参数化执行 |
| iOS 手势 | 手势测试模板 | 下拉刷新、拖动、缩放 |

输出：每个测试用例对应一组 AI 提示词（aiAct/aiQuery/aiAssert 描述）。

### 4. 脚本生成

**加载 skill**: `midscene-ios-script-generation`

根据设计好的用例和选择的模式，生成 `.test.ts` 脚本文件。

**脚本生成位置约定**：
```
项目根目录/
├── src/tests/        ← Vitest 测试脚本放这里
├── src/scripts/      ← 独立运行脚本放这里
├── src/pages/        ← 页面对象放这里
├── src/agents/       ← IOSDevice/Agent 封装放这里
└── midscene_run/     ← 报告输出目录（自动生成）
```

脚本生成原则：
- 每个文件一个 describe（一个功能模块）
- 每个 it 对应一个完整的原子操作+验证逻辑
- 添加必要的注释说明每个步骤
- 设置合理的超时和等待条件
- 测试完成后调用 `page.disconnect()`
- 处理 iOS 键盘弹出和系统弹窗

### 5. 脚本执行

执行测试并观察过程：

```bash
# 单个测试文件
npx vitest run src/tests/login.test.ts --reporter=verbose

# 全部测试
npx vitest run

# Watch 模式（适合调试）
npx vitest -- src/tests/login.test.ts
```

**执行期间的观察点**：
- 模拟器/真机屏幕是否正常操作
- 操作是否符合预期
- AI 模型推理速度是否可接受
- 有无 iOS 系统弹窗干扰（定位、通知、相机等）

**iOS 专属执行失败快速处理流程**：
```
失败 → 检查报告截图 → 判断失败类型
  ├─ 操作失败 (aiAct 找不到元素/点错位置)
  │   ├─ 截图全黑/模糊 → WDA 截图问题
  │   │   ├─ 检查 WDA 状态: curl http://localhost:8100/status
  │   │   └─ 重启 WDA: 重新运行 xcodebuild test
  │   ├─ 截图正常但 AI 识别错 → 换模型或拆细步骤
  │   └─ 键盘遮挡目标元素 → 加 autoDismissKeyboard 或在操作前先点空白区域
  ├─ 断言失败 (aiAssert 不通过)
  │   ├─ 条件不符 → 修改断言描述
  │   └─ 页面内容与预期不符 → 用 aiQuery 先提取当前内容
  └─ 环境问题
      ├─ WDA 断开 → 检查 iproxy/端口转发
      ├─ 模型 API 错误 → 检查 API Key/配额
      └─ 模拟器卡死 → xcrun simctl shutdown booted && xcrun simctl boot
```

### 6. 报告输出与总结

**加载 skill**: `midscene-ios-report`

执行完成后：

```bash
# 查看报告文件
ls -la midscene_run/report/

# 记录测试结果
echo "测试时间: $(date)" >> test-summary.md
echo "执行的测试文件: src/tests/*.test.ts" >> test-summary.md
```

**生成最终测试报告文档**（Markdown 格式）：

```markdown
# Midscene iOS 自动化测试报告

## 测试概况
- 被测应用: [App名称] (Bundle ID)
- 测试设备: [设备型号 / 模拟器]
- AI 模型: [模型名称]
- 执行时间: [时间范围]

## 测试结果汇总
| 用例ID | 场景名称 | 结果 | 耗时 | 备注 |
|--------|---------|------|------|------|
| TC-001 | 首页加载 | ✅ | 15s | |
| TC-002 | 登录成功 | ✅ | 30s | |
| TC-003 | 搜索商品 | ❌ | 22s | 键盘遮挡导致操作失败 |
| ... | ... | ... | ... | ... |

## 结果统计
- 总用例: N
- 通过: M
- 失败: K
- 通过率: M/N × 100%

## 失败分析
| 用例 | 失败原因 | 根因分析 | 修复建议 |
|------|---------|---------|---------|
| TC-003 | aiAct 元素定位失败 | iOS 键盘弹出遮挡了搜索按钮 | 在输入后使用 autoDismissKeyboard 收起键盘 |

## 报告路径
- 交互式 HTML 报告: `midscene_run/report/*.html`
- 当前文档: `test-summary.md`
```

## 全流程场景示例

### 示例：用户说"帮我测一下 iOS 版的小红书，看看能否正常浏览和搜索"

**Agent 自动执行**：

1. 测试计划设计
   - App: `com.xiaohongshu.app`（小红书）
   - 功能: 首页浏览 → 搜索"旅行" → 查看笔记详情 → 点赞评论
   - 优先级: P0（核心路径）

2. 环境检查
   - `curl http://localhost:8100/status` → WDA 正常
   - `node --version` → v20+
   - `.env` 文件存在，模型配置完整

3. 生成用例
   - TC-001: 启动小红书到首页
   - TC-002: 首页浏览并搜索"旅行"
   - TC-003: 查看笔记详情
   - TC-004: 点赞和评论交互

4. 生成脚本
```typescript
// src/tests/xiaohongshu.test.ts
import { beforeAll, afterAll, describe, it } from 'vitest';
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

let page: IOSDevice;
let agent: IOSAgent;

beforeAll(async () => {
  page = new IOSDevice({ baseUrl: 'http://localhost:8100' });
  agent = new IOSAgent(page, {
    reportFileName: 'xiaohongshu-test',
    aiActContext: '小红书 iOS App 测试。如有权限弹窗点击"允许"。语言为中文。',
  });
  await page.connect();
  await page.launch('com.xiaohongshu.app');
  await agent.aiWaitFor('小红书首页已加载', { timeoutMs: 20000 });
});

afterAll(async () => {
  await page.disconnect();
});

describe('小红书核心功能测试', () => {
  it('首页浏览', async () => {
    await agent.aiAssert('首页推荐内容已正常显示');
    await agent.aiAct('向上滑动浏览推荐笔记');
    await agent.aiWaitFor('新的笔记内容已加载');
  });

  it('搜索功能', async () => {
    await agent.aiAct('点击首页顶部搜索框');
    await agent.aiAct('在搜索框中输入 "旅行"');
    await agent.aiAct('收起键盘，点击搜索');
    await agent.aiWaitFor('搜索结果已显示', { timeoutMs: 15000 });
    await agent.aiAssert('搜索结果包含 "旅行" 相关笔记');
  });

  it('查看笔记详情', async () => {
    await agent.aiAct('点击第一个搜索结果进入笔记详情页');
    await agent.aiWaitFor('笔记详情已加载', { timeoutMs: 10000 });
    await agent.aiAssert('笔记内容（图片和文字）正常显示');
  });

  it('点赞和评论', async () => {
    await agent.aiAct('点击笔记底部的点赞按钮');
    await agent.aiAct('点击评论输入框，输入 "好美的地方！"');
    await agent.aiAct('点击发送按钮发表评论');
    await agent.aiWaitFor('评论已成功发表', { timeoutMs: 10000 });
  });
});
```

5. 执行 → `npx vitest run`

6. 输出报告 → 汇总 HTML 报告 + 摘要文档

## Demo 模式（首次演示）

当用户第一次请求"跑个 Demo"或"演示一下"时，用此模式：

1. 推荐选择 **Safari 浏览器** (`com.apple.mobilesafari`) 作为 Demo 目标
   - 系统内置应用，无需安装/账号/数据准备
   - 功能丰富：导航、搜索、标签页、手势、前进后退
2. 创建完整 10 文件脚手架（参考 `references/demo-execution-guide.md`）
3. 走完全部 6 个测试步骤：启动→访问URL→搜索→前进后退→标签页→滚动
4. 同时生成两种执行方式：Vitest 模式（带报告）和独立脚本模式（CI 可用）

Demo 的每个测试用例严格遵循 **1 act + 1 wait + 1 assert** 原子模式。

## 引用子 skill 速查

| 步骤 | skill 名称 | 作用 |
|------|-----------|------|
| 环境搭建 | `midscene-ios-env-setup` | WDA 配置、项目脚手架、模型 API |
| 用例设计 | `midscene-ios-test-case-design` | API 参考、设计模式、提示词 |
| 脚本生成 | `midscene-ios-script-generation` | 模板选择、代码生成、执行 |
| 报告分析 | `midscene-ios-report` | 报告查看、失败调试、CI 集成 |

## 引用文件

| 文件 | 路径 | 用途 |
|------|------|------|
| Demo 执行指南 | `references/demo-execution-guide.md` | 首次 Demo 的完整操作步骤、注意事项 |
| App 冒烟测试模板 | `templates/app-smoke-test.ts` | 可复用的 Vitest 冒烟测试脚本模板（替换 Bundle ID + 场景即可） |
| 设备工厂模板 | `templates/device-factory.ts` | IOSDevice/IOSAgent 工厂函数，可复制到项目中直接使用 |
| Vitest 配置模板 | `templates/vitest.config.ts` | 标准的 iOS 自动化 Vitest 配置（120s 超时 + 串行执行） |

## 坑点与注意事项

- iOS 自动化必须运行在 macOS 上，不能使用 Linux/Windows
- WDA 启动后需要等待至少 2-3 秒才能稳定响应
- 真机测试需要有效的 Apple Developer 签名，且真机通过 USB 传输截图速度有限
- 多台 iOS 设备需要启动多个 WDA 实例（不同端口），不支持类似 adb 的多设备管理
- iOS 系统弹窗（定位、通知、相机）会阻塞 UI 操作，务必在 aiActContext 中描述弹窗处理策略
- 模拟器下某些功能不可用（推送通知、Face ID、相机），测试计划中需标记跳过
- 键盘弹出是 iOS 自动化的常见坑，建议在输入操作后安排一次点击空白区域来收起键盘
- 缓存对 iOS 测试特别有用，因为 WDA 截图 + AI 推理的组合延迟较高
