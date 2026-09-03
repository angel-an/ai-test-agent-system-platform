# 框架适配分析方案（最终版）

> 基于项目0527的完整代码分析 + 4个真实项目的回归测试经验（xysjg/hsh/dtyunxi tw-sit/dtyunxi sit）
> 框架当前能力：web_mcp（MCP协议浏览器交互）+ web_cli（playwright-cli命令行交互）双模式
> 适配目标：需求文档→功能测试用例→自动执行 的全链路支持

---

## 一、当前框架能力全景

### 1.1 已有完整能力

| 层级 | 已有内容 | 状态 |
|------|---------|------|
| **Agent** | web_mcp agent（MCP协议调用Playwright）+ web_cli agent（shell命令调用playwright-cli） | ✅ |
| **Models** | WebFunction + WebSubFunction / WebTest + WebTestRun + WebTestResult / TestCase + TestStep / TestRun / TestResult | ✅ |
| **Services** | web_function_service / web_test_service / test_case_service / test_run_service / test_result_service / ScriptExecutionEngine | ✅ |
| **API** | 完整的CRUD API（test_cases / test_runs / test_results / web_tests / web_functions） | ✅ |
| **Skills** | web_mcp + web_cli 两套skills（planner/explorer/executor/generator/healer/reporter/case-designer/prerequisite） | ✅ |
| **Workspace** | web_mcp workspace + web_cli workspace 两套独立目录 | ✅ |
| **工具** | execution_tools(脚本执行报告)/function_tools(功能CRUD)/script_tools(脚本下载)/test_artifacts_tools(成果物管理) | ✅ |
| **配置** | graph.json 通过 web_agent.path 切换 web_mcp/web_cli | ✅ |

### 1.2 缺失能力

| 能力 | 缺失说明 | 来自哪个项目经验 |
|------|---------|----------------|
| **回归测试引擎** | 无自动遍历菜单→验证→报告的能力 | xysjg(36项菜单)/hsh(36项)/dtyunxi tw-sit(34项)/sit(34项) |
| **需求文档→测试用例** | 无需求文档解析器+AI生成用例的能力 | 本次新增需求 |
| **步骤级用例执行** | 只有"完整脚本执行"，无"单条用例逐步骤执行+截图验证" | 框架已有TestCase模型但无执行引擎 |
| **登录策略适配** | 无策略抽象层，每个项目需手动适配 | xysjg无验证码/hsh有验证码img/dtyunxi有验证码canvas+协议勾选 |
| **验证码识别** | 无验证码识别能力（OCR/人工兜底） | hsh img验证码/dtyunxi tw-sit canvas验证码 |
| **页面交互分析器** | 无自动检测菜单类型/路由模式/门户跳转模式 | xysjg自定义div菜单 vs dtyunxi ant-design菜单 |
| **测试结果截图关联** | 截图未结构化关联到TestCase的TestStep | 三个项目共112张截图的手动管理 |
| **多环境对比** | 无跨环境测试结果diff（sit vs uat vs prod） | dtyunxi tw-sit vs sit 差异需手动比对 |
| **菜单排除/包含配置** | 无测试范围过滤（如跳过接口文档、系统配置） | sit环境手动了跳过 |
| **新建-验证闭环** | 无"创建数据→搜索验证→断言存在"的标准循环 | 三个项目共8次新建验证 |

---

## 二、四个项目的共性规律提炼

### 2.1 交互模式分类

#### 登录模式（4种策略）
1. **simple**：只用账号密码（xysjg）
2. **captcha_img**：图片验证码（hsh，img标签，需uuid绑定）
3. **captcha_canvas**：canvas验证码（dtyunxi tw-sit，canvas元素截图）
4. **agreement**：需勾选协议（dtyunxi tw-sit + sit，复选框+用户协议）

#### 菜单模式（2种交互）
1. **ant-design菜单**：`<li.ant-menu-item>` / `<a-sub-menu>`（dtyunxi）
2. **自定义div菜单**：`<div.menuBlock> > <div.menuItem>`（xysjg/hsh）

#### 路由模式（2种）
1. **hash路由**：`#/customer/memberlist`（4个项目全部）
2. **新标签页跳转**：点击门户卡片打开新窗口，URL传递accessToken（xysjg/hsh）

