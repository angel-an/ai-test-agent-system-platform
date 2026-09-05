# 平台维护债跟踪

> 平台基线已冻结（2026-09-05），本文件跟踪后续维护项，按模块分流处理。
> 原则：维护债不阻塞平台基线，排期独立处理。

---

## 1. Legacy 兼容生命周期

### 1.1 当前状态

| 兼容项 | 位置 | 说明 | 清退条件 |
|---|---|---|---|
| `from_legacy_env(prefix="PR2")` | `execution_gates.py:51` | 兼容 PR2_* 环境变量命名 | 存量项目全部迁移至标准命名（LIVE_API / RUN_RISKY_NEG） |
| `test_from_legacy_env` | `test_p0_gates.py:62` | 验证兼容方法本身 | 随 `from_legacy_env` 一并清退 |

### 1.2 决策记录

- **不删除**：当前仍有存量项目使用 PR2_* 前缀，删除会破坏向后兼容
- **不扩展**：严禁为新项目增加新前缀兼容
- **清退触发**：确认无存量依赖后，一次性删除 `from_legacy_env` 及对应测试

---

## 2. Pydantic Deprecation（业务代码）

### 2.1 影响范围（第一批：class Config → ConfigDict）

**状态：工作区迁移完成，待提交（2026-09-05）**

`class Config` 方式在 Pydantic V2 已废弃，需迁移至 `ConfigDict`。基线中实际存在 **14 处 `class Config`**，涉及 **9 个文件**，本轮已全部清除：

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
| `app/models/mongodb/api_test_log.py` | 53 | `ApiTestLog` | ✅ |

**注**：`app/schemas/pagination.py` 等文件原本已使用 `ConfigDict`，不在本轮迁移范围内。

### 2.2 影响范围（第二批：json_encoders → field_serializer）

**状态：⏸️ 暂缓（P1.5 不阻塞提交）**

Pydantic V2 中 `ConfigDict(json_encoders=...)` 本身也被标记为 deprecated，但**在 V2 中仍可用**，V3 才移除。当前涉及 4 个 MongoDB 模型文件：

| 文件 | 说明 |
|---|---|
| `app/models/mongodb/version_history.py` | `json_encoders={UUID: str, datetime: ...}` |
| `app/models/mongodb/audit_log.py` | `json_encoders={UUID: str, datetime: ...}` |
| `app/models/mongodb/attachment.py` | `json_encoders={UUID: str, datetime: ...}` |
| `app/models/mongodb/api_test_log.py` | `json_encoders={UUID: str, datetime: ...}` |

**暂缓理由**：
1. `json_encoders` 在 V2 中功能正常，仅被标记 deprecated
2. 迁移到 `field_serializer` 需逐字段处理，工作量 1-2 天
3. 序列化回归测试已覆盖（`tests/mongodb/test_serialization_regression.py`），行为变更可及时发现
4. 等 Pydantic V3 发布计划明确后再排期

**当前警告数（可复现基线：平台 215 + 序列化回归 10）**：`225 passed, 33 warnings`
- 8 个 PytestCollectionWarning（平台层 Enum 类名与 pytest 测试类命名冲突）
- 1 个 PydanticDeprecatedSince20 `json_encoders`（由 4 个模型文件共同触发）
- 多个 DeprecationWarning `datetime.utcnow()`（来自 MongoDB 模型默认值）
- 其余为 LangChain / sqlite3 等第三方库警告

**分层测试口径**：
- 平台层：`tests/platform/` — 215 passed（基线，零环境依赖）
- 序列化回归：`tests/mongodb/test_serialization_regression.py` — 10 passed（新增，零环境依赖）
- 可复现基线合计：225 passed（提交前必跑）
- 扩展基线：`tests/` 排除 e2e/api/services — 516 passed（需 SQLite，提交前建议跑）
- 完整基线：585 collected（含环境依赖测试，环境就绪后跑）
- 现有业务测试：待确认可复现集合（当前 KB E2E 环境存在错误，需隔离或修复后重跑）

### 2.3 迁移方案（第一批）

```python
# 旧写法（废弃）
class MyModel(BaseModel):
    class Config:
        populate_by_name = True

# 新写法（推荐）
class MyModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
```

### 2.4 迁移方案（第二批）

```python
# 当前写法（已迁移到 ConfigDict，但 json_encoders 仍被标记 deprecated）
class MyModel(BaseModel):
    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})

# 目标写法（Pydantic V2 推荐）
from pydantic import field_serializer

class MyModel(BaseModel):
    created_at: datetime

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()
```

**注意**：第二批迁移涉及序列化行为变更，需确认 MongoDB 存取逻辑不受影响后再实施。

### 2.5 序列化回归测试

**状态：✅ 已补（2026-09-05）**

为验证 ConfigDict 迁移后 `json_encoders` 行为不变，新增回归测试：

