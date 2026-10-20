# IDP 接口契约文档

> 本文档基于 2026-08-26 真实联调结果整理
> 所有敏感信息已脱敏
> **2026-10-15 更新**：新增第 9 章「服务端能力边界与已知限制」——均为项目 17 真实实测结论，
> 是**提单能力的硬约束**（设计前必读，不要"接口存在即认为有权限"）。

---

## 1. 认证

### 1.1 环境变量注入 Token（MVP 阶段推荐方式）

```bash
export IDP_TOKEN="your-token-here"
export IDP_BASE_URL="https://idp-api.example.com"
export IDP_ORGANIZATION_ID="1"
```

### 1.2 Token 使用方式

所有 API 请求需要在 Header 中携带：

```
Authorization: Bearer {token}
Content-Type: application/json
```

### 1.3 Token 状态检查

```python
from app.services.idp_auth_service import IDPAuthService

# 获取 Token 状态（不包含 Token 本身）
status = IDPAuthService.get_token_status()
# 返回: {"configured": true, "source": "env", "expires_at": "...", "expired": false}
```

---

## 2. 项目查询接口

### 2.1 获取项目信息

**请求**：

```http
GET /agile/v1/projects/{projectId}/project_info
Authorization: Bearer {token}
```

**响应示例**：

```json
{
  "infoId": 1101,
  "projectId": 1169,
  "projectCode": "jf-20260122392-01",
  "objectVersionNumber": 1,
  "defaultAssigneeId": null,
  "defaultAssigneeType": null,
  "defaultPriorityCode": null,
  "creationDate": "2026-01-29 13:41:37"
}
```

---

## 3. 缺陷创建接口

### 3.1 创建缺陷

**请求**：

```http
POST /agile/v1/projects/{projectId}/issues?applyType=agile
Authorization: Bearer {token}
Content-Type: application/json
```

**请求体示例**：

```json
{
  "summary": "【自动化测试】【API】【联调验证】IDP缺陷自动登记验证",
  "description": "{\"ops\":[{\"insert\":\"问题概述\\n\"},{\"insert\":\"联调验证测试\\n\\n\"},{\"insert\":\"reqid\\n\"},{\"insert\":\"test-req-id-12345\\n\"}]}",
  "typeCode": "bug",
  "issueTypeId": 3,
  "priorityCode": "priority-2",
  "priorityId": 2,
  "sprintId": 4167,
  "epicId": 859111,
  "assigneeId": 6557
}
```

### 3.2 字段说明

#### 必填字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| summary | string | 缺陷标题 | "【自动化测试】【API】【会员卡】分页参数返回500" |
| typeCode | string | 类型代码 | "bug" |
| issueTypeId | integer | 问题类型 ID | 3 (缺陷) |
| priorityCode | string | 优先级代码 | "priority-2" |
| priorityId | integer | 优先级 ID | 2 |

#### 可选字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| description | string | 描述 (Delta JSON) | "{\"ops\":[...]}" |
| sprintId | integer | 冲刺 ID | 4167 |
| epicId | integer | 史诗 ID | 859111 |
| assigneeId | integer | 指派人 ID | 6557 |

### 3.3 优先级映射

| priorityId | priorityCode | 名称 |
|-----------|-------------|------|
| 1 | priority-1 | 高 |
| 2 | priority-2 | 中 |
| 3 | priority-3 | 低 |

### 3.4 成功响应

```json
{
  "issueId": 912536,
  "issueNum": "jf-20260122392-01-2513",
  "typeCode": "bug",
  "statusId": 2,
  "summary": "【自动化测试】【API】【联调验证】IDP缺陷自动登记验证",
  "reporterId": 6557,
  "assigneeId": 6557,
  "projectId": 1169,
  "epicId": 859111,
  "priorityId": 2,
  "issueTypeId": 3,
  "priorityDTO": {
    "id": 2,
    "name": "中",
    "colour": "#3575DF"
  },
  "issueTypeDTO": {
    "id": 3,
    "name": "缺陷",
    "typeCode": "bug"
  },
  "statusMapDTO": {
    "id": 2,
    "name": "待处理",
    "code": "create"
  },
  "activeSprint": {
    "sprintId": 4167,
    "sprintName": "SIT需求测试"
  }
}
```

### 3.5 错误响应

#### 字段缺失

```json
{
  "failed": true,
  "code": "error.IssueRule.typeCode",
  "message": "typeCode不能为空"
}
```

```json
{
  "failed": true,
  "code": "error.IssueRule.PriorityCode",
  "message": "priorityCode不能为空"
}
```

```json
{
  "failed": true,
  "code": "error.issueTypeId.isNull",
  "message": "error.issueTypeId.isNull"
}
```

#### 权限错误

