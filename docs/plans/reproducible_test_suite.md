# 可复现全量测试集合定义

> 目的：明确哪些测试在当前工作区可稳定复现，哪些因环境依赖需隔离。
> 更新日期：2026-09-05

---

## 1. 测试分层

| 层级 | 路径 | 测试数 | 环境依赖 | 状态 |
|---|---|---|---|---|
| L1 平台层 | `tests/platform/` | 215 | 无 | ✅ 稳定通过 |
| L2 序列化回归 | `tests/mongodb/test_serialization_regression.py` | 10 | 无 | ✅ 稳定通过 |
| L3 业务单元 | `tests/`（除 e2e/api/services） | ~290 | 部分需 SQLite | ✅ 516 passed, 1 skipped |
| L4 服务层 | `tests/services/` | ~30 | 需外部服务/DB | ⏸️ 环境依赖，隔离 |
| L5 API 集成 | `tests/api/` | ~20 | 需 live API | ⏸️ 环境依赖，隔离 |
| L6 E2E | `tests/e2e/` | ~20 | 需完整环境 | ⏸️ 环境依赖，隔离 |

**当前可复现集合**：L1 + L2 + L3 = **516 + 10 = 526 项**（不含 skipped）

---

## 2. 环境依赖说明

### 2.1 无需环境的测试（L1 + L2）

```bash
cd backend
python -m pytest tests/platform/ tests/mongodb/test_serialization_regression.py -v
```

- 纯内存计算，无外部依赖
- 结果：225 passed（215 + 10）

### 2.2 轻环境测试（L3）

```bash
cd backend
python -m pytest tests/ --ignore=tests/e2e --ignore=tests/api --ignore=tests/services -v
```

- 依赖：SQLite（内存/本地文件）
- 结果：516 passed, 1 skipped

### 2.3 需隔离的测试（L4 + L5 + L6）

| 路径 | 隔离原因 | 触发条件 |
|---|---|---|
| `tests/e2e/` | 需完整 KB/DB/Web 环境 | 环境未就绪时 skip/error |
| `tests/api/` | 需 live API 或 mock 服务 | PR2_LIVE_API 等变量 |
| `tests/services/` | 需外部 IDP/缺陷系统 | 网络/认证依赖 |

---

## 3. 回归策略

### 3.1 平台代码变更回归

任何 `backend/app/platform/` 或 `backend/tests/platform/` 变更后：

```bash
# 必跑
python -m pytest tests/platform/ -v

# 建议补充
python -m pytest tests/mongodb/test_serialization_regression.py -v
```

### 3.2 Pydantic 迁移回归

`schemas/` 或 `models/mongodb/` 变更后：

```bash
# 必跑：序列化回归
python -m pytest tests/mongodb/test_serialization_regression.py -v

# 建议补充：轻环境业务测试
python -m pytest tests/ --ignore=tests/e2e --ignore=tests/api --ignore=tests/services -v
```

### 3.3 全量回归（提交前）

```bash
# 当前可复现的最大集合
python -m pytest tests/ --ignore=tests/e2e --ignore=tests/api --ignore=tests/services -v
```

---

## 4. 与原始 561 项的对比

用户此前提及「561 项现有测试」，当前收集到 585 项。差异来源：

| 因素 | 说明 |
|---|---|
| 新增平台测试 | +212（原 ~446，现 platform/ 为 215） |
| 新增序列化回归 | +10 |
| 测试文件增删 | 其他模块可能有新增或移除 |

**口径统一**：后续以「收集到的实际数量」为准，不再引用历史估算值。

---

## 5. 结论

- **可复现基线**：225 项（平台 215 + 序列化回归 10）— 零环境依赖
- **扩展基线**：516 项 — 需 SQLite，无外部服务
- **完整基线**：585 项 — 需全部环境就绪
- **提交前标准**：至少通过可复现基线，建议通过扩展基线
