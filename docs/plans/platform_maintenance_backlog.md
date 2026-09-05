# 平台维护债跟踪

> 平台基线已冻结（2026-09-05），本文件跟踪后续维护项，按模块分流处理。
> 原则：维护债不阻塞平台基线，排期独立处理。

---

## 1. Legacy 兼容生命周期

### 1.1 当前状态

| 兼容项 | 位置 | 说明 | 清退条件 |
|---|---|---|---|
| `from_legacy_env(prefix="PR2")` | `execution_gates.py:54` | 兼容 PR2_* 环境变量命名 | 存量项目全部迁移至标准命名（LIVE_API / RUN_RISKY_NEG） |
| `test_from_legacy_env` | `test_p0_gates.py:65` | 验证兼容方法本身 | 随 `from_legacy_env` 一并清退 |

### 1.2 存量依赖扫描结果（2026-09-05，更新）

| 位置 | 使用方式 | 类型 | 处理建议 |
|---|---|---|---|
| `tests/api/pr2_storage_recharge/` | 8 个测试文件 + conftest 已迁移至 `_env_gate("LIVE_API")`，README 已更新 | 项目样例 | ✅ 已迁移至标准命名，保留 PR2_* 兼容回退 |
| `tests/platform/test_p0_gates.py:65` | 测试 `from_legacy_env` 兼容方法，运行时触发 DeprecationWarning | 平台测试 | 随兼容方法清退 |
| `app/platform/execution_gates.py:54` | 兼容方法已标注 `@deprecated` | 平台代码 | 待清退 |

### 1.3 决策记录

- **不删除**：`from_legacy_env` 已标注 `@deprecated`，但保留方法体以维持向后兼容
- **不扩展**：严禁为新项目增加新前缀兼容
- **项目样例迁移**：`tests/api/pr2_storage_recharge/` 已迁移至 `_env_gate()`，优先使用标准命名
- **清退触发**：确认无其他存量依赖后，一次性删除 `from_legacy_env` 及对应测试

---

## 2. Pydantic Deprecation（业务代码）

### 2.1 第一批：class Config → ConfigDict

**状态：✅ 已完成并提交（2026-09-05）**

基线中实际存在 **14 处 `class Config`**，涉及 **9 个文件**，已全部清除：

| 文件 | 原行号 | 类名 | 状态 |
|---|---|---|---|
| `app/schemas/test_run.py` | 114 | `TestRunLinks` | ✅ |
| `app/schemas/scenario.py` | 50, 80, 136, 170, 193 | 多个 Scenario 相关类 | ✅ |
| `app/schemas/project.py` | 103 | `ProjectInfo` | ✅ |
| `app/schemas/api_endpoint.py` | 73 | `APIEndpointSummary` | ✅ |
| `app/schemas/folder.py` | 27, 163 | `FolderInfo` 等 | ✅ |
| `app/models/mongodb/version_history.py` | 35 | `TestCaseVersionHistory` | ✅ |
| `app/models/mongodb/audit_log.py` | 66 | `AuditLog` | ✅ |
| `app/models/mongodb/attachment.py` | 39 | `TestCaseAttachment` | ✅ |
| `app/models/mongodb/api_test_log.py` | 53 | `ApiTestDetailLog` | ✅ |

### 2.2 第二批：json_encoders → field_serializer

**状态：✅ 已完成并提交（2026-09-05）**

4 个 MongoDB 模型已迁移至 `@field_serializer`：

| 文件 | 字段覆盖 | 状态 |
|---|---|---|
| `app/models/mongodb/version_history.py` | test_case_id, changed_by, changed_at | ✅ |
| `app/models/mongodb/audit_log.py` | entity_id, performed_by, performed_at | ✅ |
| `app/models/mongodb/attachment.py` | test_case_id, uploaded_by, uploaded_at | ✅ |
| `app/models/mongodb/api_test_log.py` | test_result_id, test_run_id, api_test_id, started_at, completed_at, created_at | ✅ |

**验证结果**：
- 序列化回归测试：`tests/mongodb/test_serialization_regression.py` — 10 passed
- 可复现基线：`tests/platform/ + tests/mongodb/test_serialization_regression.py` — 225 passed, 11 warnings（P3 完成后）
- `json_encoders` 弃用警告已消除

### 2.3 当前警告构成（2026-09-05，P3 完成后）

**可复现基线**：`225 passed, 11 warnings`
- 10 个 PytestCollectionWarning（平台层 8 + MongoDB 模型 2）
- 1 个 DeprecationWarning `from_legacy_env is deprecated`（预期行为，随方法清退消除）

**已消除**：8 个 DeprecationWarning `datetime.utcnow()`（P3 完成）

### 2.4 迁移方案参考

```python
# 旧写法（废弃）
class MyModel(BaseModel):
    class Config:
        populate_by_name = True

# 第一批迁移后
class MyModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

# 第二批迁移后（json_encoders → field_serializer）
class MyModel(BaseModel):
    created_at: datetime

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()
```

### 2.5 序列化回归测试

**状态：✅ 已补（2026-09-05）**

| 测试文件 | 覆盖模型 | 验证点 | 数量 |
|---|---|---|---|
| `tests/mongodb/test_serialization_regression.py` | `TestCaseVersionHistory` | UUID → str, datetime → isoformat, to_document | 3 |
| | `AuditLog` | UUID → str, datetime → isoformat, Enum → str, to_document | 2 |
| | `TestCaseAttachment` | UUID → str, datetime → isoformat, to_document | 2 |
| | `APITestDetailLog` | UUID → str, datetime → isoformat, Optional None, to_document | 3 |

