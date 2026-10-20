---
name: midscene-android-orchestrator
description: Midscene Android 全流程自动化测试编排器。根据用户的自然语言测试需求，自动完成"计划设计→环境检查→用例设计→脚本生成→脚本执行→报告输出"的全链路自动化测试。这是主入口 skill，加载后按步骤执行即可。
---

# Midscene Android 全流程自动化测试编排

## 触发条件

主入口 skill。当用户提出"对某个 Android App 做自动化测试"、"帮我测试某个功能"、"写一套自动化测试脚本"、或"全流程走一遍自动化测试"时，**必须优先加载此 skill**。该 skill 会自动引用其他 4 个子 skill 完成全流程。

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
   └─ adb 连接 → 模型配置 → 项目结构 → 依赖安装
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
| App 标识 | 包名 / Activity | `com.example.app/.MainActivity` |
| 测试范围 | 功能模块列表 | 登录、搜索、购物车、结算 |
| 测试类型 | 冒烟/回归/专项 | 冒烟回归 |
| 核心场景 | 用户关键路径 | 打开App→登录→搜索→加购→结算 |
| 验证点 | 每步的断言 | 页面跳转、数据显示、状态变更 |
| 数据要求 | 测试账号/商品 | 账号: test123, 密码: 123456 |

**输出格式**：Markdown 测试计划文档，包含：

````markdown
# 测试计划 - [App名称] [模块名称]

## 1. 测试范围
- 功能点列表（含优先级 P0/P1/P2）
- 覆盖场景描述

## 2. 前置条件
- 设备要求
- 账号/数据准备
- 网络环境

## 3. 测试用例列表
| 用例ID | 场景 | 前置 | 步骤 | 预期结果 | 优先级 |
|--------|------|------|------|---------|--------|
| TC-001 | ... | ... | ... | ... | P0 |

## 4. 测试数据
- 用户名/密码
- 搜索关键词
- 商品 SKU
````

### 2. 环境检查与准备

**加载 skill**: `midscene-android-env-setup`

执行以下检查清单：

```bash
# ① 检查 adb
adb --version || echo "需要安装 adb"

# ② 检查设备连接
adb devices

# ③ 检查 Node.js
node --version

# ④ 检查 .env 配置是否存在
cat .env 2>/dev/null || echo "需要创建 .env"

# ⑤ 检查依赖是否安装
ls node_modules/@midscene/android 2>/dev/null || npm install
```

如果项目不存在，按 `midscene-android-env-setup` 的步骤创建完整脚手架。

### 3. 测试用例设计

**加载 skill**: `midscene-android-test-case-design`

根据测试计划中的用例列表，为每个用例选择脚本模式：

| 测试类型 | 推荐模式 | 说明 |
|---|---|---|
| 单功能验证 | 独立脚本模式 | 快速验证单个功能点 |
| 回归测试 | Vitest BDD 模式 | 多用例组织+断言 |
| 复杂场景 | 页面对象模式 | 复用页面操作逻辑 |
| 多种数据组合 | 数据驱动模式 | 批量参数化执行 |

输出：每个测试用例对应一组 AI 提示词（aiAct/aiQuery/aiAssert 描述）。

### 4. 脚本生成

**加载 skill**: `midscene-android-script-generation`

根据设计好的用例和选择的模式，生成 `.test.ts` 脚本文件。

**脚本生成位置约定**：
```
项目根目录/
├── src/tests/        ← Vitest 测试脚本放这里
├── src/scripts/      ← 独立运行脚本放这里
├── src/pages/        ← 页面对象放这里
└── src/agents/       ← 设备/Agent 封装放这里
```

脚本生成原则：
- 每个文件一个 describe（一个功能模块）
- 每个 it 对应一个完整的原子操作+验证逻辑
- 添加必要的注释说明每个步骤
- 设置合理的超时和等待条件
- 测试完成后调用 `page.disconnect()`

### 5. 脚本执行

执行测试并观察过程：

```bash
# 单个测试文件
npx vitest run src/tests/login.test.ts --reporter=verbose

# 全部测试
npx vitest run

# 带 UI 模式（适合调试）
npx vitest --ui
```

**执行期间的观察点**：
- 设备屏幕是否正常亮起
- 操作是否符合预期
- AI 模型推理速度是否可接受
- 有无异常弹窗干扰