```xml
<oauth>
  <status>PERMISSION_MISMATCH</status>
  <code>error.permission.mismatch</code>
  <message>This request mismatch any permission</message>
</oauth>
```

---

## 4. 缺陷查询接口

### 4.1 获取缺陷详情

**请求**：

```http
GET /agile/v1/projects/{projectId}/issues/{issueId}?organizationId={organizationId}
Authorization: Bearer {token}
```

**响应**：与创建缺陷的成功响应格式相同

---

## 5. 项目字段配置

### 5.1 YAML 配置示例

```yaml
projects:
  - source_project_key: "PR-2"
    source_project_name: "小杨生煎"
    idp_project_id: 1169
    idp_project_name: "小杨生煎"
    apply_type: "agile"
    bug_type_id: 12536
    issue_type_id: 3
    type_code: "bug"
    default_priority_id: 2
    default_priority_code: "priority-2"
    default_sprint_id: 4167
    default_epic_id: 859111
    default_assignee_id: 6557
    enabled: true
```

### 5.2 配置字段说明

| 字段 | 说明 | 必填 |
|------|------|------|
| source_project_key | 本地项目标识符 | 是 |
| source_project_name | 本地项目名称 | 否 |
| idp_project_id | IDP 项目 ID | 是 |
| idp_project_name | IDP 项目名称 | 否 |
| apply_type | 应用类型 | 否 (默认 agile) |
| issue_type_id | 问题类型 ID | 是 |
| type_code | 类型代码 | 是 |
| default_priority_id | 默认优先级 ID | 否 (默认 2) |
| default_priority_code | 默认优先级代码 | 否 (默认 priority-2) |
| default_sprint_id | 默认冲刺 ID | 否 |
| default_epic_id | 默认史诗 ID | 否 |
| default_assignee_id | 默认指派人 ID | 否 |
| enabled | 是否启用 | 否 (默认 true) |

---

## 6. Mock 测试数据

### 6.1 创建缺陷请求（Mock）

```json
{
  "summary": "【自动化测试】【API】【Mock测试】冒烟验证",
  "description": "{\"ops\":[{\"insert\":\"Mock测试描述\\n\"},{\"insert\":\"reqid\\n\"},{\"insert\":\"mock-req-id-99999\\n\"}]}",
  "typeCode": "bug",
  "issueTypeId": 3,
  "priorityCode": "priority-2",
  "priorityId": 2
}
```

### 6.2 创建缺陷响应（Mock）

```json
{
  "issueId": 999999,
  "issueNum": "MOCK-PROJECT-0001",
  "typeCode": "bug",
  "statusId": 2,
  "summary": "【自动化测试】【API】【Mock测试】冒烟验证",
  "reporterId": 6557,
  "projectId": 1169,
  "priorityId": 2,
  "issueTypeId": 3,
  "priorityDTO": {
    "id": 2,
    "name": "中",
    "colour": "#3575DF"
  }
}
```

---

## 7. 敏感信息处理规范

### 7.1 日志脱敏规则

| 字段 | 脱敏方式 |
|------|---------|
| Token | 前 8 位 + "..." + 后 4 位 |
| Password | 全部替换为 "***REDACTED***" |
| Authorization Header | 替换为 "***REDACTED***" |
| Cookie | 替换为 "***REDACTED***" |

### 7.2 环境变量配置

```bash
# 生产环境
export IDP_TOKEN="your-production-token"
export IDP_DRY_RUN="false"
export IDP_AUTO_CREATE_ENABLED="true"

# 测试环境
export IDP_TOKEN="your-test-token"
export IDP_DRY_RUN="true"
export IDP_AUTO_CREATE_ENABLED="false"
```

---

## 8. 验收检查清单

- [x] 固定账号可以成功查询项目 1169
- [x] 可以成功创建 IDP 缺陷
- [x] 标题以【自动化测试】开头
- [x] 标题不包含优先级
- [x] 描述中包含 reqid
- [x] pageNum=9999 按规则设置低或中优先级
- [x] 同一问题不会重复创建（指纹去重）
- [x] IDP 异常不影响测试结果保存
- [x] 密码和 Token 不进入代码、日志和 Git
- [ ] 报告能回写 IDP 编号和链接（待冒烟确认）
- [ ] 重复执行关联原缺陷而不是重复创建（待冒烟确认）

---

## 9. 服务端能力边界与已知限制（2026-10-15 实测）

> 实测环境：`https://idp-api.dtyunxi.cn`，项目 **17（美心-HK）**，普通测试账号 Token。
> 所有结论均为**真实请求返回**，不是推测。这些是**框架提单能力的硬约束**：
> 设计自动提单/改单能力前必读，**不要「接口存在就认为有权限」**。

### 9.1 无程序化附图通道（403）

以下端点对该 Token **全部 403 PERMISSION_MISMATCH**：

