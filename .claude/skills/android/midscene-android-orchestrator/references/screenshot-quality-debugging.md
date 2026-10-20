# Midscene Android 截图质量与 AI 识别失败排查指南

## 问题拓扑

截图质量导致 AI 识别失败，根因可能出现在 **6 个层面**。按排查优先级排列：

```
截图不够清楚（输入侧）      ← 先排查这里
    ↓
Midscene 参数压缩了截图      ← 然后检查配置
    ↓
模型多模态视觉能力不足        ← 换模型测试
    ↓
单步操作步骤太粗             ← 拆细步骤
    ↓
页面状态不稳定               ← 加等待
    ↓
物理环境干扰                 ← 最后排查
```

---

## 维度一：截图清晰度（adb 截图质量）

Midscene Android 底层通过 `adb exec-out screencap -p` 获取截图。

### 检查方法

```bash
# 手动截取对比
adb exec-out screencap -p > /tmp/debug_screenshot.png

# 查看截图分辨率
file /tmp/debug_screenshot.png
# 输出示例: PNG image data, 1080 x 1920, 8-bit/color RGBA, non-interlaced
```

### 优化手段

| 手段 | 命令 | 说明 |
|------|------|------|
| 提高显示密度 | `adb shell wm density 420` | 推荐 320-480，越大细节越丰富 |
| 设置分辨率 | `adb shell wm size 1080x1920` | 不低于 720p，手机推荐 1080p |
| 屏幕常亮 | `adb shell svc power stayon true` | 避免测试中屏幕熄灭 |
| 亮度 | `adb shell settings put system screen_brightness 200` | 范围 0-255，推荐 > 150 |

### 阈值参考

- **截图清晰可读** —> 这条路通常没有问题
- **截图模糊、文字发虚** —> 检查设备分辨率设置或模拟器渲染配置
- **截图全黑** —> 屏幕锁定、adb 断开或设备休眠

---

## 维度二：Midscene 图像预处理参数

`AndroidAgent` / `AndroidDevice` 构造时可通过 `aiVisionConfig` 控制截图传给模型前的处理方式。

```typescript
const page = new AndroidDevice(deviceId, {
  aiVisionConfig: {
    screenshotMode: 'full',        // 不压缩截图尺寸
    maxImageSize: 1920,            // 配合设备分辨率
    imageQuality: 0.95,            // 压缩质量，默认可能 0.8
  },
});
```

### 参数说明

| 参数 | 默认值 | 推荐值 | 作用 |
|------|--------|--------|------|
| `screenshotMode` | `'compressed'` | `'full'` | 是否压缩截图后再传给模型 |
| `maxImageSize` | 取决于平台 | 1920 | 截图最大边长/pixel |
| `imageQuality` | 0.8 | 0.95-1.0 | JPEG/WebP 压缩质量 0-1 |

注意：`screenshotMode: 'full'` 和 `imageQuality: 1.0` 会增加 API 调用成本（更大的图片=更多的 token）。

---

## 维度三：模型选择（最关键因素）

Midscene 采用**纯视觉方案**，模型的多模态视觉能力直接决定截图识别成功率。

### 模型推荐排名（官方实测排序）

| 排名 | 模型 | MIDSCENE_MODEL_FAMILY | 特点 |
|------|------|----------------------|------|
| 1 | 豆包 Seed-2.0 / 1.6-Vision | `doubao` | 定位能力最强，但速度偏慢 |
| 2 | Gemini 3.x 系列 | `gemini` | 综合表现好，速度快 |
| 3 | 千问 Qwen3.x-VL | `qwen3-vl` | 开源可选（8B/30B 可私有化部署） |
| 4 | 智谱 GLM-4.6V | `glm` | 新适配，开放体验 |
| 5 | 智谱 AutoGLM | `glm` | 端到端能力 |

### 诊断方法

如果截图本身清晰但 AI 仍然识别错误，尝试：

```bash
# 1. 临时换一个模型系列对比
export MIDSCENE_MODEL_NAME="gemini-2.5-flash"
export MIDSCENE_MODEL_FAMILY="gemini"

# 2. 或者用豆包
export MIDSCENE_MODEL_NAME="doubao-seed-1.6-vision"
export MIDSCENE_MODEL_FAMILY="doubao"
```

### 必须避免的模型

- ❌ 纯文本模型（GPT-4-turbo-tools、DeepSeek-R1、Claude 3 Haiku 等无视觉能力）
- ❌ 视觉能力弱的模型（早期版本的小参数 VLM）
- ❌ 不支持图片理解的多模态模型

---

## 维度四：操作步骤拆分粒度

纯视觉方案的 AI 在**每一张截图上做一次视觉分析**。如果一条 aiAct 包含多个动作（"输入 A、点击 B、选择 C"），AI 需要在**一张截图上**同时定位多目标，识别率下降。

### 反例与正例

```typescript
// ❌ 不好：三步操作放在一个 aiAct 里
await agent.aiAct('在登录页面，输入用户名和密码，点击登录按钮');

// ✅ 好：每步一个 aiAct，每步一张截图
await agent.aiAct('在用户名输入框中输入 "test_user"');
await agent.aiAct('在密码输入框中输入 "password123"');
await agent.aiAct('点击登录按钮');
```

### 拆分原则

