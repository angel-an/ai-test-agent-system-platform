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

## 完整性校验

```
# SHA-256 校验（示例格式，实际值由执行环境生成）
# 生成命令：find workspace/evidence/pr2-pilot-query/20260906-1540 -type f | sort | xargs sha256sum

# 文件哈希占位符（执行时填充）
# README.md: <待填充>
# request/query_request.json: <待填充>
# response/query_response.json: <待填充>
# audit/a1_a5_record.md: <待填充>
```

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
| 证据包目录存在 | ☐ |
| 文件清单完整 | ☐ |
| 脱敏检查通过 | ☐ |
| 责任人确认 | admin |
