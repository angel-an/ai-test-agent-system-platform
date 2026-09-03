# IDP 接口契约文档

> 本文档基于 2026-08-26 真实联调结果整理
> 所有敏感信息已脱敏

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
