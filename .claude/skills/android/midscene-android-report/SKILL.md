---
name: midscene-android-report
description: Midscene Android 测试报告输出、解析与结果分析。涵盖报告配置、报告查看、失败分析、缓存策略和 CI 集成。
---

# Midscene Android 测试报告与结果分析

## 触发条件

当用户需要"查看测试报告"、"分析测试结果"、"配置报告输出"、"集成 CI"、"使用缓存"时加载此 skill。

## 报告机制

Midscene 自动为每个 Agent 实例生成可视化报告，默认输出到 `midscene_run/report/` 目录。

### 报告配置

```typescript
const agent = new AndroidAgent(page, {
  generateReport: true,             // 是否生成报告（默认 true）
  reportFileName: 'my-test-report', // 报告文件名（自动生成时间戳）
  outputFormat: 'single-html',      // 'single-html' | 'html-and-external-assets'
  autoPrintReportMsg: true,         // 执行完成后打印报告路径
  persistExecutionDump: false,      // 是否保存每次执行的 JSON dump
});
```

### 输出格式说明

| outputFormat | 输出路径 | 适用场景 |
|---|---|---|
| `single-html` | `midscene_run/report/<name>.html` | 单文件报告，方便分享 |
| `html-and-external-assets` | `midscene_run/report/<name>/index.html` | 大型报告，含独立资源文件 |

## 报告查看

执行完成后控制台会打印报告路径：

```bash
📍 Midscene report: /path/to/project/midscene_run/report/my-test-report.html
```

直接使用浏览器打开 `.html` 文件即可查看交互式报告。

### 报告内容

HTML 报告包含：
- 测试执行时间轴
- 每一步的截图对比
- AI 操作详情（Planning、Action、Observation、Assertion）
- 断言结果（PASS / FAIL）
- 失败时的错误截图和 AI 分析

## 失败分析与调试

### 常见失败原因

| 失败症状 | 可能原因 | 解决方案 |
|---|---|---|
| aiAct 无法定位元素 | 截图与 AI 描述不匹配，或截图本身质量不足 | 检查报告截图是否清晰；如模糊则调高 adb wm density + aiVisionConfig.imageQuality |
| aiWaitFor 超时 | 页面状态变化较慢或预期状态未出现 | 增加 timeoutMs，或检查应用是否异常；也可能是截图未捕捉到目标状态 |
| aiAssert 失败 | 页面内容与断言不符 | 用 aiString/aiQuery 先提取当前页面内容，与预期对比后再调整断言描述 |
| 截图全黑 | 设备屏幕锁定 | 保持屏幕唤醒（`adb shell svc power stayon true`），检查 adb 连接 |
| 截图正常但 AI 频繁定位错误 | 模型视觉能力不足 | 换推荐模型（豆包 Seed / Gemini 3.x 优先） |
| 同一操作偶发成功/失败 | 页面状态不稳定（加载中/动画未结束） | 在操作前加 aiWaitFor('页面稳定，所有动画已完成') |
| 连接断开 | USB 不稳定或 adb 服务异常 | `adb kill-server && adb start-server` |
| 模型 API 错误 | API Key 过期或配额不足 | 检查模型服务状态 |

### 调试步骤

```bash
# 1. 确认设备连接
adb devices

# 2. 查看设备屏幕（实时）
# 一种方法是使用 scrcpy 查看
# 或使用 adb 截图检查
adb exec-out screencap -p > screenshot.png

# 3. 检查截图质量（文件大小可以反映清晰度）
ls -lh screenshot.png
# 1080p 手机正常截图约 100-300KB，<50KB 说明截图质量很低

# 4. 检查应用当前 Activity
adb shell dumpsys window | grep mCurrentFocus

# 5. 如果是模型问题，测试 API
curl $MIDSCENE_MODEL_BASE_URL/models \
  -H "Authorization: Bearer $MIDSCENE_MODEL_API_KEY"
```

### 使用缓存加速调试

在调试阶段频繁执行相同测试时，启用缓存可大幅减少 API 调用：

```typescript
const agent = new AndroidAgent(page, {
  cache: {
    id: 'login-scenario-v1',
    strategy: 'read-write', // 'read-write' | 'read-only' | 'write-only'
    dir: './midscene_cache',
  },
});
```

```bash
# 首次执行：写入缓存
# 后续执行：从缓存读取 AI 计划和定位结果
# 测试完成后清理缓存
rm -rf ./midscene_cache
```

## 集成到 CI 流程

### GitHub Actions 示例

```yaml
# .github/workflows/android-test.yml
name: Android UI Test

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Install dependencies
        run: npm ci

      - name: Setup adb
        run: |
          sudo apt update
          sudo apt install -y adb

      - name: Run Android tests
        env:
          MIDSCENE_MODEL_BASE_URL: ${{ secrets.MIDSCENE_MODEL_BASE_URL }}
          MIDSCENE_MODEL_API_KEY: ${{ secrets.MIDSCENE_MODEL_API_KEY }}
          MIDSCENE_MODEL_NAME: ${{ secrets.MIDSCENE_MODEL_NAME }}
          MIDSCENE_MODEL_FAMILY: ${{ secrets.MIDSCENE_MODEL_FAMILY }}
        run: npx vitest run

      - name: Upload Test Report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: midscene-report
          path: midscene_run/report/
```

### Jenkins 集成

```groovy
// Jenkinsfile
pipeline {
  agent any
  environment {
    MIDSCENE_MODEL_BASE_URL = credentials('midscene-model-base-url')
    MIDSCENE_MODEL_API_KEY = credentials('midscene-model-api-key')
    MIDSCENE_MODEL_NAME = 'qwen3-vl-plus'
    MIDSCENE_MODEL_FAMILY = 'qwen3-vl'
  }
  stages {
    stage('Setup') {
      steps {
        sh 'npm ci'
      }
    }
    stage('Test') {
      steps {
        sh 'npx vitest run'
      }
    }
  }
  post {
    always {
      archiveArtifacts artifacts: 'midscene_run/report/**'
    }
  }
}
```

## 测试结果汇总输出

在测试执行完成后，将结果整理成结构化的报告文档：

```
┌─────────────────────────────────────────────┐
│  Midscene Android 自动化测试报告              │
├─────────────────────────────────────────────┤
│  执行时间: 2025-06-13 14:30:00               │
│  设备: Pixel 6 (adb)                        │
│  模型: qwen3-vl-plus                        │
│  总用例: 12 | 通过: 10 | 失败: 2 | 跳过: 0   │
├─────────────────────────────────────────────┤
│  ✅  首页加载验证                            │
│  ✅  登录功能 - 正确凭据                      │
│  ❌  登录功能 - 错误密码提示                  │
│      → AI 断言失败: 错误提示文字不符预期       │
│  ✅  商品搜索 - 关键词搜索                    │
│  ✅  商品搜索 - 筛选排序                     │
│  ❌  结算流程 - 正常商品结算                  │
│      → aiWaitFor 超时: 结算页面未在15s内加载  │
│  ...                                        │
└─────────────────────────────────────────────┘
```

## 坑点与注意事项

- 报告文件默认存在项目根目录下的 `midscene_run/report/`，在 .gitignore 中应添加此目录
- `persistExecutionDump: true` 会生成大量 JSON 文件，调试时按需开启
- 缓存策略 `read-write` 在首次执行后建立缓存，修改测试描述后需要清除缓存
- 多 Agent 实例同时生成报告可能产生文件名冲突，建议为每个 Agent 设置唯一的 `reportFileName`
- 报告是独立 HTML 文件，可直接打开，不需要启动任何服务器
