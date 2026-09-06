# P0-A 试点：PR-2 只读接入记录

> 低风险只读接入试点，验证 P0-B 数据治理规范在实际项目中的有效性。
> 范围：只读查询（余额、流水、会员信息），不执行充值、退款、批量导入等写入操作。

---

## A1 接入登记

| 字段 | 内容 |
|---|---|
| 项目编号 | PR-2 |
| 项目名称 | 储值充值 |
| 环境 | UAT |
| 接入时间 | 2026-09-06 |
| 负责人 | admin |
| 需求文档 | PR-2 储值充值交付文档 |
| 测试范围 | 会员查询、余额查询、流水查询（只读） |
| 排除范围 | 充值、退款、批量导入（写入操作，需审批后执行） |
| 数据保留期限 | 项目结束后 7 天内清理 |

**状态**：登记内容已填写，负责人 admin。

---

## A2 需求与测试范围映射

| 需求模块 | 测试用例契约 | 覆盖维度 | 风险等级 |
|---|---|---|---|
| 会员信息查询 | `TestCaseContract`（查询类） | 会员存在/不存在、字段完整性 | L1 |
| 账户余额查询 | `TestCaseContract`（查询类） | 余额精度、空账户、多账户 | L1 |
| 充值流水查询 | `TestCaseContract`（查询类） | 时间范围、分页、排序 | L1 |
| 退款流水查询 | `TestCaseContract`（查询类） | 时间范围、分页、排序 | L1 |
| 批量导入记录查询 | `TestCaseContract`（查询类） | 导入批次、状态过滤 | L1 |

**排除的写入场景**（需审批后执行）：
- 账户充值（L2）
- 账户退款（L3）
- 批量导入会员（L2）

---

## A3 只读测试数据登记

### 3.1 查询账号

| 字段 | 内容 |
|---|---|
| 数据对象 | 会员账户信息 |
| 数据标识 | 手机号：`PR2_STORAGE_ACCOUNT_NO`（环境变量） |
| 操作类型 | 查询（`/api/icdp/v1/memberCard/account/page`） |
| 执行风险等级 | L1（UAT 只读数据） |
| 数据敏感等级 | PII（手机号） |
| 是否需要审批 | 否 |
| 预期影响 | 返回会员余额、积分等，无数据变更 |
| 回滚方式 | 无需回滚 |
| 证据位置 | `workspace/evidence/pr2-pilot-query/{timestamp}/` |
| 责任人 | QA |

### 3.2 查询参数（POST JSON）

| 字段 | 值 | 说明 |
|---|---|---|
| mobile | `PR2_STORAGE_ACCOUNT_NO` | 环境变量，脱敏后示例：`183****0265` |
| pageNum | 1 | 页码 |
| pageSize | 10 | 每页条数 |

**请求体示例**：
```json
{
  "pageNum": 1,
  "pageSize": 10,
  "mobile": "183****0265"
}
```

**注意**：真实执行时 `mobile` 从环境变量读取，不硬编码。

### 3.3 预期响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| memberId | string | 会员 ID（脱敏后归档） |
| accountNo | string | 手机号（脱敏后归档） |
| balance | decimal | 余额 |
| points | decimal | 积分 |
| memberLevel | string | 会员等级 |
| status | string | 账户状态 |

---

## A4 质量门禁（只读场景）

| 检查项 | 状态 | 说明 |
|---|---|---|
| 环境变量已配置 | ☑ | `PR2_STORAGE_ACCOUNT_NO` 等已配置 |
| 凭据未硬编码 | ☑ | 脚本从环境变量读取，无硬编码 |
| 只读操作确认 | ☑ | POST 查询，无数据变更 |
| 响应脱敏检查 | ☑ | 手机号、ID 已脱敏归档 |

---

## A5 执行记录（只读查询）

**执行时间**：2026-09-06 15:40

### 5.1 执行前检查

| 检查项 | 状态 |
|---|---|
| LIVE_API 开关状态 | ☑ `LIVE_API=1` |
| 环境变量已加载 | ☑ `tests/conftest.py` 加载 `backend/.env` |
| 查询参数已确认 | ☑ `mobile=183****0265`（脱敏） |

### 5.2 执行步骤（使用现有 PR-2 API 客户端）

**接口信息**：
- 方法：`POST`
- 路径：`/yundt-saas-gateway/api/icdp/v1/memberCard/account/page`
- 请求体：JSON（`{"pageNum": 1, "pageSize": 10, "mobile": "..."}`）
- 鉴权：`access-token` + `Cookie` + `referer`
- 客户端：`DirectHttpApiClient`（已知 token + Cookie）

**执行命令**：
```bash
cd backend
python -m pytest tests/api/pr2_storage_recharge/test_p1_query.py -v
```

**执行结果**（2026-09-06 15:40）：
```
tests\api\pr2_storage_recharge\test_p1_query.py::test_p1_account_query_by_mobile PASSED [100%]
============================== 1 passed in 1.27s ==============================
```

**补充验证**（统一导入测试包）：
```
tests\api\api_doc_import\ 55 passed, 1 warning
```
- 唯一 warning：Pydantic schema 字段命名冲突（与数据库连接无关）
- 无 event loop 清理异常

**验证点**：
- ☑ 按手机号查询命中记录
- ☑ 余额为数值（≥ 0）
- ☑ 有会员卡号
- ☑ 响应状态 200 OK

### 5.3 执行后检查

| 检查项 | 状态 |
|---|---|
| 响应已保存 | ☑ |
| 响应已脱敏（手机号、ID） | ☑ |
| 证据已归档 | ☑ |
| 无数据变更确认 | ☑（只读查询，无余额/流水变更） |

---

## 证据包结构

```
workspace/evidence/pr2-pilot-query/{timestamp}/
├── README.md              # 证据包说明
├── request/               # 请求参数
│   └── query_request.json
├── response/              # 响应结果（已脱敏）
│   └── query_response.json
└── audit/                 # 接入记录
    └── a1_a5_record.md    # 本文件副本
```

---

## 试点结论

| 检查项 | 结论 |
|---|---|
| A1 接入登记 | 登记内容已填写，负责人 admin |
| A2 需求映射 | 登记内容已填写（只读范围已限定） |
| A3 数据登记 | 登记内容已填写（只读数据已登记） |
| A4 质量门禁 | ☑ 通过（环境变量、凭据、只读、脱敏均已确认） |
| A5 只读执行 | ☑ 已完成（2026-09-06 15:40，1 passed in 1.27s） |
| 治理规范有效性 | ☑ 验证通过（P0-B 规范在实际执行中有效） |

**执行摘要**：
- 项目：PR-2 储值充值
- 环境：UAT
- 操作：会员账户只读查询（L1）
- 结果：PASSED
- 数据变更：无（只读查询）
- 证据：已脱敏归档

**下一步**：
1. 按 B3 审批流程申请写入场景（充值、退款等）
2. 或继续 PR-9 只读接入试点
3. 或进入 P1-C 接入规范固化

---

## 与 P0-B 规范的对应关系

| P0-B 规范 | 本试点应用 |
|---|---|
| B1 数据分级 | L1 只读查询，PII 数据已识别 |
| B2 凭据管理 | 环境变量 `PR2_STORAGE_ACCOUNT_NO`、`PR2_ACCESS_TOKEN` |
| B3 执行审批 | L1 无需审批，已记录 |
| B4 回滚机制 | L1 无需回滚，已记录 |
| B5 证据脱敏 | 响应手机号、ID 需脱敏后归档 |
| B6 数据清理 | 只读无残留，证据包按保留期限处理 |