**合计**：10 passed

---

## 3. Pytest Collection Warning（平台层 + MongoDB 模型）

### 3.1 影响范围

| 文件 | 类名 | 类型 |
|---|---|---|
| `app/platform/testcase_contract.py` | `TestCaseType`, `TestCaseStep`, `TestCaseKeyword`, `TestCasePriority` | 平台层 Enum |
| `app/platform/report_templates.py` | `TestResult`, `TestResultCode` | 平台层 Enum |
| `app/models/mongodb/version_history.py` | `TestCaseVersionHistory` | MongoDB 模型 |
| `app/models/mongodb/attachment.py` | `TestCaseAttachment` | MongoDB 模型 |

### 3.2 处理方案

**保持现状**（推荐）
- 这些类名是领域模型的一部分，改名会破坏 API 一致性
- 警告无害，只是 pytest 不会把它们当测试类收集
- 已在 `pytest.ini` 中通过 `filterwarnings` 控制

---

## 4. datetime.utcnow() 弃用（Python 3.12+）

### 4.1 影响范围

**MongoDB 模型默认时间字段：✅ 已完成（2026-09-05，提交 4e22c91）**

4 个 MongoDB 模型已迁移：

| 文件 | 字段 | 状态 |
|---|---|---|
| `version_history.py` | `changed_at` | ✅ |
| `audit_log.py` | `performed_at` | ✅ |
| `attachment.py` | `uploaded_at` | ✅ |
| `api_test_log.py` | `started_at`, `created_at` | ✅ |

**全仓其他使用点：✅ 已完成（2026-09-05）**

全仓扫描已清零，涉及 9 个文件、13 处 `datetime.utcnow()` 已全部迁移：

| 文件 | 类型 | 处数 |
|---|---|---|
| `app/repositories/api_test_repo.py` | 仓储 | 1 |
| `app/services/api_case_link_service.py` | 服务 | 1 |
| `app/services/export_service.py` | 服务 | 3 |
| `app/services/mongodb_service.py` | 服务 | 2 |
| `app/services/scheduler_service.py` | 服务 | 1 |
| `app/services/test_case_service.py` | 服务 | 1 |
| `app/api/v2/test_runs.py` | API | 1 |
| `app/api/v2/security_tests.py` | API | 2 |
| `app/api/v2/documents.py` | API | 1 |

**合计**：9 文件，13 处

### 4.2 迁移方案

```python
# 旧写法（Python 3.12+ 弃用）
from datetime import datetime

datetime.utcnow()

# 新写法（P3 已应用）
from datetime import datetime, timezone

datetime.now(timezone.utc)
```

**兼容性验证**：
- MongoDB BSON Date 类型原生支持带 tzinfo 的 datetime
- `to_document()` 直接传递 datetime 对象给驱动，驱动自动处理时区转换
- `@field_serializer` 输出的 ISO 格式字符串兼容下游消费方
- 序列化回归测试 10 passed，完整基线 225 passed

---

## 5. 测试样例字符串统一（洁净度优化）

### 5.1 当前状态

平台测试中使用的中性示例字符串已基本统一：
- 项目代号：`PROJ-1`、`DEMO-1`
- 测试数据：`demo_user_001`、`13900139000`
- 模块名：`认证`、`登录`、`支付`、`查询`、`写入`

### 5.2 可进一步优化项

| 位置 | 当前 | 建议 |
|---|---|---|
| `test_p0_contract.py` | `号码校验通过` | `格式校验通过`（更通用） |

**优先级**：低。当前字符串已基本中性，不绑定具体业务，属于「洁净度优化」而非「功能阻断项」。

---

## 6. 维护项优先级

| 优先级 | 事项 | 预估工作量 | 状态 |
|---|---|---|---|
| P1 | Pydantic `class Config` → `ConfigDict` 迁移 | 已完成 | ✅ 已提交 |
| P1.5 | Pydantic `json_encoders` → `field_serializer` 迁移 | 已完成 | ✅ 已提交 |
| P2 | Legacy 兼容清退（`from_legacy_env` 已标注 `@deprecated`） | 0.5 天 | 待确认无存量依赖后删除 |
| P3 | MongoDB 模型 `datetime.utcnow()` → `datetime.now(timezone.utc)` | 0.5 天 | ✅ 已完成（4e22c91） |
| P3.5 | 全仓 `datetime.utcnow()` 清理（API、服务、仓储等） | 1 天 | ✅ 已完成（9 文件，13 处） |
| P4 | Pytest 警告消除（重命名领域模型） | 1 天 | 保持现状 |
| P5 | 测试样例字符串再统一 | 0.5 天 | 低优 |

---

## 7. 冻结声明

**平台通用规则（`backend/app/platform/` + `backend/tests/platform/`）自 2026-09-05 起内容口径冻结。**

- 冻结含义：平台规则文档和代码的内容/接口/行为已定型，不再扩展新功能
- 版控状态：平台基线已提交（`9cb34a9`），P1/P1.5 已提交（`aca3d07`），`from_legacy_env` 已标注 deprecated（`389c287`），P3 MongoDB 模型 datetime 迁移已提交（`4e22c91`），P3.5 全仓 datetime 迁移已提交（`4aaea9b`），维护债文档已同步
- 真实项目样例只用于验证平台能力，不定义平台规则
- 新增平台功能需走 PR 评审，确保不引入项目专属逻辑
- 维护债按本文件排期独立处理，不回流平台主线
- 真实项目验证持续进行，但验证结论回收为平台基线时才修改平台代码
