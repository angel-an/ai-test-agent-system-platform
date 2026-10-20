---
name: api-coverage-review
description: API 接口覆盖度评审（设计后、执行前）。用 review_api_coverage 对接口契约做确定性评分——状态码/必填参数/枚举/类型与格式/请求体字段/鉴权 六维，未知项不计入分母，低于门槛返回缺失项并阻止执行。适用于生成计划与用例之后、生成或执行脚本之前。
---

# API 覆盖度评审（设计后、执行前）

## 何时使用

**必须**在下面这个时点调用 `review_api_coverage`：

```
需求/接口分析 → 测试计划 → 测试用例设计 → ★ 覆盖度评审 ★ → 脚本生成 → 执行
```

- 用例已经设计完（有明确的用例清单），但**还没有生成脚本、也没有执行**；
- 用户要求"评估接口覆盖度""这个接口测得够不够""能不能开跑"；
- 执行失败或准备回归前，想确认覆盖面是否退化。

不要在脚本执行**之后**用它做质量报告——那是 `reporter` 的职责。

## 它与其他 skill 的分工

| skill | 评什么 | 时点 |
|---|---|---|
| `test-scenario-design` | 单接口/复合场景的**设计思路** | 设计期 |
| `api-coverage-review`（本 skill） | 接口**契约面**的确定性覆盖度评分 | 设计后、执行前 |
| `quality-review` | **功能测试用例**的质量（业务视角） | 用例生成后 |
| `reporter` | 执行结果与覆盖的**文字分析** | 执行后 |

## 评审的六个维度

权重合计 100：

| 维度 | 权重 | 契约来源 | 覆盖项定义 |
|---|---|---|---|
| 状态码覆盖 | 25 | `responses` 键 | 契约声明的每个具体状态码（`default` 不计入） |
| 必填参数覆盖 | 20 | `parameters[].required=true` | 每个必填参数 |
| 枚举覆盖 | 15 | `schema.enum` | 每个"字段=取值"组合 |
| 类型与格式覆盖 | 15 | `schema.type` / `schema.format` | 每个带类型/格式的字段 |
| 请求体字段覆盖 | 15 | `request_body` schema 的 `required` | 每个必填请求体字段 |
| 鉴权场景覆盖 | 10 | `security`（已合并全局/path/method 三级） | `with_token` + `without_token` |

### 权重与门槛是平台规则，不是行业标准原文要求

ISTQB / OWASP / ISO 25010 / IEEE 829 只作为方法学**参考方向**，
它们**不规定**上述权重，也不规定 70/80/85 这些门槛。
评审报告里会把口径标注为 `platform_rule`，引用时不要表述为"行业标准要求"。

### 门槛（两层，叠加生效）

| 层级 | 门槛 |
|---|---|
| 单接口硬门槛 | 每个接口总分 ≥ 70 |
| 核心接口硬门槛 | 核心接口总分 ≥ 85 |
| 集合均值 | 平均分 ≥ 80 |
| 必填参数硬门槛 | 必填参数覆盖率 = 100% |

## 三条必须遵守的口径

1. **未知 ≠ 未覆盖。**
   契约没提供数据的维度标记为 `unknown`，从分母剔除并**重归一化**，绝不按 0 分计入。
   契约薄到无法评分时，该接口判为 `insufficient_data` 并**不给分**，进人工确认项。
2. **不做二次扣分。**
   单维度扣分 = 该维度满分 − 该维度得分，恒不超过满分。
   扣分清单只是解释，不与总分重复计算。
3. **不臆造覆盖。**
   声明覆盖了契约里不存在的状态码/参数/字段/枚举值，会被判为**非法声明**并直接阻断。
   只声明用例**真的**断言或使用过的项。

## 调用方式

```javascript
const result = await tools.review_api_coverage({
  project_identifier: "{context.project_identifier}",
  // 省略 endpoint_ids 表示评审项目下全部端点
  endpoint_ids: ["<endpoint-uuid-1>", "<endpoint-uuid-2>"],
  core_endpoint_ids: ["<endpoint-uuid-1>"],   // 核心接口套用 85 分门槛
  coverage_claims: JSON.stringify({
    "<endpoint-uuid-1>": [
      {
        case_id: "TC-001",
        status_codes: ["200", "400"],                 // 用例断言过的状态码
        parameters: ["x-tenant"],                     // 用例实际发送的参数名
        enum_values: { "body:status": ["a", "b"] },   // 字段名要带 body:/param: 前缀
        body_fields: ["name"],                        // 用例实际发送的请求体字段
        auth_scenarios: ["with_token", "without_token"]
      }
    ],
    "*": []   // 兜底：未单独声明的端点
  }),
  save_report: true   // 报告存入 API 测试成果物
});
```

### `coverage_claims` 字段说明

| 字段 | 含义 | 允许值 |
|---|---|---|
| `case_id` | 用例编号（便于追溯证据） | 任意字符串 |
| `status_codes` | 用例断言过的状态码 | 契约 `responses` 中存在的码 |
| `parameters` | 用例实际发送的参数名 | 契约 `parameters[].name` |
| `enum_values` | 覆盖到的枚举取值，键用 `param:<名>` 或 `body:<名>` | 取值必须在契约 enum 内 |
| `body_fields` | 用例实际发送的请求体字段名 | 契约请求体 schema 的字段 |
| `auth_scenarios` | 鉴权场景 | `with_token` / `without_token` / `invalid_token` / `expired_token` / `insufficient_scope` / `wrong_tenant` |

整理 `coverage_claims` 时**逐条对照已有用例**，不要凭印象填。

## 结果处理

```
blocked == true
  → 禁止生成脚本、禁止执行
  → 按 block_reasons / endpoints[].dimensions[].missing 补齐用例
  → 补齐后重新调用 review_api_coverage

blocked == false
  → 可以进入脚本生成与执行阶段

manual_review_items
  → 这些维度第一阶段不自动评分（无结构化来源），需人工确认：
    业务规则、权限矩阵、状态机约束、限流/超时/依赖服务不可用、
    安全 payload（SQL 注入/XSS 等）、响应"核心字段"定义
  → 报告里必须如实呈现，不要用自动分掩盖它们没被评过
```

## 执行门禁

评审结论会写入端点 `custom_config["coverage_gate"]`，`execute_api_script` 在执行前读取它：

| 环境变量 | 行为 |
|---|---|
| 未设置 / `warn`（默认） | 未通过时只记录告警，**不阻断**执行 |
| `API_COVERAGE_GATE_STRICT=strict` | 未通过时**硬阻断**，返回 `COVERAGE_GATE_BLOCKED` |

因此：**不要**依赖 warn 模式放行。评审未通过时，正确做法是补齐缺口，而不是去跑脚本。

## 常见误用

| 误用 | 后果 | 正确做法 |
|---|---|---|
| 把契约里没有的维度（业务规则、权限矩阵）说成"已覆盖" | 非法声明，直接阻断 | 放进人工确认项 |
| 契约信息缺失时按 0 分报"未覆盖" | 把未知说成未覆盖，误导决策 | 看 `unknown` / `insufficient_data`，如实说明契约缺什么 |
| 只在报告里写分数，不落 `coverage_claims` | 无证据，分数不可复算 | 每条声明带 `case_id` |
| 用本 skill 代替执行后的结果分析 | 覆盖度≠通过率 | 执行后用 `reporter` |
