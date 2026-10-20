# Week 3 P0 基座收口文档

> 日期：2026-09-06
> 目标：统一 canonical path 为 `app.test_platform`，`app.platform` 保留为兼容层

---

## 1. 执行摘要

| 任务 | 状态 |
|------|------|
| 统一 test_platform 内部导入路径 | 完成 |
| 创建 platform 兼容层（re-export） | 完成 |
| 验证双路径导入 | 通过 |
| P0 测试回归（95 项） | 全部通过 |
| P1/P2 测试回归（57 项） | 全部通过 |

---

## 2. 变更清单

### 2.1 test_platform 内部导入修正

将 `app.platform.*` 改为 `app.test_platform.*`（消除循环依赖源头）：

| 文件 | 变更 |
|------|------|
| `app/test_platform/orchestrator.py` | `app.platform.execution_gates` / `report_templates` → `app.test_platform.*` |
| `app/test_platform/quality_report.py` | `app.platform.testcase_contract` → `app.test_platform.testcase_contract` |
| `app/test_platform/versioning.py` | `app.platform.directory_layout` → `app.test_platform.directory_layout` |
| `app/test_platform/evidence_chain.py` | `app.platform.directory_layout` → `app.test_platform.directory_layout` |

### 2.2 platform 兼容层创建

`app/platform/` 下所有子模块改为 re-export：

| 模块 | 转发目标 |
|------|---------|
| `__init__.py` | 聚合导出全部 P0-P2 符号 |
| `testcase_contract.py` | `app.test_platform.testcase_contract` |
| `directory_layout.py` | `app.test_platform.directory_layout`（含 `_safe_name`、`_now_str`） |
| `report_templates.py` | `app.test_platform.report_templates` |
| `execution_gates.py` | `app.test_platform.execution_gates` |
| `versioning.py` | `app.test_platform.versioning` |
| `quality_report.py` | `app.test_platform.quality_report` |
| `evidence_chain.py` | `app.test_platform.evidence_chain` |
| `orchestrator.py` | `app.test_platform.orchestrator` |
| `generation_prompts.py` | 保持不变（无外部导入依赖） |

---

## 3. 导入规范（生效）

### 3.1 新代码必须遵循

```python
# 正确：直接从 canonical path 导入
from app.test_platform.testcase_contract import TestCaseContract
from app.test_platform.execution_gates import ExecutionGate

# 错误：禁止新代码继续使用旧路径
from app.platform.testcase_contract import TestCaseContract  # 不推荐
```

### 3.2 旧代码兼容

现有 `from app.platform.*` 调用继续工作，无需立即修改。兼容层将在后续版本中评估废弃。

---

## 4. 验证结果

### 4.1 双路径导入验证

```python
# app.test_platform.* 可独立导入
from app.test_platform.orchestrator import DualTrackRunner  # OK

# app.platform.* 兼容层仍可用
from app.platform.orchestrator import DualTrackRunner  # OK

# 对象一致性
assert app.platform.TestCaseContract is app.test_platform.TestCaseContract  # True
```

### 4.2 测试回归

| 测试文件 | 通过数 | 失败数 |
|---------|--------|--------|
| `test_p0_contract.py` | 23 | 0 |
| `test_p0_gates.py` | 32 | 0 |
| `test_p0_report.py` | 22 | 0 |
| `test_p0_directory.py` | 18 | 0 |
| `test_p1_quality_report.py` | 14 | 0 |
| `test_p1_versioning.py` | 14 | 0 |
| `test_p2_orchestrator.py` | 15 | 0 |
| `test_p2_evidence.py` | 14 | 0 |
| **合计** | **152** | **0** |

---

## 5. 架构图

```
┌─────────────────────────────────────────┐
│           消费者（业务代码/测试）           │
│  from app.test_platform import ...      │  ← 推荐
│  from app.platform import ...           │  ← 兼容（继续工作）
└─────────────────────────────────────────┘
                   │
    ┌──────────────┴──────────────┐
    ▼                             ▼
┌──────────────┐         ┌──────────────┐
│ app.platform │         │app.test_platform│
│  兼容层       │◄────────│   主实现层      │
│  (re-export) │         │  (全部实现)     │
└──────────────┘         └──────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
        testcase_contract  execution_gates  report_templates
        directory_layout   versioning       quality_report
        evidence_chain     orchestrator     generation_prompts
```

---

## 6. 后续建议

1. **新代码**：一律从 `app.test_platform` 导入
2. **旧代码迁移**：非紧急，可在后续重构中逐步替换
3. **兼容层废弃**：待所有外部调用迁移完成后，评估移除时机
4. **文档更新**：技能文档和 README 中统一使用 `app.test_platform` 示例

---

## 7. 结论

Week 3 P0 基座收口完成。`app.test_platform` 已确立为唯一主实现层，`app.platform` 保留为兼容层确保零中断迁移。全部 152 项平台测试通过，无循环导入问题。