#### 表单模式（2种）
1. **弹窗表单**：Modal + Form（dtyunxi 品牌管理/优惠券管理）
2. **页面表单**：整页表单（dtyunxi 商品管理）

#### 页面类型（5种）
1. **列表页**（table + search bar）：用户管理、订单列表等
2. **看板页**（dashboard/charts）：首页、消费者总览
3. **配置页**（form 表单）：配送配置、系统配置
4. **文档页**（Swagger）：接口文档
5. **编辑页**（弹窗/页面内编辑）：板块管理、角色编辑

### 2.2 回归测试通用流程

```
1. login(target_url, account, password, captcha, agreement)
2. enter_portal(page)
3. switch_to_app(target_app_name)  # 可能有新标签页/iframe
4. for each menu in menu_tree:
   a. click(menu)
   b. verify_page_loaded()  # 检查URL变化 / 数据加载
   c. screenshot()
   d. if has_search: test_search()
   e. if has_create: test_create_and_verify()
5. generate_report()
```

---

## 三、适配方案（完整文件清单）

### 3.1 新增模型（2个）

| 文件 | 说明 | 核心字段 |
|------|------|---------|
| `backend/app/models/regression_test.py` | 回归测试运行 | run_id, environment, target_url, login_config(JSON), status, total_menus, passed, failed, report_path |
| `backend/app/models/regression_result.py` | 逐菜单回归结果 | run_id, menu_path, menu_url, status, has_data, data_count, has_search, search_works, has_create, create_works, screenshot_path, error_message |

### 3.2 扩展模型（1个）

| 文件 | 修改内容 |
|------|---------|
| `backend/app/models/test_case.py` | TestCase增加: `source`(manual/ai_generated/requirement_parsed), `auto_executable`(bool), `requirement_ref`(str), `test_steps`(扩展为含locator/data的步骤) |

### 3.3 新增/修改 services __init__.py（1个）

| 文件 | 说明 |
|------|------|
| `backend/app/services/__init__.py` | 注册新服务: RequirementParserService, TestCaseGeneratorService, RegressionTestService, WebExecutionService, PageAnalyzerService, LoginStrategyService |

### 3.4 新增服务（6个）

| # | 文件 | 用途 | 核心方法 |
|---|------|------|---------|
| 1 | `services/page_analyzer.py` | 页面交互模式自动检测 | `detect_login_strategy(url)` → simple/captcha_img/captcha_canvas/agreement/sso; `detect_menu_type(page)` → ant-menu/custom-div; `detect_route_type(page)` → hash/browser/new-tab; `extract_menu_tree(page)` → MenuNode[]; `detect_page_type(page)` → list/dashboard/config/doc |
| 2 | `services/login_strategy.py` | 登录策略适配 | `get_strategy(page)` → 策略对象; `execute_login(strategy, page, credentials, captcha?)` → success/fail; `build_login_config(url, account_info)` → LoginPageConfig(含selectors) |
| 3 | `services/captcha_solver.py` | 验证码识别 | `solve_from_img(page, img_selector)` → OCR识别; `solve_from_canvas(page, canvas_selector)` → canvas截图→OCR; `solve_with_human(screenshot_path)` → 请求用户输入; 使用tesseract + pytesseract |
| 4 | `services/requirement_parser.py` | 需求文档解析 | `parse_from_markdown(text)` → ParsedRequirement(modules/features/rules/constraints); `parse_from_docx(file)` → 同上; 识别功能模块、业务规则、数据约束、页面元素 |
| 5 | `services/test_case_generator.py` | 测试用例生成 | `generate_from_requirement(parsed_req)` → TestCase[]; 对每个功能点生成正向/负向/边界用例; 为每个用例生成步骤(含locator建议); 标记auto_executable |
| 6 | `services/regression_test_service.py` | 回归测试执行 | `run_regression(config: RegressionConfig)` → RegressionTestRun; 登录→遍历菜单→验证→截图→报告 |
| 7 | `services/web_execution_service.py` | 步骤级用例执行 | `execute_test_case(case, browser_context)` → TestCaseExecution; 逐步骤执行(navigate/click/type/verify/screenshot); 步骤失败截图; 支持重试 |
| 8 | `services/execution/case_runner.py` | 单条用例执行器 | `run_step(step, page, context)` → StepResult; 解析步骤指令为Playwright动作; 失败时auto-heal; 记录截图/日志 |