| 测试文件 | 覆盖模型 | 验证点 |
|---|---|---|
| `tests/mongodb/test_serialization_regression.py` | `TestCaseVersionHistory` | UUID → str, datetime → isoformat |
| | `AuditLog` | UUID → str, datetime → isoformat, Enum → str |
| | `TestCaseAttachment` | UUID → str, datetime → isoformat |
| | `APITestDetailLog` | UUID → str, datetime → isoformat, Optional None 处理 |

**测试结果**：6 passed，序列化行为与迁移前一致。

### 2.6 排期建议

- **第一批（class Config → ConfigDict）**：工作区迁移完成，序列化回归测试通过，待提交
- **第二批（json_encoders → field_serializer）**：⏸️ 暂缓至 Pydantic V3 前完成，需先验证 MongoDB 序列化行为（回归测试已补）

按模块分批迁移，每批验证后合并：

1. ~~**第一批**：`app/schemas/`（5 个文件）+ `app/models/mongodb/`（4 个文件）— `class Config` → `ConfigDict`~~ 工作区完成，待提交
2. **第二批**：`app/models/mongodb/`（4 个文件）— `json_encoders` → `field_serializer`，⏸️ 暂缓至 V3 前

**注意**：`json_encoders` 在 Pydantic V2 也有替代方案（`field_serializer` / `PlainSerializer`），迁移时可一并评估。

---

## 3. Pytest Collection Warning（平台层）

### 3.1 影响范围

平台层 Enum 类名与 pytest 测试类命名规则冲突：

| 文件 | 类名 | 警告内容 |
|---|---|---|
| `app/platform/testcase_contract.py` | `TestCaseType` | cannot collect test class because it has __init__ |
| `app/platform/report_templates.py` | `TestResult` | cannot collect test class because it has __init__ |
| `app/platform/report_templates.py` | `TestResultCode` | cannot collect test class because it has __init__ |

### 3.2 处理方案

**选项 A：保持现状**（推荐）
- 这些类名是领域模型的一部分，改名会破坏 API 一致性
- 警告无害，只是 pytest 不会把它们当测试类收集
- 已在 `pytest.ini` 中通过 `filterwarnings` 控制

**选项 B：重命名**（如后续要完全消除警告）
- `TestCaseType` → `CaseType`
- `TestResult` → `ExecutionResult`
- `TestResultCode` → `ResultCode`
- 需同步修改所有引用处，影响面较大

**决策**：当前保持选项 A，如后续要完全消除警告再评估选项 B。

---

## 4. 测试样例字符串统一（洁净度优化）

### 4.1 当前状态

平台测试中使用的中性示例字符串已基本统一：
- 项目代号：`PROJ-1`、`DEMO-1`
- 测试数据：`demo_user_001`、`13900139000`
- 模块名：`认证`、`登录`、`支付`、`查询`、`写入`

### 4.2 可进一步优化项

| 位置 | 当前 | 建议 |
|---|---|---|
| `test_p0_contract.py` | `号码校验通过` | `格式校验通过`（更通用） |
| `test_p1_quality_report.py` | `登录`、`错误密码` | 已中性，无需改动 |
| `test_p2_orchestrator.py` | `查询`、`写入` | 已中性，无需改动 |

**优先级**：低。当前字符串已基本中性，不绑定具体业务，属于「洁净度优化」而非「功能阻断项」。

---

## 5. 维护项优先级

| 优先级 | 事项 | 预估工作量 | 状态 |
|---|---|---|---|
| P1 | Pydantic `class Config` → `ConfigDict` 迁移 | 工作区完成 | ✅ 待提交 |
| P1.5 | Pydantic `json_encoders` → `field_serializer` 迁移 | 1-2 天 | ⏸️ 暂缓（V2 仍可用，V3 前完成） |
| P2 | Legacy 兼容清退（确认无存量后） | 0.5 天 | 待确认 |
| P3 | Pytest 警告消除（选项 B） | 1 天 | 保持现状 |
| P4 | 测试样例字符串再统一 | 0.5 天 | 低优 |

---

## 6. 冻结声明

**平台通用规则（`backend/app/platform/` + `backend/tests/platform/`）自 2026-09-05 起内容口径冻结。**

- 冻结含义：平台规则文档和代码的内容/接口/行为已定型，不再扩展新功能
- 版控状态：当前平台代码、测试和维护债文档为工作区修改或未跟踪状态，**尚未形成可由 Git 直接还原的提交基线**
- 真实项目样例只用于验证平台能力，不定义平台规则
- 新增平台功能需走 PR 评审，确保不引入项目专属逻辑
- 维护债按本文件排期独立处理，不回流平台主线
- 真实项目验证持续进行，但验证结论回收为平台基线时才修改平台代码
- **提交前需完成**：
  1. P1 迁移代码提交
  2. P1.5 序列化回归测试（含 `model_dump(mode="json")` 与 `to_document()`）
  3. 全量测试回归通过（平台 215 项 + 序列化回归 6 项 + 现有业务测试集）
  4. 确认无 KB E2E 环境错误阻塞
