---
name: webwright
description: Webwright 核心技能 — 基于 Python/Playwright 脚本驱动的 Web 自动化模式。智能体不直接操作浏览器，而是编写可复用的 end-to-end 脚本来完成浏览器自动化。
allowed-tools:
  - read
  - write
  - search
  - Bash(python3:*)
  - Bash(mkdir:*)
  - Bash(cp:*)
  - Bash(ls:*)
  - Bash(head:*)
  - Bash(cat:*)
model: deepseek-chat
---

# Webwright：脚本驱动的 Web 自动化

## 核心原则

**智能体不直接操作浏览器。** 一切自动化通过编写并执行 Python/Playwright 脚本来完成。智能体的角色是脚本作者和验证者，而不是浏览器操作员。

## 工作模式

Webwright 支持两种工作模式，通过 SKILL 参数 `mode` 切换：

### 1. 一次性模式（One-Shot Mode）

直接在当前工作目录下一次性完成整个流程：规划 → 探索 → 编写脚本 → 执行 → 验证。

适用场景：快速自动化任务、单次运行测试、探索性自动化。

### 2. CLI 工具模式（CLI Tool Mode）

生成带有完整命令行参数的独立 Python 脚本（内嵌 argparse），可通过 `--url`、`--output-dir` 等参数在不同环境和 URL 上复用以类似 CLI 工具的方式运行。

适用场景：可重复执行的测试套件、CI/CD 集成、多环境验证。

详细参考见 `reference/workflow.md`（CLI 工具模式示例已随 `reference/cli_tool_mode.md` 归档至 `.claude/skills_archive/webwright/`，脚本执行请走 `execute_web_script`）。

### 3. 高性能模式（Fast Mode）

启用性能优化策略，最小化脚本执行时间：
- 资源拦截：禁用图片/CSS/字体/媒体加载
- 快速导航：使用 `domcontentloaded` 替代 `networkidle`
- 登录态缓存：通过 `storage_state` 复用会话
- 按需截图：只在关键验证点和失败时截图
- 并行执行：独立场景使用 `asyncio.gather` 并发

适用场景：大规模回归测试、频繁执行的监控任务、时间敏感场景。

详细参考见 `reference/playwright_patterns_fast.md`。

## 工作空间契约

所有 Webwright 操作都在以下工作空间中完成：

### 目录结构

```
WORKSPACE_DIR/
├── plan.md                           # 测试计划（关键检查点列表）
├── playground/                       # 探索脚本目录（临时文件）
│   ├── explore_<功能>_1_initial.py
│   └── explore_<功能>_2_detail.py
├── final_runs/                       # 所有正式运行的目录
│   ├── run_001/
│   │   ├── final_script.py           # 要执行的脚本
│   │   ├── final_script_log.txt      # 执行日志
│   │   ├── screenshots/              # 截图目录
│   │   │   ├── final_execution_1_navigate.png
│   │   │   ├── final_execution_2_form.png
│   │   │   └── ...
│   │   └── self_reflect_result.json  # 自验证结果
│   ├── run_002/
│   └── run_003/
└── reference/                        # 参考文档
    ├── playwright_patterns.md        # Playwright 编程模式参考（含智能认证、OCR、Token管理）
    └── workflow.md                   # 完整工作流说明
```

### 关键规则

1. **每个新运行必须创建新的 run_<id+1>/ 目录。** 永不覆盖已有 run 目录。
2. **Run ID 使用三位数字填补**：run_001、run_002、...
3. **final_script.py 必须自包含。** 不依赖外部 session、Cookie 或状态。
4. **截图仅使用视口尺寸（1280x1800）。** 禁止 `full_page=True` 截图。
5. **所有截图文件名以 `final_execution_<编号>_<描述>.png` 格式命名。**

## 标准工作流

Webwright 的工作流分为六个阶段：

### Stage 1: 规划（Plan）

目标：理解需求，制定详细计划，输出 plan.md。

输出物：
- `plan.md` — 包含编号的关键检查点（Critical Points，简称 CP）
- 每个 CP 是一个可验证的断言或操作步骤
- CP 编号方式：CP-001、CP-002、...

```
plan.md 示例：
# 测试计划：登录流程自动化

## 关键检查点
- [ ] CP-001: 导航到登录页面，页面标题正确
- [ ] CP-002: 填写用户名和密码
- [ ] CP-003: 点击登录按钮，跳转至控制台
- [ ] CP-004: 页面显示"欢迎回来"提示
```

### Stage 2: 探索（Explore）

目标：通过临时 Playwright 脚本探查页面结构和元素定位器。

方法：
- 编写短小的探索脚本放入 playground/ 目录
- 运行脚本获取 ARIA 快照、可交互元素列表
- 验证关键交互的定位器（选择器）有效性
- 探索脚本命名格式：`explore_<功能>_<序号>_<描述>.py`

探索完成后，将稳定的定位器记录回 plan.md。

### Stage 3: 编写脚本（Author）

目标：基于 plan.md 编写最终的 final_script.py。

要求：
- 自包含：所有 import、配置、逻辑都在一个文件中
- 结构化：配置区、主函数、步骤注释清晰
- 仪器化：每个步骤有日志记录和截图
- 错误处理：超时和异常捕获，失败时截图保存
- 遵循 `reference/playwright_patterns.md` 中的模式