| 端点 | 结果 |
|------|------|
| `GET/POST /agile/v1/projects/{p}/issues/{id}/attachments` | 403 |
| `GET /agile/v1/projects/{p}/issue_images` | 403 |
| `GET /agile/v1/projects/{p}/upload_images` | 403 |

**结论**：框架**不得设计"自动附图"能力**，截图只能由人工拖入工单。

**唯一可能的程序化内嵌路径**：`description` 的 Delta 支持图片 op —
`{"insert":{"image":"<公网可读 URL>"}}`。已在同事单 `dmcloud-saas-67196` 描述中观察到实例
（`https://idp-minio.dtyunxi.cn/agile-service-2024/image/.../xxx_blob.png`，该 URL 公网 GET 可读，HTTP 206）。
**前提是截图必须先有公网可读地址**；本地路径、内网地址一律无效。

### 9.2 需求 / 元数据类端点不可用（403）

| 端点 | 结果 |
|------|------|
| `/agile/v1/projects/{p}/requirements` | 403 |
| `/agile/v1/projects/{p}/backlog` | 403 |
| `/agile/v1/projects/{p}/issues?typeCode=feature` | 403 |
| `/agile/v1/projects/{p}/schemes/query_issue_types` | 403 |

**结论**：**需求名称、问题类型清单等元数据无法程序化获取**，只能来自本地配置
（`idp_projects.yaml`）或人工给定。框架里任何"自动识别需求名称"的设计都不可行。

### 9.3 号段 ≠ issueId（静默返回无关单，最危险）

- 问题号 `dmcloud-saas-67196` 与 issueId `927934` **是两个不同的值**。
- 把问题号数字当 issueId 直接查（`GET /issues/67196`）会 **HTTP 200 返回另一张无关单** ——
  不报错、静默错误，极易把回读校验做成假通过。
- 且列表类端点对该 Token 403 ⇒ **无法按编号反查**，只能用**已知 issueId 锚点做区间内插扫描**定位目标单。

### 9.4 详情接口返回扁平 JSON（无 `data` 包装）

`GET /agile/v1/projects/{p}/issues/{id}?organizationId=1` **直接返回 issue 对象**，没有 `{"data": ...}`。

```python
payload = resp.json()
issue = payload.get("data") or payload   # 正确
# issue = (payload or {}).get("data") or {}   # 错误：得到空值，误判"标题/描述丢了"
```

### 9.5 更新接口契约与成功判据

`PUT /agile/v1/projects/{p}/issues`，请求体至少含：
`issueId` + `objectVersionNumber` + 目标字段（`summary` / `description`）。

**成功判据（三者同时满足才算成功）**：
1. HTTP **201**（不是 200）；
2. 回读 `objectVersionNumber` = 原值 **+1**；
3. 目标字段与预期内容**逐字**一致。

⚠️ 只查关键词（例如"描述里出现 requestId 字样"）会把 `HTTP 201` 的成功写入**误判成失败**。
⚠️ 回滚/对比场景要**按版本另存快照**（`*_original_ver2.json` / `*_v{N}.json` / `*_after.json`），
因为再次 GET 会覆盖 before 快照。

### 9.6 标题口径是双轨的，不可互相套用

| 场景 | 口径 | 示例 |
|------|------|------|
| 框架**自动**提单（`idp_defect_service._generate_title`） | `【自动化测试】【类型】【业务模块】现象` | `【自动化测试】【API】【会员卡】分页参数 pageNum=9999 返回 500` |
| **人工 / 半自动**提单（测试执行人） | `【美心-HK】_UAT_<需求名称>，<对象>，<现象>` | `【美心-HK】_UAT_YL主数据管理，店铺主数据-导入门店，导入报"Required String parameter 'excelFileUrl' is not present"，导入功能不可用` |

- 人工口径里 `_UAT_` 之后跟的是**需求名称**（如 `YL主数据管理`），**不是固定段**；
- 同事单 `dmcloud-saas-67316 / 67317 / 67324` 写成 `_UAT_CS，CS Case，`，是因为**那批的需求名称恰好叫 CS**——
  **CS 只是参照实例，不是模板**，换需求时只替换需求名称段，其余结构沿用；
- 自动口径与人工口径**并存但不得混用**：人工单不要带 `【自动化测试】` 前缀，自动单不要套 `_UAT_` 段。

### 9.7 复现锚点（供复核）

- 本次实测项目：17（美心-HK）；涉及单：`dmcloud-saas-67349`（issueId 930361）、`67350`（issueId 930362）；
  参照单：`67196`（issueId 927934）。
- 权限类结论统一返回体形态：
  ```xml
  <oauth><status>PERMISSION_MISMATCH</status><code>error.permission.mismatch</code>
  <message>This request mismatch any permission</message></oauth>
  ```
