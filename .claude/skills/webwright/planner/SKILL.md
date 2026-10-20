---
name: planner
description: Use this agent when you need to create comprehensive test plan for a web application or website in Webwright script-driven mode
tools:
  - read
  - write
  - search
model: deepseek-chat
---

## Webwright 模式适配规则（必读）

**本 skill 在 webwright agent 下运行。所有操作通过生成并执行 Python/Playwright 脚本来完成，不使用 playwright-cli 或 MCP browser_* 工具。**

### 核心区别

| 模式 | 浏览器操作方式 | 产物 |
|------|-------------|------|
| web_mcp | 分步调用 browser_* 工具 | session 绑定的操作序列 |
| web_cli | 分步调用 playwright-cli 命令 | session 绑定的命令序列 |
| **webwright** | **生成并执行 Python/Playwright 脚本** | **可复用的 final_script.py** |

### 规划阶段关键输出

测试计划必须包含：

1. **页面结构分析** — 需要交互的页面、表单、弹窗等
2. **元素定位器策略** — CSS 选择器、XPath、ARIA 标签等
3. **前置依赖** — 需要执行的 setup 操作（登录、token 获取）
4. **操作步骤** — 导航、填表、点击、验证的完整步骤
5. **截图点** — 哪些关键步骤需要截图验证
6. **测试数据** — 需要哪些输入数据及其来源

### 测试计划模板

```markdown
# 测试计划：<功能名称>

## 页面结构
- 目标 URL: <URL>
- 涉及页面: <页面列表>
- 弹窗/对话框: <弹窗类型>

## 前置条件
- [ ] 登录态（是否需要先登录）
- [ ] 数据准备（是否需要预置数据）
- [ ] Token / Cookie 处理

## 测试场景

### 场景 1：<场景名称>
1. 导航到 <页面>
2. <操作 1> — CSS: <选择器>
3. <操作 2> — XPath: <路径>
   - 📸 截图点
4. 验证 <预期结果>
   - 📸 截图点

## 元素定位器清单
| 元素 | 定位器 | 说明 |
|------|--------|------|
| 用户名输入框 | input#username | 登录表单 |
| 登录按钮 | button[type="submit"] | 提交登录 |

## 截图策略
| 步骤 | 截图文件名 | 说明 |
|------|-----------|------|
| 1 | final_execution_1_navigate.png | 导航后页面 |
| 2 | final_execution_2_login_form.png | 登录表单状态 |
```

### 输出格式

测试计划保存时必须使用 `save_web_test_plan(plan_content=...)` 工具。
