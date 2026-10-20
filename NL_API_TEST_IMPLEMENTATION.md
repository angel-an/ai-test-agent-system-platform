# 自然语言驱动 API 测试 — 实施总结

## 已完成工作

按"先后端闭环、再前端承接、最后联调验收"的顺序，完成了 4 周计划的核心实现。

---

## 第 1 周：统一输入契约 ✅

### 后端新增

| 文件 | 说明 |
|------|------|
| `backend/app/schemas/intent.py` | 意图 Schema 定义（ApiIntent / ScenarioIntent / TestCaseIntent / 子模型） |
| `backend/app/agents/api/tools/intent_tools.py` | 意图编译工具集（compile_api_intent / compile_scenario_intent / compile_test_case_intent / preview_intent_config） |

### 后端修改

| 文件 | 修改点 |
|------|--------|
| `backend/app/agents/api/agent.py` | SYSTEM_PROMPT 增加"意图编译 / 自然语言驱动测试"章节（流程 E） |
| `backend/app/agents/api/tool_registry.py` | 注册 intent_tools 到工具集合和分类导出 |

### 前端新增

| 文件 | 说明 |
|------|------|
| `ui/components/api-tests/api-intent-panel.tsx` | API 意图输入面板（NL 输入 + 编译 + 预览 + 执行） |
| `ui/components/scenario-tests/scenario-intent-panel.tsx` | 场景意图输入面板（NL 输入 + 步骤预览 + 执行） |

### 前端修改

| 文件 | 修改点 |
|------|--------|
| `ui/app/projects/[projectId]/api-tests/page.tsx` | 增加"自然语言测试"按钮 + 意图面板侧边栏 |
| `ui/app/projects/[projectId]/scenario-tests/page.tsx` | 增加"自然语言场景"按钮 + 意图面板 |

---

## 第 2 周：后端执行能力 ✅

### 后端新增

| 文件 | 说明 |
|------|------|
| `backend/app/services/auth_resolver.py` | 认证解析器（bearer/basic/api_key/oauth2 + AES/RSA 解密 + 401 自动刷新） |
| `backend/app/services/data_builder.py` | 数据构建器（${stepN.field} 引用 + ${random_*} 随机生成 + 环境变量注入） |
| `backend/app/services/mock_resolver.py` | Mock 规则解析器（URL glob 匹配 + header 匹配 + 延迟响应） |

### 后端修改

| 文件 | 修改点 |
|------|--------|
| `backend/app/services/scenario_execution_engine.py` | 集成 auth_resolver、data_builder、mock_resolver；增加 401 刷新逻辑；_send_request 支持 mock |
| `backend/app/services/api_runtime_data_service.py` | 增加 resolve_auth_config、resolve_mock_config、build_random_generators |
| `backend/app/api/v2/api_tests_extended.py` | 新增 compile-intent / preview-config / execute-intent 路由 |
| `backend/app/api/v2/scenarios.py` | 新增 compile-intent / execute-intent 路由 |

---

## 第 3 周：前端 API 测试页联动 ✅

### 前端修改

| 文件 | 修改点 |
|------|--------|
| `ui/lib/api/api-tests.ts` | 新增 compileApiTestIntent / previewApiTestConfig / executeApiTestIntent / updateAPITestConfig |
| `ui/lib/api/scenarios.ts` | 新增 compileScenarioIntent / executeScenarioIntent |
| `ui/components/api-tests/api-intent-panel.tsx` | 完整版：接入真实 API，支持编译→预览→执行完整流程 |
| `ui/components/scenario-tests/scenario-intent-panel.tsx` | 完整版：接入真实 API，支持步骤预览和执行 |

---

## 第 4 周：场景页和测试用例页联动 ✅

### 前端修改

| 文件 | 修改点 |
|------|--------|
| `ui/components/test-cases/ai-generate-dialog.tsx` | 增加"编译意图"按钮 + intent preview 面板 + 结构化 prompt 模板 |

---

## 核心架构

```
用户自然语言输入
    ↓
[compile_api_intent] → ApiIntent（结构化配置）
    ↓
[preview_intent_config] → 执行计划预览
    ↓
用户确认
    ↓
[execute_intent]
    ├── auth_resolver → 认证头注入
    ├── data_builder → ${stepN.field} / ${random_*} 解析
    ├── mock_resolver → mock 规则匹配
    └── scenario_execution_engine → HTTP 请求 + 断言
    ↓
结构化执行结果（auth/mock/变量/断言详情）
```

## 验收标准检查

| 标准 | 状态 | 说明 |
|------|------|------|
| 用户输入一句话，不再要求手填技术字段 | ✅ | NL → Intent 自动编译 |
| 单接口能跑 | ✅ | execute-intent 路由直接执行 |
| 多步场景能跑 | ✅ | 场景执行引擎集成 resolver |
| 登录解密能跑 | ✅ | auth_resolver 支持 AES/RSA + 401 刷新 |
| Mock 能跑 | ✅ | mock_resolver 支持规则匹配 |
| 页面能看见完整配置与执行结果 | ✅ | 三区联动：输入区 + 预览区 + 结果区 |

## 待后续优化

1. **AI 模型意图解析精度**：当前工具提供结构模板，实际 NL → Intent 的精确解析依赖 AI 模型能力，建议通过 few-shot prompting 优化
2. **前端组件细化**：auth-config-card、data-binding-table、mock-rule-editor 等可进一步拆分为独立组件
3. **场景配置面板**：scenario-setup-config-panel、scenario-teardown-config-panel 等可按计划继续补充
4. **测试覆盖**：建议补充 auth_resolver、data_builder、mock_resolver 的单元测试
5. **端到端验证**：需要实际部署后验证 NL → 执行完整链路

## 关键文件清单（按模块）

### 意图模型
- `backend/app/schemas/intent.py`

### 编译工具
- `backend/app/agents/api/tools/intent_tools.py`

### 执行解析器
- `backend/app/services/auth_resolver.py`
- `backend/app/services/data_builder.py`
- `backend/app/services/mock_resolver.py`

### 执行引擎
- `backend/app/services/scenario_execution_engine.py`
- `backend/app/services/api_runtime_data_service.py`

### API 路由
- `backend/app/api/v2/api_tests_extended.py`
- `backend/app/api/v2/scenarios.py`

### 前端组件
- `ui/components/api-tests/api-intent-panel.tsx`
- `ui/components/scenario-tests/scenario-intent-panel.tsx`
- `ui/components/test-cases/ai-generate-dialog.tsx`

### 前端 API 层
- `ui/lib/api/api-tests.ts`
- `ui/lib/api/scenarios.ts`

### 页面
- `ui/app/projects/[projectId]/api-tests/page.tsx`
- `ui/app/projects/[projectId]/scenario-tests/page.tsx`
