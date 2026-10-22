# 问卷表单 radio/checkbox 必填字段 —— API 层兜底补测记录

> 日期：2026-10-22　项目：**小杨生煎**（需求名称：问卷调查）　SUT：数盈营销云 UAT（`imarketing-uat.xysjg.com`）　子功能：`marketing-cloud-survey-management/survey-operation`
> 背景：5 条用例的 `submit_only`（提交时校验）在 UI 层恒为 `unverified`——单选组有默认选中、多选组无自由输入，
> 页面构造不出「必填为空」的提交。按计划改走 API 层直调创建接口，验证服务端是否有必填兜底。

## 1. 被测接口

```
POST https://imarketing-uat.xysjg.com/yundt-saas-gateway/api/icdp/v1/evaluate
GET  .../v1/evaluate/{id}        # 回读
DELETE .../v1/evaluate/{id}      # 删除（清理用，data:true 即成功）
```

- **鉴权不是 cookie 就够**：用浏览器上下文直接发请求会命中网关 `SaasUserInfoInterceptor`
  （`code=100000 应用识别失败 / app实例key为空或不存在`）。必须带前端真实请求头
  `access-token` + `defaultorg` + `dtyunxi.saas.service.namespace/region` + `dtyunxi.service.version`。
  采集方式：context 级 request 监听 + 取**登录后**那条（覆盖式捕获，早期请求 token 为空会 401 `no login`）。
- 请求体（实测可用）：

```json
{"type":1,"sbjTypeName":"XY<tag>","orderType":"1","priority":1,
 "applyStoreType":0,"applyStoreCodes":null,"applyGoodsType":0,"applyGoodsCodes":null,
 "questionContent":"[{\"sort\":1,\"type\":1,\"required\":1,\"title\":\"服务满意度\",\"options\":[\"满意\"]}]",
 "status":0}
```

## 2. 用例与字段映射

| 用例 | 字段（UI 标签） | 请求体位置 | 省略后服务端行为 | 回读落库 |
|---|---|---|---|---|
| TC-740182 | 业务类型（多选） | `orderType` | 200 / `resultCode=0` 创建成功 | **`orderType: null`** |
| TC-551349 | 适用门店（单选） | `applyStoreType` | 200 / `resultCode=0` 创建成功 | `applyStoreType: 0`（= 全部门店，等同界面默认） |
| TC-501964 | 适用商品（单选） | `applyGoodsType` | 200 / `resultCode=0` 创建成功 | `applyGoodsType: 0`（= 全部商品，等同界面默认） |
| TC-122920 | 题目类型（单选） | `questionContent[0].type` | 200 / `resultCode=0` 创建成功 | `questionContent` 中**无 `type` 键**（未补默认值） |
| TC-628689 | 是否必答（单选） | `questionContent[0].required` | 200 / `resultCode=0` 创建成功 | `questionContent` 中**无 `required` 键**（未补默认值） |

正向对照（完整请求体）同样 `resultCode=0`，证明上面的负例失败不是请求体形状错误。

## 3. 结论

1. **服务端对这 5 个字段都没有必填校验**（省略即通过，无一返回 `400`/非 0 码）。
2. 其中 3 个的省略后果可接受：`applyStoreType`/`applyGoodsType` 落默认 0，与界面默认值一致 ⇒ **不算缺陷**，
   对应 `submit_only` 可由 unverified 收敛为「服务端默认值兜底、前端必填不可绕过」。
3. 2 个存在数据完整性风险，**建议登记缺陷（合并 1 条）**：
   - `orderType` 省略 ⇒ 落库 `null`，即可通过接口创建「无业务类型」的问卷；
   - `questionContent.type` / `.required` 省略 ⇒ 题目缺失类型/必答标记，未补默认值。
   影响：绕过前端必填（直接调接口或脚本批量建单）可产生脏数据，列表/详情渲染与后续编辑行为不确定。
4. 该结论是**服务端校验缺失**，不是测试侧问题；判定依据为真实业务断言结果
   （HTTP 200 + `resultCode=0` + 回读落库值），符合登记门槛。

## 4. 探测数据清理

- 共创建 11 条探测记录（首轮 6 条 + 回读 5 条），全部通过 `DELETE /v1/evaluate/{id}` 删除，返回 `"data":true`。
- 标题前缀统一 `XY<MMDDHHMMSS>API` / `XY<...>ART`，便于列表核对。
- 证据：`.workbuddy/tmp/xy_survey/api_required_probe_1022091221.json`、`api_required_readback_*.json`；
  脚本：`.workbuddy/tmp/api_required_probe2.py`、`api_required_readback.py`、`api_cleanup_probe.py`。

## 5. 缺陷草稿（待确认后登记，不自动提）

- 标题：`【小杨生煎】_UAT_问卷调查，调查问卷管理-新建问卷，接口未校验必填字段，可创建无业务类型/无题目类型的问卷`
- 类型：功能缺陷（服务端校验缺失）　优先级：中
- 复现：登录 UAT → 取前端 `access-token` 请求头 → `POST /v1/evaluate`，请求体删除 `orderType` 后提交 →
  返回 `resultCode=0` 并生成 `id`；`GET /v1/evaluate/{id}` 回读 `orderType` 为 `null`。
- 预期：服务端校验必填，返回非 0 错误码并提示（如「请选择业务类型」），不落库。
- 实际：创建成功且落库为空值。