| 场景 | 拆分数 | 原因 |
|------|--------|------|
| 简单的 Tap（点击按钮） | 1 个 aiAct | 单目标定位 |
| 输入文本（Input） | 1 个 aiAct / 每个输入框 | 聚焦局部区域 |
| 输入+点击+等待 | 3-4 个 aiAct | 每个动作独立截图分析 |
| 多步拖拽/滑动 | 1-2 个 aiAct | 滑动可以合并 |

---

## 维度五：页面状态稳定性

未稳定页面上的截图包含了加载动画、半渲染状态，AI 在非最终状态上做分析极易误判。

### 优化方法

```typescript
// ✅ 每次操作前等待页面稳定
await agent.aiWaitFor('页面上的加载动画消失');
await agent.aiAct('点击"下一步"按钮');

// ✅ 等待特定 UI 元素出现
await agent.aiWaitFor('搜索结果列表已显示');
await agent.aiQuery('...');
```

### 常见不稳定场景

| 场景 | 等待条件 |
|------|---------|
| 页面切换 | `aiWaitFor('新页面完全加载')` |
| 网络请求 | `aiWaitFor('网络加载指示器消失')` |
| 弹窗动画 | `aiWaitFor('页面状态稳定，所有动画完成')` |
| 列表刷新 | `aiWaitFor('列表内容已刷新')` |

---

## 维度六：物理环境与设备因素

### 模拟器 vs 真机

| 对比项 | 模拟器 | 真机 |
|--------|--------|------|
| 截图清晰度 | 依赖模拟器渲染配置 | 物理分辨率决定 |
| 推荐配置 | 开启 Host GPU + 1080p | 无额外要求 |
| 常见问题 | 默认分辨率低（如 720x1280） | 屏幕亮度太低 |

### 系统设置检查清单

```bash
# 1. 检查字体大小（不要用最小字体）
adb shell settings get system font_scale

# 2. 检查显示大小（不要过小）
adb shell wm density

# 3. 检查屏幕亮度
adb shell settings get system screen_brightness

# 4. 检查暗色模式
adb shell settings get secure ui_night_mode

# 5. 开启屏幕常亮
adb shell svc power stayon true
adb shell settings put global stay_on_while_plugged_in 3
```

### 暗色模式影响

某些模型在暗色背景上识别浅色文字**效果不如亮色模式**。如果识别困难：

```bash
# 关闭暗色模式
adb shell settings put secure ui_night_mode 1
```

---

## 快速诊断流程（按顺序排查）

```
[AI 识别失败]
    │
    ▼
1. 查看 HTML 报告中的截图
    ├── 截图本身模糊/全黑?
    │   └─→ 走维度一（adb 截图质量）+ 维度六（设备因素）
    ├── 截图清楚但 AI 找不到元素?
    │   └─→ 走维度三（换模型）
    ├── 截图清楚，操作大致对但有些偏差?
    │   └─→ 走维度四（拆分步骤）+ 维度五（加等待）
    └── 截图清楚，换模型也没改善？
        └─→ 走维度二（检查 aiVisionConfig）+ 维度六（物理环境）
```

---

## 常用调试脚本片段

### 截图质量检测脚本

```typescript
// scripts/check-screenshot-quality.ts
import { getConnectedDevices } from '@midscene/android';
import { execSync } from 'child_process';
import * as fs from 'fs';

async function checkScreenshotQuality() {
  const devices = await getConnectedDevices();
  if (devices.length === 0) throw new Error('No device');
  
  const udid = devices[0].udid;
  console.log(`Device: ${udid}`);
  
  // 获取设备信息
  const res = execSync(`adb -s ${udid} shell wm size`).toString().trim();
  console.log(`Screen size: ${res}`);
  
  const density = execSync(`adb -s ${udid} shell wm density`).toString().trim();
  console.log(`Screen density: ${density}`);
  
  // 截图并检查文件大小
  execSync(`adb -s ${udid} exec-out screencap -p > /tmp/quality_check.png`);
  const stat = fs.statSync('/tmp/quality_check.png');
  console.log(`Screenshot size: ${(stat.size / 1024).toFixed(1)} KB`);
  
  if (stat.size < 50 * 1024) {
    console.warn('WARNING: Screenshot is very small (<50KB), likely low quality');
  } else {
    console.log('OK: Screenshot size is reasonable');
  }
}
```

### 模型对比测试

在不同模型间切换执行同一个简单操作，对比识别率：

```typescript
// 记录不同模型对同一截图的识别结果
// 需要手动设置环境变量后分别运行
// 
// 运行方式:
// MIDSCENE_MODEL_NAME=doubao-seed-1.6-vision MIDSCENE_MODEL_FAMILY=doubao npx tsx test-model.ts
// MIDSCENE_MODEL_NAME=gemini-3.5-flash MIDSCENE_MODEL_FAMILY=gemini npx tsx test-model.ts
export async function testModelRecognition() {
  const device = await createDevice();
  const agent = await createAgent(device);
  await device.connect();
  await device.launch('com.example.app/.MainActivity');
  
  // 同一个操作，对比不同模型
  const result = await agent.aiAssert('页面上的登录按钮可见且可点击');
  console.log(`Model: ${process.env.MIDSCENE_MODEL_NAME}`);
  console.log(`Recognition: ${result.pass ? 'PASS' : 'FAIL'}`);
  
  await device.disconnect();
}
```
