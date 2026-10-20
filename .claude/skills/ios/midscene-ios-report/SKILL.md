---
name: midscene-ios-report
description: Midscene iOS 测试报告输出、解析与结果分析。涵盖报告配置、失败分析、WDA 调试、缓存策略和 CI 集成。
---

# Midscene iOS 测试报告与结果分析

## 触发条件

当用户需要"查看 iOS 测试报告"、"分析 iOS 测试结果"、"配置报告输出"、"调试 WDA 连接"、"集成 CI"时加载此 skill。

## 报告机制

Midscene 自动为每个 Agent 实例生成可视化报告，默认输出到 `midscene_run/report/` 目录。

### 报告配置

```typescript
const agent = new IOSAgent(page, {
  generateReport: true,             // 是否生成报告（默认 true）
  reportFileName: 'ios-test-report', // 报告文件名
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
📍 Midscene report: /path/to/project/midscene_run/report/ios-test-report.html
```

直接使用浏览器打开 `.html` 文件即可查看交互式报告。

### 报告内容

HTML 报告包含：
- 测试执行时间轴
- 每一步的 iOS 设备截图
- AI 操作详情（Planning、Action、Observation、Assertion）
- 断言结果（PASS / FAIL）
- 失败时的错误截图和 AI 分析

## 失败分析与调试

### iOS 专属失败原因排查

| 失败症状 | 可能原因 | 解决方案 |
|---|---|---|
| aiAct 无法定位元素 | WDA 截图模糊或 AI 模型对 iOS UI 识别不准 | 检查报告截图；换模型（豆包 Seed / Gemini 优先） |
| aiWaitFor 超时 | iOS 页面动画较慢或 WDA 响应延迟 | 增加 timeoutMs；检查 WDA 是否忙 |
| 截图全黑或不更新 | WDA 连接异常或设备屏幕锁定 | `curl http://localhost:8100/status` 检查 WDA 状态 |
| 键盘遮挡元素 | iOS 键盘弹出后未收起 | Input 时使用 `autoDismissKeyboard: true` 参数 |
| 系统弹窗干扰 | iOS 权限弹窗（定位/通知/相机） | 在 aiActContext 中描述弹窗处理策略 |
| WDA 连接断开 | USB 不稳定或 WDA 进程退出 | 重新启动 WDA，检查 iproxy 转发 |
| 真机测试偶发超时 | 真机通过 USB 传输截图较慢 | 降低截图质量或使用模拟器调试 |
| App 无法启动 | Bundle ID 错误或签名问题 | 用 `xcrun simctl listapps` 验证 Bundle ID |

### WDA 调试命令

```bash
# 检查 WDA 状态
curl http://localhost:8100/status

# 获取当前屏幕截图（手动验证截图质量）
curl -o screenshot.png http://localhost:8100/screenshot

# 检查 WDA 版本信息
curl -s http://localhost:8100/status | python3 -m json.tool

# 检查 iOS 模拟器列表
xcrun simctl list devices

# 启动指定模拟器
xcrun simctl boot "iPhone 15"

# 检查模拟器 App 列表
xcrun simctl listapps booted

# 真机 App 列表（需要 ideviceinstaller）
ideviceinstaller -l
```

### 调试步骤

```bash
# 1. 确认 WDA 服务正常
curl http://localhost:8100/status
# 期望返回 {"value": {"state": "success", "os": {...}, "ios": {...}}}

# 2. 检查截图质量
curl -s -o /tmp/ios-screenshot.png http://localhost:8100/screenshot
ls -lh /tmp/ios-screenshot.png
# 正常截图约 100-500KB，<50KB 说明截图质量极低

# 3. 检查模型 API 连通性
curl $MIDSCENE_MODEL_BASE_URL/models \
  -H "Authorization: Bearer $MIDSCENE_MODEL_API_KEY"

# 4. 如果使用真机，检查端口转发
iproxy 8100 8100
# 确保没有端口冲突
```

### 使用缓存加速调试

在调试阶段频繁执行相同测试时，启用缓存可大幅减少 API 调用：

```typescript
const agent = new IOSAgent(page, {
  cache: {
    id: 'ios-login-scenario-v1',
    strategy: 'read-write',  // 'read-write' | 'read-only' | 'write-only'
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

### GitHub Actions 示例（macOS runner）

```yaml
# .github/workflows/ios-test.yml
name: iOS UI Test

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: macos-14  # iOS 需要 macOS runner
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: 20

      - name: Install dependencies
        run: npm ci

      - name: Start WebDriverAgent
        run: |
          xcrun simctl boot "iPhone 15"
          xcodebuild -project WebDriverAgent.xcodeproj \
            -scheme WebDriverAgentRunner \
            -destination 'platform=iOS Simulator,name=iPhone 15,OS=17.4' \
            test &
          sleep 10

      - name: Verify WDA
        run: curl http://localhost:8100/status

      - name: Run iOS tests
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
          name: midscene-ios-report
          path: midscene_run/report/
```

## 测试结果汇总输出

在测试执行完成后，将结果整理成结构化的报告文档：

```
┌──────────────────────────────────────────────────┐
│  Midscene iOS 自动化测试报告                       │
├──────────────────────────────────────────────────┤
│  执行时间: 2025-06-18 10:30:00                    │
│  设备: iPhone 15 Simulator (iOS 17.4)            │
│  WDA: http://localhost:8100                      │
│  模型: qwen3-vl-plus                             │
│  总用例: 10 | 通过: 8 | 失败: 1 | 跳过: 1        │
├──────────────────────────────────────────────────┤
│  ✅  首页加载验证                                 │
│  ✅  Tab Bar 切换验证                             │
│  ✅  搜索商品                                     │
│  ❌  商品详情页 — 加入购物车                      │
│      → aiAct 失败: 报告截图显示购物车按钮被键盘遮挡  │
│  ✅  结算流程                                     │
│  ✅  个人中心                                     │
│  ✅  下拉刷新                                     │
│  ✅  双指缩放图片                                 │
│  ✅  退出登录                                     │
│  ⏭  真机推送通知测试 (跳过: 模拟器不支持推送)     │
└──────────────────────────────────────────────────┘
```

## 坑点与注意事项

- 报告文件默认存在项目根目录下的 `midscene_run/report/`，在 .gitignore 中应添加此目录
- `persistExecutionDump: true` 会生成大量 JSON 文件，调试时按需开启
- 缓存策略 `read-write` 在首次执行后建立缓存，修改测试描述后需要清除缓存
- 多 Agent 实例同时生成报告可能产生文件名冲突，建议为每个 Agent 设置唯一的 `reportFileName`
- iOS 真机测试的截图通过 USB 传输，比模拟器慢，可能影响 aiWaitFor 的响应时间
- 如果 WDA 返回 404 或 500，常见原因是 WDA 进程崩溃或端口被占用，重新启动即可
- GitHub Actions 的 macOS runner 资源有限，模拟器启动后建议等待 10-15s 再开始测试