**执行失败的快速处理流程**：
```
失败 → 检查报告截图 → 判断失败类型
  ├─ 操作失败 (aiAct 找不到元素/点错位置)
  │   ├─ 截图模糊/全黑 → 走截图质量排查（详见 references/screenshot-quality-debugging.md）
  │   ├─ 截图正常但 AI 识别错 → 换模型或拆细步骤
  │   └─ 页面状态未稳定 → 加 aiWaitFor
  ├─ 断言失败 (aiAssert 不通过)
  │   ├─ 条件不符 → 修改断言描述
  │   └─ 页面内容与预期不符 → 用 aiQuery 先提取当前内容
  └─ 环境问题
      ├─ adb 断开 → adb kill-server && adb start-server
      ├─ 模型 API 错误 → 检查 API Key/配额
      └─ 屏幕锁定 → adb shell svc power stayon true
```

**截图质量导致识别失败的高效排查路径**（详见 reference 文件）:
```
1. 看报告截图 → 清楚还是模糊?
2. 清楚 → 换模型（豆包/Gemini 优先）
3. 模糊 → 调 adb wm size + aiVisionConfig
4. 仍然不行 → 拆步骤 + 加等待 + 检查设备环境
```

> 详细 6 维度排查法见 skill 自带的 reference 文件: `references/screenshot-quality-debugging.md`

### 6. 报告输出与总结

**加载 skill**: `midscene-android-report`

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
# Midscene Android 自动化测试报告

## 测试概况
- 被测应用: [App名称] (包名)
- 测试设备: [设备型号]
- AI 模型: qwen3-vl-plus
- 执行时间: 2025-06-13 14:30 - 15:00

## 测试结果汇总
| 用例ID | 场景名称 | 结果 | 耗时 | 备注 |
|--------|---------|------|------|------|
| TC-001 | 首页加载 | ✅ | 12s | |
| TC-002 | 登录成功 | ✅ | 25s | |
| TC-003 | 密码错误提示 | ❌ | 18s | 断言描述不准，已调整 |
| ... | ... | ... | ... | ... |

## 结果统计
- 总用例: N
- 通过: M
- 失败: K
- 通过率: M/N × 100%

## 失败分析
| 用例 | 失败原因 | 根因分析 | 修复建议 |
|------|---------|---------|---------|
| TC-003 | aiAssert 失败 | 错误提示文字为英文但断言用了中文 | 将断言改为英文描述 |

## 报告路径
- 交互式 HTML 报告: `midscene_run/report/*.html`
- 当前文档: `test-summary.md`
```

## 全流程场景示例

### 示例：用户说"帮我测一下懂车帝 App 查看小米 SU7 参数"

**Agent 自动执行**：

1. 测试计划设计
   - App: `com.dongchedi.app`（懂车帝）
   - 功能: 首页 → 搜索"小米SU7" → 进入车型详情 → 查看参数
   - 优先级: P0

2. 环境检查 → adb 连接确认、依赖安装、.env 配置

3. 生成用例
   - TC-001: 启动 App 到首页
   - TC-002: 搜索"小米SU7"
   - TC-003: 进入车型页面查看参数

4. 生成脚本
```typescript
it('打开懂车帝并搜索小米SU7', async () => {
  await page.launch('com.dongchedi.app/.MainActivity');
  await agent.aiWaitFor('懂车帝首页已加载', { timeoutMs: 20000 });
  await agent.aiAct('点击首页顶部的搜索框');
  await agent.aiAct('在搜索框中输入 "小米SU7"');
  await agent.aiAct('点击搜索');
  await agent.aiWaitFor('搜索结果已显示', { timeoutMs: 15000 });
  await agent.aiAssert('搜索结果页面包含"小米SU7"相关车型');
});

it('查看车型参数', async () => {
  await agent.aiAct('点击第一个搜索结果进入车型详情页');
  await agent.aiWaitFor('车型详情页已加载', { timeoutMs: 15000 });
  await agent.aiAct('向下滑动到参数区域');
  await agent.aiAssert('车型参数信息已显示');
  const specs = await agent.aiQuery('{name: string, value: string}[], 列出所有显示的参数名称和值');
  console.log('车型参数:', specs);
});
```

5. 执行 → npx vitest run

6. 输出报告 → 汇总 HTML 报告 + 摘要文档

## 坑点与注意事项

- 全流程执行前务必先做环境检查，避免执行到一半发现 adb 未连接
- 测试脚本生成后建议先人工审核骨架结构再执行
- 对于长时间执行（10+ 用例），建议设置分步执行或分批执行
- aiAct 里不要包含时间敏感的指令（如"等待 5 秒"），用 aiWaitFor 替代
- 第一次执行某场景时 AI 较慢（需做视觉规划），缓存启用后后续执行会快很多
- 如果测试中途中断，检查报告截图可知执行到哪一步失败
- 本 skill 是所有子 skill 的入口，加载此 skill 后按步骤逐步执行即可，不需要分别加载每个子 skill