### 3.5 新增API端点（3个文件）

| # | 文件 | 端点 |
|---|------|------|
| 1 | `api/v2/test_generation.py` | POST `/projects/{id}/test-cases/from-requirement` (上传需求文档→生成用例); POST `/projects/{id}/test-cases/batch-ai-generate` (按模块批量生成) |
| 2 | `api/v2/regression_tests.py` | POST `/projects/{id}/regression-tests/run` (执行回归); GET `/projects/{id}/regression-tests/reports` (历史报告) |
| 3 | `api/v2/test_executions.py` | POST `/projects/{id}/test-runs/{run_id}/execute` (执行); GET `/projects/{id}/test-runs/{run_id}/progress` (进度); GET `/projects/{id}/test-runs/{run_id}/report` (报告导出) |

### 3.6 新增Agent工具（2组）

#### web_mcp 新增工具
| 工具名 | 位置 | 说明 |
|--------|------|------|
| `analyze_page_structure` | tools/analysis_tools.py | 调用page_analyzer分析页面 |
| `execute_test_case_step` | tools/execution_tools.py | 单步执行测试用例 |
| `batch_execute_test_cases` | tools/execution_tools.py | 批量执行测试用例 |
| `run_regression_test` | tools/execution_tools.py | 运行回归测试 |
| `generate_cases_from_requirement` | tools/function_tools.py | 从需求文档生成用例 |
| `solve_captcha` | tools/captcha_tools.py | 自动/人工验证码识别 |

#### web_cli 新增工具
| 工具名 | 位置 | 说明 |
|--------|------|------|
| `analyze_page_structure_cli` | tools/analysis_tools.py | 通过playwright-cli分析页面 |
| `execute_test_case_step_cli` | tools/execution_tools.py | 单步执行测试用例 |
| `run_regression_test_cli` | tools/execution_tools.py | 运行回归测试（CLI实现） |
| `generate_cases_from_requirement` | tools/function_tools.py | 与web_mcp共享 |

**原则**：业务逻辑在services层共享，浏览器交互层各实现一套。

### 3.7 修改Agent System Prompt（2个文件）

| 文件 | 修改内容 |
|------|---------|
| `agents/web_mcp/agent.py` | SYSTEM_PROMPT增加3种工作模式: SCRIPT/CASE/REGRESSION; 登录策略选择逻辑; 用例执行流程; 回归测试流程; 注册新工具 |
| `agents/web_cli/agent.py` | 与web_mcp对称改造 |

### 3.8 新增Skills（5个，web_mcp + web_cli各一份，共享2个）

#### web_mcp 新增skills
| Skill | 路径 | 核心内容 |
|-------|------|---------|
| regression-tester | `.claude/skills/web_mcp/regression-tester/SKILL.md` | 回归测试完整流程：登录策略→菜单遍历→验证→报告；使用browser_* MCP工具 |
| test-case-generator | `.claude/skills/web_mcp/test-case-generator/SKILL.md` | 需求文档解析规则；用例模板（正向/负向/边界）；自动执行标记策略 |
| case-executor | `.claude/skills/web_mcp/case-executor/SKILL.md` | 用例步骤执行步骤；截图策略；失败处理 |
| login-strategy | `.claude/skills/web_mcp/login-strategy/SKILL.md` | 4种策略的检测方法和配置模板 |

#### web_cli 新增skills（对称，使用playwright-cli命令）
| Skill | 路径 | 核心内容 |
|-------|------|---------|
| regression-tester | `.claude/skills/web_cli/regression-tester/SKILL.md` | 使用playwright-cli实现回归测试 |
| case-executor | `.claude/skills/web_cli/case-executor/SKILL.md` | 使用playwright-cli实现用例执行 |

#### 共享skills（不涉及工具调用）
| Skill | 路径 | 说明 |
|-------|------|------|
| test-case-generator | `.claude/skills/test-case-generator/SKILL.md` | 只涉及文本分析和生成，不调用浏览器工具 |

### 3.9 新增Workspace目录（1个）

| 路径 | 用途 |
|------|------|
| `backend/workspace/regression/` | 回归测试截图和报告存储 |
| `backend/workspace/test-cases/` | 用例模板和导入文件存储 |

