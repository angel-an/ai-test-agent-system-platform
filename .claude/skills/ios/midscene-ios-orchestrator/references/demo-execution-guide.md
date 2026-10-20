# iOS 自动化测试 Demo 执行指南

## 适用场景

当用户第一次要求"跑一个 iOS 自动化测试 Demo"或"演示一下怎么用"时，使用此指南快速搭建一个完整的演示项目。

## 推荐 First Demo 选择

**Safari 浏览器** (`com.apple.mobilesafari`) 作为 Demo App 的原因：
- 系统内置应用，无需开发者账号安装
- 无需测试账号/数据准备
- Bundle ID 稳定不变
- 功能丰富（导航、搜索、标签页、手势）
- 谁都知道怎么用手验证

## Demo 项目脚手架（完整 10 文件）

```
ios-test-demo/
├── TEST_PLAN.md                        ← 测试计划（5模块6功能点）
├── TEST_CASES.md                       ← 用例设计（6用例）
├── TEST_REPORT.md                      ← 测试报告（执行后生成）
├── package.json                        ← 依赖声明
├── tsconfig.json                       ← TypeScript 配置
├── vitest.config.ts                    ← Vitest 配置（超时120s+串行）
├── .env.example                        ← 环境变量模板
├── src/
│   ├── agents/
│   │   └── device.ts                   ← IOSDevice/IOSAgent 工厂
│   ├── tests/
│   │   └── safari-smoke.test.ts         ← Vitest 测试脚本（6 it）
│   └── scripts/
│       └── safari-auto.ts              ← 独立运行脚本（CI 友好）
└── midscene_run/report/                ← 报告目录（执行后自动生成）
```

## 快速执行步骤

```bash
# 1. 创建项目
mkdir ios-test-demo && cd ios-test-demo
npm init -y
npm install @midscene/ios typescript tsx vitest
npm install -D @types/node dotenv

# 2. 配置 .env
echo 'WDA_BASE_URL=http://localhost:8100' > .env
echo 'MIDSCENE_MODEL_BASE_URL=https://你的模型地址/v1' >> .env
echo 'MIDSCENE_MODEL_API_KEY=你的key' >> .env
echo 'MIDSCENE_MODEL_NAME=qwen3-vl-plus' >> .env
echo 'MIDSCENE_MODEL_FAMILY=qwen3-vl' >> .env

# 3. 创建 agents/device.ts（设备封装）
cat > src/agents/device.ts << 'EOF'
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

const WDA_BASE_URL = process.env.WDA_BASE_URL || 'http://localhost:8100';

export function createDevice(): IOSDevice {
  return new IOSDevice({ baseUrl: WDA_BASE_URL });
}

export function createAgent(page: IOSDevice, reportName: string): IOSAgent {
  return new IOSAgent(page, {
    generateReport: true,
    reportFileName: reportName,
    aiActContext: `iOS Safari 浏览器自动化测试。
    被测应用: 系统 Safari 浏览器 (Bundle ID: com.apple.mobilesafari)。
    如有权限弹窗点击"允许"或"好"。
    语言为中文。`,
  });
}

export const SAFARI_BUNDLE_ID = 'com.apple.mobilesafari';
EOF

# 4. 创建测试脚本（参考 safari-smoke.test.ts 内容）
#    — 6 个测试: 启动→访问URL→搜索→前进后退→标签页→滚动

# 5. 确认 WDA 在线
curl http://localhost:8100/status

# 6. 执行（二选一）
npx vitest run src/tests/safari-smoke.test.ts    # Vitest 模式
npx tsx src/scripts/safari-auto.ts               # 独立脚本

# 7. 查看报告
open midscene_run/report/*.html
```

## Demo 测试用例设计模式

每个测试用例遵循 **1 act + 1 wait + 1 assert** 的原子模式：

```typescript
it('TC-NNN: 场景描述', async () => {
  await agent.aiAct('用自然语言描述点击/输入/滑动等操作');
  await agent.aiWaitFor('等待条件', { timeoutMs: 15000 });
  await agent.aiAssert('用自然语言描述验证条件');
});
```

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| WDA 连接失败 | WDA 未启动 | `curl http://localhost:8100/status` 确认 |
| 操作超时 | Vitest 默认超时短 | vitest.config.ts 设置 `testTimeout: 120000` |
| iOS 键盘遮挡 | 输入后键盘未收起 | 操作描述中加入"收起键盘" |
| 模拟器无法启动 | Xcode 未安装或版本不兼容 | `xcrun simctl list` 查看可用模拟器 |
| 截图模糊 | WDA 截图分辨率问题 | 重启 WDA: `xcodebuild test ...` |
