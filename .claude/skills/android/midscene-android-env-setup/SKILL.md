---
name: midscene-android-env-setup
description: Midscene Android 自动化测试的环境搭建与项目初始化。负责 adb 连接、Android SDK 配置、项目脚手架、模型 API 配置的完整流程。
---

# Midscene Android 环境搭建

## 触发条件

当用户需要"搭建 Midscene Android 环境"、"初始化 Android 测试项目"、"配置 adb 连接"、"设置模型 API"等场景时加载此 skill。

## 前置检查

运行以下命令确认基本环境：

```bash
adb --version
echo $ANDROID_HOME
node --version
npm --version
```

## 步骤

### 1. 安装 adb 与 Android SDK

如果没有安装 adb，通过包管理器安装：

```bash
# Ubuntu / Debian
sudo apt install adb

# macOS
brew install android-platform-tools
```

设置 ANDROID_HOME（自行替换路径）：

```bash
export ANDROID_HOME=~/Android/Sdk
export PATH=$PATH:$ANDROID_HOME/platform-tools
```

### 2. 连接 Android 设备

```bash
# 列出已连接的设备
adb devices

# 如果设备未授权，在手机上确认 USB 调试授权
# 确认设备序列号出现且为 "device" 状态
```

### 3. 创建项目脚手架

```bash
mkdir my-android-test && cd my-android-test
npm init -y
```

安装必备依赖：

```bash
npm install @midscene/android typescript tsx vitest
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

### 4. 创建环境变量配置

创建 `.env` 文件：

```bash
# 模型服务配置
MIDSCENE_MODEL_BASE_URL="https://你的模型服务地址/v1"
MIDSCENE_MODEL_API_KEY="你的 API Key"
MIDSCENE_MODEL_NAME="qwen3-vl-plus"
MIDSCENE_MODEL_FAMILY="qwen3-vl"
```

创建 `.env.example` 作为模板提交到版本控制。

### 5. 创建的目录结构

```
my-android-test/
├── src/
│   ├── agents/          # AndroidAgent 实例封装
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

### 6. 创建设备管理封装

`src/agents/device.ts`：

```typescript
import { AndroidAgent, AndroidDevice, getConnectedDevices } from '@midscene/android';
import 'dotenv/config';

export async function createDevice(autoDismissKeyboard = false) {
  const devices = await getConnectedDevices();
  if (devices.length === 0) {
    throw new Error('未检测到 Android 设备，请确认 adb 已连接');
  }
  const page = new AndroidDevice(devices[0].udid, { autoDismissKeyboard });
  return page;
}

export async function createAgent(page: AndroidDevice, aiActContext?: string) {
  const agent = new AndroidAgent(page, {
    generateReport: true,
    reportFileName: `report-${Date.now()}`,
    aiActContext: aiActContext || '如果有定位、权限、用户协议等弹窗，点击同意。如果登录页面弹出，关闭它。',
  });
  return agent;
}
```

### 7. 创建 Vitest 配置文件

`vitest.config.ts`：

```typescript
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    testTimeout: 120_000,  // 2分钟超时
    hookTimeout: 60_000,
    globals: true,
  },
});
```

### 8. 验证环境

运行以下脚本来验证环境是否正常：

```typescript
// src/scripts/verify-env.ts
import { getConnectedDevices } from '@midscene/android';

async function main() {
  console.log('正在检测 Android 设备...');
  const devices = await getConnectedDevices();
  if (devices.length === 0) {
    console.error('未检测到设备，请检查 adb 连接');
    process.exit(1);
  }
  console.log(`发现设备: ${devices[0].udid}`);
  console.log('环境验证通过');
}

main();
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
| Gemini 3.5 Flash | gemini-3.5-flash | gemini |
| UI-TARS | ui-tars | ui-tars |

## 坑点与注意事项

- `autoDismissKeyboard` 默认 true，但在 WebView 中 BACK 键可能触发输入框清空，对 Web 类 App 设置为 false
- `getConnectedDevices()` 返回数组，取 `[0]` 确保至少有一个设备
- 每次测试执行前必须调用 `await page.connect()`
- Vitest 超时需设置为 120s+ 以上，因为 AI 模型推理可能较慢
- .env 文件必须包含完整的模型配置，否则 Midscene 会报错
- 确保设备屏幕保持亮屏状态，建议在开发者选项中开启"不锁定屏幕"