### 3.10 修改路由注册（1个）

| 文件 | 修改内容 |
|------|---------|
| `api/v2/__init__.py` | 注册新路由: test_generation, regression_tests, test_executions |

### 3.11 修改models __init__.py（1个）

| 文件 | 修改内容 |
|------|---------|
| `models/__init__.py` | 导出RegressionTestRun, RegressionMenuResult |

### 3.12 新增配置（1个）

| 文件 | 用途 |
|------|------|
| `config/regression_config.py` | 回归测试默认配置: max_concurrent_browsers, screenshot_on_every_step, retry_failed_cases, headless, report_format, auto_heal_locators |

---

## 四、完整适配文件清单汇总

### 新增（18个文件）

```
backend/app/models/regression_test.py          — 新增: 回归测试运行模型
backend/app/models/regression_result.py         — 新增: 逐菜单回归结果模型
backend/app/services/page_analyzer.py           — 新增: 页面交互分析器
backend/app/services/login_strategy.py          — 新增: 登录策略适配器
backend/app/services/captcha_solver.py          — 新增: 验证码识别器
backend/app/services/requirement_parser.py      — 新增: 需求文档解析器
backend/app/services/test_case_generator.py     — 新增: 测试用例生成器
backend/app/services/regression_test_service.py — 新增: 回归测试引擎
backend/app/services/web_execution_service.py   — 新增: Web用例步骤执行服务
backend/app/services/execution/case_runner.py   — 新增: 单条用例执行器
backend/app/api/v2/test_generation.py           — 新增: 需求→用例API
backend/app/api/v2/regression_tests.py          — 新增: 回归测试API
backend/app/api/v2/test_executions.py           — 新增: 用例执行API
backend/app/config/regression_config.py         — 新增: 回归测试配置
agents/web_mcp/tools/analysis_tools.py          — 新增: 页面分析工具
agents/web_mcp/tools/captcha_tools.py           — 新增: 验证码工具
agents/web_cli/tools/analysis_tools.py          — 新增: CLI页面分析工具
.claude/skills/test-case-generator/SKILL.md     — 新增: 共享用例生成skill
```

### 修改（15个文件）

```
backend/app/models/test_case.py                 — 扩展: 添加source/auto_executable/requirement_ref字段
backend/app/models/__init__.py                  — 修改: 导出新模型
backend/app/services/__init__.py                — 修改: 注册新服务
backend/app/api/v2/__init__.py                  — 修改: 注册新路由
agents/web_mcp/agent.py                         — 修改: SYSTEM_PROMPT增加3种工作模式+新工具
agents/web_mcp/tool_registry.py                 — 修改: 注册新工具
agents/web_mcp/tools/execution_tools.py          — 修改: 增加execute_test_case_step/batch_execute
agents/web_mcp/tools/function_tools.py           — 修改: 增加generate_cases_from_requirement
agents/web_cli/agent.py                         — 修改: 与web_mcp对称改造
agents/web_cli/tool_registry.py                 — 修改: 注册CLI版新工具
agents/web_cli/tools/execution_tools.py          — 修改: 增加CLI版执行工具
agents/web_cli/tools/function_tools.py           — 修改: 增加CLI版功能工具
.claude/skills/web_mcp/regression-tester/SKILL.md   — 新增: MCP回归测试skill
.claude/skills/web_mcp/case-executor/SKILL.md       — 新增: MCP用例执行skill
.claude/skills/web_mcp/login-strategy/SKILL.md      — 新增: MCP登录策略skill
```

**总计：新增18个文件，修改15个文件，共33个文件需要处理**

---

## 五、需求文档→测试用例→执行 完整工作流

