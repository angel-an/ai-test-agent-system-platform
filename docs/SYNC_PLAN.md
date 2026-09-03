# 测试用例生成Agent同步计划

## 当前状态对比

### 参考项目 (ai-test-agent-system)
- **agent.py**: 完整的MFQ&PPDCS六步测试分析法，支持SIT/UAT双模式，动态模型选择
- **tools.py**: 单文件包含所有工具（43,910字节）
  - SIT暂存机制: stage_testcases/list_staged_testcases/export_all_testcases/clear_staged_testcases
  - UAT暂存机制: stage_uat_scenarios/list_staged_uat_scenarios/export_all_uat_scenarios/clear_staged_uat_scenarios
  - 质量门禁检查: 关键词比例、模糊词检查、9维覆盖、模块分布均衡性
  - 数据库持久化: _db_save_test_cases, _db_save_uat
  - 原型解析: prototype_parse_tool
  - RAG MCP工具集成
- **staging.py**: 内存暂存区，支持SIT和UAT双通道，线程安全，去重机制
- **prototype/**: 完整原型解析目录（Axure/Figma/MasterGo/蓝湖/截图）

### 当前项目 (backend/app/agents/testcase)
- **agent.py**: 简化的六大Skills体系，无SIT/UAT模式区分，固定模型
- **tools/testcase/**: 拆分为4个文件
  - testcase_tools.py: HTTP API调用工具（创建/更新/批量创建）
  - excel_tools.py: 纯Excel导出，无数据库保存，无质量门禁
  - document_tools.py: 文档解析
  - pdf_processor.py: PDF处理
- **无staging.py**: 没有内存暂存区
- **prototype/**: 空目录

## 需要同步的关键差异

### 1. agent.py 系统提示词
**参考项目优势**:
- MFQ&PPDCS六步测试分析法（更专业的方法论）
- SIT/UAT双模式支持（测试模式识别、专属流程）
- 详细的用例格式规范（module必填、keyword必填、9维标记）
- 质量红线（10条硬性规则）
- 暂存与导出机制说明
- UAT四步法

**当前项目优势**:
- BDD模板支持
- 工厂函数模式（asynccontextmanager）
- 上下文注入中间件（project_identifier/folder_id/template_type）
- 工具调用验证中间件
- 错误处理包装器

### 2. tools 需要新增的功能
1. **staging机制**: 内存暂存区（SIT/UAT双通道）
2. **质量门禁**: 导出前的质量检查
3. **UAT导出**: UAT业务场景两段式Excel导出
4. **数据库持久化**: 导出时保存到PostgreSQL
5. **原型解析**: Axure/Figma/MasterGo/蓝湖支持

### 3. Excel导出字段对比

| 字段 | 参考项目 | 当前项目 | 状态 |
|------|---------|---------|------|
| 用例编号 | ✅ id/用例编号 | ✅ id/用例编号 | 一致 |
| 用例标题 | ✅ title/用例标题 | ✅ title/用例标题 | 一致 |
| 所属模块 | ✅ module/所属模块 | ✅ module/所属模块 | 一致 |
| 用例类型 | ✅ type/用例类型 | ✅ type/用例类型 | 一致 |
| 关键词 | ✅ keyword/关键词 | ✅ keyword/关键词 | 一致 |
| 优先级 | ✅ priority/优先级 | ✅ priority/优先级 | 一致 |
| 前置条件 | ✅ preconditions/前置条件 | ✅ preconditions/前置条件 | 一致 |
| 测试步骤 | ✅ steps/测试步骤 | ✅ steps/测试步骤 | 一致 |
| 测试数据 | ✅ test_data/测试数据 | ✅ test_data/测试数据 | 一致 |
| 预期结果 | ✅ expected_results/预期结果 | ✅ expected_results/预期结果 | 一致 |
| 备注 | ✅ remarks/备注 | ✅ remarks/备注 | 一致 |
| 质量门禁 | ✅ 有 | ❌ 无 | 需新增 |
| 暂存机制 | ✅ 有 | ❌ 无 | 需新增 |
| UAT导出 | ✅ 有 | ❌ 无 | 需新增 |
| 数据库保存 | ✅ 有 | ❌ 无 | 需新增 |

## 实施建议

由于差异较大，建议按以下优先级逐步同步：

### 高优先级（核心功能）
1. 同步agent.py系统提示词（MFQ&PPDCS方法论、SIT/UAT双模式）
2. 新增staging.py（内存暂存区）
3. 在testcase_tools.py中新增stage/export工具（带质量门禁）

### 中优先级（增强功能）
4. 新增UAT导出工具
5. 新增原型解析目录和工具
6. 数据库持久化集成

### 低优先级（优化改进）
7. 动态模型选择（多模态支持）
8. 测试模式中间件（TestModeMiddleware）