**新增：脚本中应优先使用智能认证模块处理登录**

```python
# 推荐：使用 SmartAuthRouter 自动处理认证
from smart_auth import SmartAuthRouter, AuthConfig, AuthMode

auth_config = AuthConfig(
    login_url="https://example.com/login",
    username="admin",
    password="123456",
    token="eyJhbG...",  # 预获取 Token（可选）
    preferred_mode=AuthMode.TOKEN_URL,  # 优先 Token 方式
    captcha_ocr_enabled=True,  # 启用 OCR 自动识别验证码
)
auth_router = SmartAuthRouter(auth_config)
auth_result = await auth_router.authenticate(page)
```

### Stage 4: 执行（Execute）

目标：通过平台工具运行 final_script.py（唯一脚本执行通道）。

调用：
```
execute_web_script(local_script_path="final_runs/run_001/final_script.py", framework="python", workspace_mode="webwright", project_identifier=..., sub_function_id=...)
```

⛔ 禁止 shell 直跑 `python3|python|node <script>`（SHELL-POLICY-BANNED）

执行产物：
- `final_script_log.txt` — 步骤日志
- `screenshots/*.png` — 各步骤截图

### Stage 5: 自验证（Self-Verify）

目标：对照 plan.md 的关键检查点，逐一验证。

方法：
1. 查看 `final_script_log.txt`，确认执行过程中无错误
2. 逐个检查 screenshots/ 中的截图，验证页面状态
3. 对每个 CP，只在使用**具体证据**（截图内容、日志记录）确认后才标记为通过
4. 输出 `self_reflect_result.json`

### Stage 6: 完成（Done）

目标：确认验证通过后，报告最终结果。

- 如所有 CP 通过 → 报告成功
- 如有 CP 失败 → 进入修复循环（healer），增加 run ID 重新执行
- 最大重试 3 次

## 硬规则（Hard Rules）

1. **一步一个 Bash 命令。** 每次 `execute_command` 调用只执行一条命令。复杂操作用 Python 脚本封装。

2. **使用稳定选择器。** 遵循优先级：`id` > `data-testid` > `name` > `aria-label` > 文本内容 > CSS class > XPath。避免使用动态生成的 class 名或索引。

3. **constraints 必须精确。** 在定义操作约束时，值必须写死（不是变量、通配符或近似匹配）。例如 `page.fill("input#username", "admin")` 而不是 `page.fill("input#username", user_var)`。

4. **视口固定为 1280x1800。** 所有浏览器上下文必须设置此视口。禁止使用 `full_page=True` 截图。

5. **Firefox 优先。** 某些站点在 Chromium 下会出现 `ERR_HTTP2_PROTOCOL_ERROR`。遇到此问题时切换到 Firefox。

6. **模式参数化。** 如果使用 CLI 工具模式，所有可配置项（URL、超时、输出路径）必须通过命令行参数传入，而非硬编码。
7. **性能优先。** 遵循 `reference/playwright_patterns_fast.md` 中的高性能模式：资源拦截、快速导航、登录态缓存、按需截图。

## 新增能力模块

### 1. 智能认证路由（SmartAuthRouter）

**位置**: `backend/app/agents/tools/web/smart_auth.py`

自动选择最佳认证方式，按优先级：
1. URL Token（免登录，无感知）
2. Storage Token（预注入，免登录）
3. 验证码 OCR（自动识别）
4. 人工介入（截图等待输入）

**适用场景**：
- 开发环境（验证码关闭）→ Token 方式
- 测试/验收环境（验证码开启）→ OCR 自动识别
- Token 过期自动刷新

### 2. 验证码 OCR 识别（CaptchaOCR）

**位置**: `backend/app/agents/tools/web/captcha_ocr.py`

支持多种 OCR 引擎：
- **ddddocr** (推荐) — 轻量高效，专为验证码优化
- **paddleocr** — 百度开源，准确率高
- **easyocr** — 多语言支持
- **tesseract** — 传统 OCR

**安装**：`pip install ddddocr`

### 3. Token 管理（TokenManager）

**位置**: `backend/app/agents/tools/web/token_manager.py`

功能：
- 通过登录 API 获取 Token
- Token 自动刷新（在过期前）
- Token 持久化存储（文件/内存）
- 多环境 Token 隔离管理

### 4. 全功能遍历（TraversalEngine）

**位置**: `backend/app/agents/tools/web/traversal_engine.py`

三级遍历策略：
- **菜单级** — 遍历所有一级/二级菜单
- **功能级** — 遍历菜单下的功能入口（按钮、链接、Tab）
- **表单级** — 遍历表单内的字段、操作（增删改查）

特性：
- 智能去重：基于 URL + 页面标题去重
- 危险操作保护：自动跳过删除、批量操作等
- 弹窗处理：自动处理确认弹窗
- 截图记录：每个访问页面自动截图

## 后续参考

- `reference/playwright_patterns.md` — 浏览器启动骨架、元素定位模式、日志格式、截图命名、智能认证、OCR、Token管理、遍历引擎
- `reference/workflow.md` — 各阶段的详细走查与完成清单（脚本执行统一 `execute_web_script`；CLI 工具模式示例已归档）