```
┌─────────────────────────────────────────────────────────────────┐
│ 用户上传需求文档(Markdown/Word/TXT)                              │
│ 文件内容: "## 品牌管理\n- 品牌列表...\n- 新增品牌：品牌名称(必填)..." │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ requirement_parser.py                                           │
│ 1. AI解析需求文档，识别功能模块/子模块/业务规则/数据约束             │
│ 2. 输出 ParsedRequirement                                       │
│    ├─ module: "商品管理" → sub_modules: ["品牌管理", "商品类型"]    │
│    ├─ feature: "品牌列表" → rules: ["分页", "每页10条"]            │
│    └─ constraints: {品牌名称: "必填", 品牌编码: "必填、唯一"}       │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ test_case_generator.py                                          │
│ 1. 对每个功能点生成测试用例                                      │
│ 2. 三种类型：正向 / 负向 / 边界                                   │
│ 3. 输出 List[TestCase] (含preconditions/steps/expected/priority) │
│    ├─ TC-BRAND-001: 品牌列表展示 (P0, 自动)                       │
│    ├─ TC-BRAND-002: 品牌列表分页 (P0, 自动)                       │
│    ├─ TC-BRAND-003: 新增品牌-必填全部填写 (P0, 自动)              │
│    ├─ TC-BRAND-004: 新增品牌-名称为空 (P1, 自动)                  │
│    ├─ TC-BRAND-005: 新增品牌-编码重复 (P1, 手动, 需特定数据)      │
│    └─ TC-BRAND-006: 新增品牌-长名称边界 (P2, 自动)                │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 用户审查 & 确认 (AI生成 → 可修改 → 确认保存到DB)                  │
│ API: POST /projects/{id}/test-cases/batch-ai-generate            │
│     (AI生成 → 返回预览 → 用户编辑 → 确认)                        │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ test_run_service.create() → 创建 TestRun                         │
│ API: POST /projects/{id}/test-runs                              │
│     (选择用例 → 创建运行批次)                                     │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ web_execution_service.execute_run()                             │
│ 自动执行(auto_executable=true) 用例:                              │
│                                                                  │
│ 对于 TC-BRAND-003 "新增品牌-必填全部填写":                         │
│                                                                  │
│ Step 1: page_analyzer.detect_login_strategy(url)                 │
│         → 检测到 agreement + simple 策略                          │
│ Step 2: login_strategy.execute(page, credentials)                │
│         → 勾选协议 + 输入账号密码 + 点击登录                       │
│ Step 3: page_analyzer.extract_menu_tree(page)                    │
│         → 导航到"商品管理 > 品牌管理"                               │
│ Step 4: case_runner.run_step("click 新建按钮")                    │
│         → 弹窗表单打开，截图 ✓                                    │
│ Step 5: case_runner.run_step("type 品牌名称 = 测试品牌_0514")      │
│         → 输入框填充，截图 ✓                                      │
│ Step 6: case_runner.run_step("type 品牌编码 = BP_0514_001")       │
│         → 输入框填充，截图 ✓                                      │
│ Step 7: case_runner.run_step("click 保存按钮")                    │
│         → 表单提交，截图 ✓                                        │
│ Step 8: case_runner.run_step("verify 成功提示visible")             │
│         → 检查成功消息，截图 ✓                                     │
│ Step 9: 回到列表页 → 搜索"测试品牌_0514"                           │
│         → 搜索结果包含该品牌，截图 ✓                               │
│                                                                  │
│ 执行结果: TestCaseExecution(status=passed, 截图=9张)             │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 测试报告生成                                                     │
│ ├─ 用例总数 / 通过 / 失败 / 阻塞                                  │
│ ├─ 每个用例的步骤级执行结果(含截图缩略图)                          │
│ ├─ 覆盖率统计(按模块/按优先级)                                     │
│ ├─ 失败原因分析                                                  │
│ └─ 截图ZIP打包保存到MinIO                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 六、web_mcp vs web_cli 两种模式的适配差异

### 6.1 浏览器交互方式的差异

| 操作 | web_mcp | web_cli |
|------|---------|---------|
| 导航 | `browser_navigate(url)` | `playwright-cli open url` |
| 点击 | `browser_click(selector)` | `playwright-cli click selector` |
| 输入 | `browser_type(selector, text)` | `playwright-cli fill selector text` |
| 截图 | `browser_take_screenshot()` | `playwright-cli screenshot` |
| DOM分析 | `browser_snapshot()` | `playwright-cli snapshot` | 或 `page.content()` |
| 新标签页 | MCP自动追踪所有page | 需 `playwright-cli pages` 列出后切换 |
| 对话框 | `browser_handle_dialog()` | 需 `page.on('dialog')` 事件 |

### 6.2 适配策略：业务逻辑共享，交互层分离

```
┌──────────────────────────────────────────────┐
│                services层                      │
│  (regression_test_service / page_analyzer /   │
│   login_strategy / web_execution_service)      │
│                                                │
│  业务逻辑：菜单遍历算法 / 登录流程编排 /         │
│  报告生成 / 用例生成                            │
│  → 只定义"做什么"，不定义"怎么做"                │
└────────┬──────────────┬───────────────────────┘
         │              │
         ▼              ▼
