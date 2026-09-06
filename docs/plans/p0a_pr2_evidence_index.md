# PR-2 只读接入试点证据索引

> 脱敏证据包索引，用于验证证据完整性，不暴露敏感数据。
> 证据目录被 .gitignore 忽略，本索引文件纳入版本控制。

---

## 证据包信息

| 字段 | 内容 |
|---|---|
| 证据包 ID | pr2-pilot-query-20260906-1540 |
| 项目 | PR-2 储值充值 |
| 操作 | 会员账户只读查询 |
| 执行时间 | 2026-09-06 15:40 |
| 执行人 | admin |
| 风险等级 | L1（UAT 只读数据） |
| 审批 | 无需审批 |

---

## 文件清单

| 文件路径 | 类型 | 脱敏状态 |
|---|---|---|
| `workspace/evidence/pr2-pilot-query/20260906-1540/README.md` | 说明 | 无敏感数据 |
| `workspace/evidence/pr2-pilot-query/20260906-1540/request/query_request.json` | 请求 | 手机号已脱敏 |
| `workspace/evidence/pr2-pilot-query/20260906-1540/response/query_response.json` | 响应 | 手机号、卡号已脱敏 |
| `workspace/evidence/pr2-pilot-query/20260906-1540/audit/a1_a5_record.md` | 记录 | 无敏感数据 |

---

## 完整性校验（SHA-256）

```
c0f21cac99e9da96db5b985cdf8f67590ac0f0bde1d0608605604b1f4133f478  README.md
1c92b91068d0aad921d27386a35ae501bd3d82899d20b72d2e33fa33bbe59591  request/query_request.json
6cab48b3d6e737b57cbf367d9b0ddc5565f6792a7ac5a694003efe3e1b4d7231  response/query_response.json
898bc6bd6184dba846c8b85be6185d4b34ce7c0c0ebd868f6713f6811b04167c  audit/a1_a5_record.md
```

**校验说明**：4 个文件全部记录 SHA-256，完整性可验证。

---

## 脱敏声明

- [x] 请求参数中的手机号已脱敏（`183****0265`）
- [x] 响应中的手机号已脱敏（`183****0265`）
- [x] 响应中的卡号已脱敏（`C****1234`）
- [x] 无 token、Cookie、密码出现在证据中
- [x] 文件名无敏感信息

---

## 保留期限

- 证据包保留至：2026-09-13（项目结束后 7 天）
- 索引记录保留：长期（纳入版本控制）

---

## 验证状态

| 检查项 | 状态 |
|---|---|
| 证据包目录存在 | ☑ |
| 文件清单完整（4 个文件） | ☑ |
| 脱敏检查通过 | ☑ |
| SHA-256 已记录 | ☑ |
| 责任人确认 | admin |
