---
name: midscene-ios-env-setup
description: Midscene iOS 自动化测试的环境搭建与项目初始化。负责 WebDriverAgent 连接、Xcode 配置、项目脚手架搭建、模型 API 配置的完整流程。
---

# Midscene iOS 环境搭建

## 触发条件

当用户需要"搭建 Midscene iOS 环境"、"初始化 iOS 测试项目"、"配置 WebDriverAgent"、"连接 iOS 设备"、"设置模型 API"时加载此 skill。

## 前置要求

- macOS 操作系统（iOS 开发必需）
- Xcode 及 Xcode Command Line Tools
- iOS 模拟器或已越狱/开发者签名的真机设备
- WebDriverAgent >= 7.0.0
- Node.js >= 18

## 步骤

### 1. 准备 WebDriverAgent 服务

在使用 Midscene iOS 之前，需要先确保 WebDriverAgent（WDA）服务运行正常。

#### 模拟器配置

使用预构建的 WDA，参考官方指南：
- https://github.com/appium/WebDriverAgent/wiki/Run-Prebuilt-WDA

```bash
# 克隆 WebDriverAgent
git clone https://github.com/appium/WebDriverAgent.git
cd WebDriverAgent

# 安装依赖
./Scripts/bootstrap.sh

# 在 Xcode 中打开项目
open WebDriverAgent.xcodeproj
```

在 Xcode 中：
1. 选择 WebDriverAgentLib target
2. 配置 Team（开发者账号）
3. 选择 iOS 模拟器作为目标
4. 选择 Product > Build

#### 真机配置

参考：https://github.com/appium/WebDriverAgent/wiki/Real-Device-Configuration

关键步骤：
1. 在 Xcode 中配置 Apple Developer 账号
2. 设置有效的 Bundle Identifier
3. 配置 Provisioning Profile
4. 对 WebDriverAgentRunner target 进行签名

#### 启动 WDA

```bash
# 方式一：通过 xcodebuild（模拟器）
xcodebuild -project WebDriverAgent.xcodeproj \
  -scheme WebDriverAgentRunner \
  -destination 'platform=iOS Simulator,name=iPhone 15,OS=17.4' \
  test

# 方式二：使用 usbmuxd 转发（真机，需安装 iproxy）
iproxy 8100 8100
```

### 2. 验证 WDA 连接

配置完成后，验证服务是否启动：

```bash
curl http://localhost:8100/status
```

正常响应应包含设备信息、iOS 版本等 JSON 数据。

### 3. 配置 AI 模型环境变量

```bash
export MIDSCENE_MODEL_BASE_URL="https://你的模型服务地址/v1"
export MIDSCENE_MODEL_API_KEY="你的 API Key"
export MIDSCENE_MODEL_NAME="qwen3-vl-plus"
export MIDSCENE_MODEL_FAMILY="qwen3-vl"
```

也可写入 `.env` 文件，通过 `dotenv` 加载。

### 4. 创建项目脚手架

```bash
mkdir my-ios-test && cd my-ios-test
npm init -y
```

安装必备依赖：

```bash
npm install @midscene/ios typescript tsx vitest
npm install -D @types/node dotenv
```

创建 tsconfig.json：

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "esModuleInterop": true,
    "strict": true,
    "skipLibCheck": true
  },
  "include": ["**/*.ts"]
}
```

添加 npm scripts 到 package.json：

```json
{
  "scripts": {
    "test": "vitest run",
    "test:watch": "vitest",
    "run:script": "tsx src/scripts/run.ts"
  }
}
```

### 5. 创建环境变量配置

创建 `.env` 文件：

```bash
# WebDriverAgent 地址
WDA_BASE_URL="http://localhost:8100"

# 模型服务配置
MIDSCENE_MODEL_BASE_URL="https://你的模型服务地址/v1"
MIDSCENE_MODEL_API_KEY="你的 API Key"
MIDSCENE_MODEL_NAME="qwen3-vl-plus"
MIDSCENE_MODEL_FAMILY="qwen3-vl"
```

创建 `.env.example` 作为模板提交到版本控制。

### 6. 目录结构

```
my-ios-test/
├── src/
│   ├── agents/          # IOSDevice / IOSAgent 实例封装
│   │   └── device.ts
│   ├── scripts/         # 独立运行脚本
│   │   └── run.ts
│   ├── tests/           # Vitest 测试用例
│   │   └── demo.test.ts
│   ├── pages/           # 页面对象模型（POM）
│   │   └── home.ts
│   └── utils/           # 工具函数
│       └── sleep.ts
├── midscene_run/        # 报告输出（自动生成）
├── .env
├── .env.example
├── tsconfig.json
├── package.json
└── vitest.config.ts
```

### 7. 创建设备管理封装

`src/agents/device.ts`：

```typescript
import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

const WDA_BASE_URL = process.env.WDA_BASE_URL || 'http://localhost:8100';

export function createDevice() {
  const page = new IOSDevice({
    baseUrl: WDA_BASE_URL,
  });
  return page;
}

export function createAgent(page: IOSDevice) {
  const agent = new IOSAgent(page, {
    generateReport: true,
    reportFileName: `report-${Date.now()}`,
  });
  return agent;
}
```

### 8. 创建 Vitest 配置文件

`vitest.config.ts`：

```typescript
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    testTimeout: 120_000,  // 2分钟超时（AI 推理较慢）
    hookTimeout: 60_000,
    globals: true,
  },
});
```

### 9. 验证环境

运行以下脚本来验证 WDA 连接是否正常：

```typescript
// src/scripts/verify-env.ts
import { IOSDevice } from '@midscene/ios';

const WDA_URL = process.env.WDA_BASE_URL || 'http://localhost:8100';

async function main() {
  console.log(`正在连接 WebDriverAgent: ${WDA_URL}`);
  const page = new IOSDevice({ baseUrl: WDA_URL });
  await page.connect();
  const status = await page.status();
  console.log('设备信息:', JSON.stringify(status, null, 2));
  await page.disconnect();
  console.log('环境验证通过');
}

main().catch(err => {
  console.error('环境验证失败:', err);
  process.exit(1);
});
```

```bash
npx tsx src/scripts/verify-env.ts
```

## 支持的多模态模型

| 模型 | MIDSCENE_MODEL_NAME | MIDSCENE_MODEL_FAMILY |
|------|-------------------|----------------------|
| Qwen3-VL-Plus | qwen3-vl-plus | qwen3-vl |
| Qwen3-VL-Max | qwen3-vl-max | qwen3-vl |
| Doubao-Seed-2.0 | doubao-seed-2.0 | doubao |
| GLM-4.6V | glm-4.6v | glm |
| Gemini 2.5 Flash | gemini-2.5-flash | gemini |

## 坑点与注意事项

- WDA 启动后需要等待几秒才能稳定响应，建议在 connect 后 sleep(2)
- 真机测试需要有效的 Apple Developer 签名，免费账号有设备数量限制（每年 3 台）
- WDA 默认端口 8100，如果冲突可通过环境变量 `WDA_BASE_URL` 修改
- `getConnectedDevices()` 在 iOS 中不适用——iOS 通过 WDA 连接，不支持 adb 那种设备发现
- 每次测试执行前必须调用 `await page.connect()`
- Vitest 超时需设置为 120s+ 以上，因为 AI 模型视觉推理可能较慢
- .env 文件必须包含完整的模型配置，否则 Midscene 会报错
- 确保 iOS 模拟器/真机屏幕保持亮屏状态