┌────────────────┐  ┌────────────────┐
│ web_mcp tools   │  │ web_cli tools   │
│ (MCP Browser)   │  │ (playwright-cli)│
│                  │  │                 │
│ browser_click    │  │ playwright-cli  │
│ browser_type     │  │  click/fill     │
│ browser_snapshot │  │  snapshot       │
│ browser_*        │  │  screenshot     │
└────────────────┘  └────────────────┘
```

### 6.3 Skills的对称设计

| Skill | web_mcp版本 | web_cli版本 | 差异点 |
|-------|-----------|-----------|--------|
| regression-tester | 引用browser_*工具 | 引用playwright-cli命令 | tools不同 |
| case-executor | 引用browser_*工具 | 引用playwright-cli命令 | tools不同 |
| login-strategy | 引用browser_*工具 | 引用playwright-cli命令 | 检测/执行方式不同 |
| test-case-generator | 共享 | 共享 | 无差异，仅文本生成 |

---

## 七、实现优先级与工作量

| 优先级 | 内容 | 工作量 | 说明 |
|--------|------|--------|------|
| **P0** | `page_analyzer.py` + `login_strategy.py` + `captcha_solver.py`（页面/登录/验证码分析器） | 2天 | 所有Web自动化的基础能力 |
| **P0** | `regression_test_service.py` + regression-tester skill（回归测试引擎） | 2天 | 核心能力：菜单遍历+验证+报告 |
| **P0** | `requirement_parser.py` + `test_case_generator.py` + test-case-generator skill（需求→用例） | 2天 | 核心能力：AI生成测试用例 |
| **P1** | `web_execution_service.py` + `case_runner.py` + case-executor skill（用例步骤执行） | 2天 | 核心能力：自动执行用例 |
| **P1** | 回归测试模型（regression_test.py + regression_result.py）+ models __init__.py | 0.5天 | 持久化回归结果 |
| **P1** | TestCase模型扩展（source/auto_executable/requirement_ref字段） | 0.5天 | 记录用例来源和可执行性 |
| **P1** | API端点（test_generation/regression_tests/test_executions） | 1.5天 | REST接口 |
| **P2** | agent.py改造（SYSTEM_PROMPT增加新模式）+ tool_registry扩展 | 1天 | Agent可使用新模式 |
| **P2** | analysis_tools.py + captcha_tools.py（Agent工具） | 1天 | 工具化登录策略/验证码/页面分析 |
| **P2** | services __init__.py + api __init__.py 注册 | 0.5天 | 服务路由注册 |
| **P2** | login-strategy skill（两个版本） | 0.5天 | 记录4种策略模板 |
| **P3** | regression_config.py | 0.5天 | 默认配置 |
| **P3** | 新增workspace目录（regression/ + test-cases/） | 0.5天 | 文件存储 |
| **P3** | 前端页面（用例列表/回归报告/执行记录） | 2天 | 可视化管理 |

**总计工作量：约16天，P0+P1 核心能力约10天**

---

## 八、关键设计决策

### 8.1 登录策略配置必须以DB/文件方式持久化

4个项目的登录配置不同，每次回归都要人工输入。策略配置应保存在DB中，项目级别关联：

```python
# 在 project 模型中新增 login_config 字段 (JSONB)
login_config = {
    "strategy": "agreement_captcha",
    "username_selector": "#username",
    "password_selector": "#password",
    "captcha_image_selector": "img.login-code-img",
    "captcha_input_selector": "#code",
    "agreement_selector": ".agree-checkbox",
    "submit_selector": ".login-btn",
    "post_login_wait_selector": ".portal-container",
    "new_tab_expected": True
}
```

### 8.2 用例步骤需要包含定位器建议

TestCaseStep 模型当前只有 `action`(操作) 和 `expected_result`(预期结果)，不足以自动执行。需扩展或在步骤外挂载定位器信息：

```python
# 方案：在 TestStep 中增加定位器建议字段 (不用改DB，存在JSONB)
# 或: 在 custom_fields 中存储
{
    "locator_hints": {
        "type": "click",
        "selector": "button:has-text('新建品牌')",
        "fallback_selectors": [".ant-btn-primary", "button.ant-btn"],
        "wait_after": 2000
    }
}
```

### 8.3 回归测试结果的横纵向对比

```python
# RegressionResult 模型包含:
class RegressionMenuResult:
    run_id          # 关联本次运行
    environment     # sit/uat/prod (冗余，方便查询)
    menu_path       # "商品管理 > 品牌管理"
    status          # pass/fail/skip
    data_count      # 列表行数
    has_create      # 有新建按钮
    create_works    # 新建功能正常
    screenshot_path # 截图路径
    
