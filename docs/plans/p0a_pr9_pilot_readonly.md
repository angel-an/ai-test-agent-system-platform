# P0-A 试点：PR-9 只读接入记录

> 低风险只读接入试点，验证 P0-B 数据治理规范在第二项目中的通用性。
> 范围：只读查询（商品列表、预热状态、交易记录），不执行积分兑换等写入操作。

---

## A1 接入登记

| 字段 | 内容 |
|---|---|
| 项目编号 | PR-9 |
| 项目名称 | 积分商城 |
| 环境 | UAT |
| 接入时间 | 2026-09-06 |
| 负责人 | admin |
| 需求文档 | PR-9 积分商城交付文档 |
| 测试范围 | 商品列表查询、预热状态查询、交易记录查询（只读） |
| 排除范围 | 积分兑换、CRM 商品新增、CRM 装修配置（写入操作，需审批后执行） |
| 数据保留期限 | 项目结束后 7 天内清理 |

**状态**：登记内容已填写，负责人 admin。

---

## A2 需求与测试范围映射

| 需求模块 | 测试用例契约 | 覆盖维度 | 风险等级 |
|---|---|---|---|
| 商品列表查询 | `TestCaseContract`（查询类） | 等级可见、活动周期、预热提前、CMS 排序 | L1 |
| 预热状态查询 | `TestCaseContract`（查询类） | 倒计时文案、按钮状态（置灰/可点） | L1 |
| 交易记录查询 | `TestCaseContract`（查询类） | 兑换列表、订单详情、积分流水 | L1 |

**排除的写入场景**（需审批后执行）：
- 积分兑换（L2）
- CRM 商品新增（L2）
- CRM 装修配置修改（L2）

---

## A3 只读测试数据登记

### 3.1 查询账号

| 字段 | 内容 |
|---|---|
| 数据对象 | 会员账户信息 |
| 数据标识 | 会员 ID：`PR9_MEMBER_ID`（环境变量） |
| 操作类型 | 查询（商品列表、预热状态、交易记录） |
| 执行风险等级 | L1（UAT 只读数据） |
| 数据敏感等级 | PII（会员 ID） |
| 是否需要审批 | 否 |
| 预期影响 | 返回商品列表/状态/交易记录，无数据变更 |
| 回滚方式 | 无需回滚 |
| 证据位置 | `workspace/evidence/pr9-pilot-query/{timestamp}/` |
| 责任人 | admin |

### 3.2 查询参数

**商品列表查询**：
- 等级可见过滤：当前会员等级
- 活动周期：当前时间在商品开始~结束范围内
- 预热提前 N 天：当前时间在（开始时间 - N 天）范围内也显示
- CMS 排序：按配置排序值展示

**预热状态查询**：
- 商品 ID（公开数据）
- 校验列表页和详情页的倒计时文案、按钮状态

**交易记录查询**：
- 会员 ID：`PR9_MEMBER_ID`
- 按时间倒序展示

### 3.3 预期响应字段

**商品列表**：
| 字段 | 类型 | 说明 |
|---|---|---|
| productId | string | 商品 ID |
| productName | string | 商品名称 |
| points | int | 所需积分 |
| stock | int | 库存 |
| startTime | datetime | 活动开始时间 |
| endTime | datetime | 活动结束时间 |
| status | string | 状态（预热/活动中/已结束） |

**预热状态**：
| 字段 | 类型 | 说明 |
|---|---|---|
| countdownText | string | 倒计时文案 |
| buttonEnabled | bool | 按钮是否可点 |
| buttonText | string | 按钮文案 |

**交易记录**：
| 字段 | 类型 | 说明 |
|---|---|---|
| orderId | string | 订单号（脱敏后归档） |
| productName | string | 商品名称 |
| points | int | 积分值 |
| direction | string | 方向（正/负） |
| createTime | datetime | 创建时间 |

---

## A4 质量门禁（只读场景）

| 检查项 | 状态 | 说明 |
|---|---|---|
| 环境变量已配置 | ☐ | `PR9_MEMBER_ID` 等 |
| 凭据未硬编码 | ☐ | 确认脚本无硬编码 |
| 只读操作确认 | ☐ | 确认无积分扣减、订单创建等写入 |
| 响应脱敏检查 | ☐ | 会员 ID、订单号已脱敏 |

---

## A5 执行记录（只读查询）

**执行时间**：（待执行）

### 5.1 执行前检查

| 检查项 | 状态 |
|---|---|
| LIVE_API 开关状态 | ☐ `LIVE_API=1` |
| 环境变量已加载 | ☐ `tests/conftest.py` 加载 `backend/.env` |
| 查询参数已确认 | ☐ |

### 5.2 执行步骤

**接口信息**：
- 商品列表：GET `/api/.../products`（具体路径需确认）
- 预热状态：GET `/api/.../products/{id}/preview`（具体路径需确认）
- 交易记录：GET `/api/.../member/{id}/transactions`（具体路径需确认）
- 鉴权：`PR9_MEMBER_TOKEN` + Cookie

**执行命令**（待配置环境后执行）：
```bash
cd backend
python -m pytest tests/api/pr9_points_mall/test_p1_query.py -v
```

### 5.3 执行后检查

| 检查项 | 状态 |
|---|---|
| 响应已保存 | ☐ |
| 响应已脱敏（会员 ID、订单号） | ☐ |
| 证据已归档 | ☐ |
| 无数据变更确认 | ☐（只读查询，无积分/订单变更） |

---

## 证据包结构

```
workspace/evidence/pr9-pilot-query/{timestamp}/
├── README.md              # 证据包说明
├── request/               # 请求参数
│   ├── product_list_request.json
│   ├── preview_status_request.json
│   └── transaction_record_request.json
├── response/              # 响应结果（已脱敏）
│   ├── product_list_response.json
│   ├── preview_status_response.json
│   └── transaction_record_response.json
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
| A4 质量门禁 | 待执行后确认 |
| A5 只读执行 | 待执行（需环境变量配置后执行） |
| 治理规范有效性 | 待验证 |

**下一步**：
1. 配置 PR-9 环境变量后执行只读查询
2. 验证响应脱敏、证据归档流程
3. 对比 PR-2/PR-9 差异，进入 P1-C 接入规范固化

---

## 与 P0-B 规范的对应关系

| P0-B 规范 | 本试点应用 |
|---|---|
| B1 数据分级 | L1 只读查询，PII 数据已识别 |
| B2 凭据管理 | 环境变量 `PR9_MEMBER_ID`、`PR9_MEMBER_TOKEN` |
| B3 执行审批 | L1 无需审批，已记录 |
| B4 回滚机制 | L1 无需回滚，已记录 |
| B5 证据脱敏 | 响应会员 ID、订单号需脱敏后归档 |
| B6 数据清理 | 只读无残留，证据包按保留期限处理 |

---

## 与 PR-2 试点的对比

| 对比项 | PR-2（储值充值） | PR-9（积分商城） |
|---|---|---|
| 业务场景 | 余额、流水查询 | 商品、状态、交易查询 |
| 数据对象 | 手机号（PII） | 会员 ID（PII） |
| 接口方法 | POST | GET（待确认） |
| 鉴权方式 | access-token + Cookie | member-token + Cookie |
| 风险等级 | L1 | L1 |
| 治理规范适用性 | ✅ | 待验证 |