# 跨环境对比:
# SELECT menu_path, 
#   MAX(CASE WHEN environment='tw-sit' THEN status END) as tw_sit,
#   MAX(CASE WHEN environment='sit' THEN status END) as sit,
#   MAX(CASE WHEN environment='tw-sit' THEN data_count END) as tw_sit_count,
#   MAX(CASE WHEN environment='sit' THEN data_count END) as sit_count
# FROM regression_menu_results
# WHERE run_id IN (run_a, run_b)
# GROUP BY menu_path
```

### 8.4 菜单排除/包含列表

```python
# 回归测试配置中增加
class RegressionConfig:
    exclude_menus: List[str] = ["接口文档", "系统配置", "操作日志"]  # 默认跳过
    focus_menus: Optional[List[str]] = None  # 只测试这些菜单
    max_menu_depth: int = 3  # 菜单遍历最大深度
```

---

## 九、与框架现有模式的集成关系

```
现有框架模式                     新增模式
─────────────                   ────────
web_mcp_agent                   
  ├─ 用户给URL+需求              ├─ 用户上传需求文档
  ├─ Agent探索页面                ├─ 调用requirement_parser解析
  ├─ 写Playwright脚本             ├─ 调用test_case_generator生成用例
  ├─ 执行脚本                     ├─ 用户审查确认
  └─ 修复失败脚本                 ├─ 调用web_execution_service执行
                                  └─ 生成报告

web_cli_agent                   regression模式
  ├─ 用户给URL                    ├─ 用户给URL+账号
  ├─ Agent用playwright-cli探索     ├─ 调用page_analyzer检测
  ├─ 写.playwright脚本             ├─ 调用login_strategy登录
  ├─ npx playwright test执行       ├─ 遍历菜单→验证→截图
  └─ 修复                        └─ 生成回归报告

test_case_service               test_case_generator
  ├─ 手动创建/编辑用例              ├─ AI批量生成用例
  ├─ BDD导出                      ├─ 按模块/功能点组织
  └─ 分文件夹管理                  └─ 自动标记可执行性

test_run_service                regression_test_service
  ├─ 选择用例→创建运行批次          ├─ 全量菜单遍历
  ├─ 记录执行结果                  ├─ 自动截图验证
  └─ 统计通过率                   └─ 跨环境对比
```

**关键原则**：新增功能不破坏现有流程。现有脚本模式保持不变，新增的用例模式/回归模式作为独立入口存在。框架根据入口自动选择工作模式。

---

## 十、总结

基于4个真实项目的回归测试经验（xysjg 36项菜单 + hsh 36项菜单 + dtyunxi tw-sit 34项菜单 + sit 34项菜单，共112项菜单验证），框架0527需要新增3条核心链路：

1. **需求文档→测试用例链路**（requirement_parser + test_case_generator）
2. **测试用例自动执行链路**（web_execution_service + case_runner）
3. **全量回归测试链路**（regression_test_service + page_analyzer + login_strategy + captcha_solver）

以及3个基础能力组件（page_analyzer / login_strategy / captcha_solver）支撑上述链路的自动适配能力。

**框架新增33个文件**（18新增 + 15修改），P0+P1核心能力约10天开发工作量，可使框架从"写脚本→执行"的辅助模式升级为"需求→用例→执行→报告"的全链路自动化测试平台，并支持 web_mcp / web_cli 双模式共存。
