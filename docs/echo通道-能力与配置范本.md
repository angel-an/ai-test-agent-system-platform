# echo 通道：能力边界与配置范本（2026-10-23）

> 面向"要配一个回显检查"的人。结论全部来自 TR-805 ~ TR-809 的真实通道运行，
> 不是设计推演。每个"为什么这么配"背后都是一次踩坑。

## 1. 它验证什么

字段级校验（必填/长度/枚举/边界）天生覆盖不了这条链路：

```
新建 → 选对象（门店/商品/题目）→ 填必填项 → 保存 → 重新打开 → 看回显是否还是我选的那个
```

`echo_checks` 就是这条链路。它**只判末态**：保存后重开，字段回显是否仍包含当时选中的值。
不判业务规则（价格/库存/权限），不看中间请求。

## 2. 三个阶段与判据

| 阶段 | 输出 | 判据 |
|---|---|---|
| 预填 | `ECHO_STAGE prefill`（逐字段留痕 `prefillTrace` / `prefillMiss`） | 每个 `required_fields` 都要落到具体控件上 |
| 保存 | `save_result`（`left_form` / `toast` / `notices` / `err_items`） | **三者任一**成立即视为已离开表单：URL 离开表单页 / 成功提示出现 / 拦截提示出现 |
| 重开 | `ECHO_TRACE`（`picked` vs `echoed` / `missing`） | 回显值**包含**选中值（全等或子串），全部字段命中才算 pass |

⚠️ **保存判据的坑（rev194）**：`route_form` 是整页表单（不是弹窗），`modalGone` 恒为真。
早期实现拿"弹窗消失"当保存成功的信号，导致"标题必填"这类前端拦截被误读成
「列表未找到记录行」。现在判据与弹窗解耦，只看上面三个信号。

⚠️ **重开前那次点击要验效果（rev196）**：本 SUT 的操作列是**独立的动作表**，数据行里的
`<a>` 点了没反应且不报错（异常被吞）。早期实现直接 `row.locator('a').click()`，然后在
**列表页**读值，把一个完好无损的编辑页（实测 9 个表单项 + 子表 2 行）报成
「编辑页字段不存在」。现在三策略点击（行内 locator → 链接坐标点击 → 按「操作」表头定位
动作表同序号行）+ 三条件验证（URL 变化 / 新开页 / 表单项数变多），无效则明确报
「编辑入口点击无效」，不再假装是字段问题。

## 3. 配置速查

配置位置：`custom_config.field_locators.echo_checks`（数组）。

```jsonc
{
  "name": "echo:适用门店",              // 断言名，缺省 echo:<首个 field.label>
  "list_url": "#/mainData/shopManage",  // 必要时直链；缺省回退 locators.list_url
  "new_button": "新增|新建|添加|New|Create",
  "fields": [                           // 必填，≥1 项
    {
      "label": "适用门店",               // 页面字段文案（不是文档字段名）
      "kind": "multi_select",           // multi_select | select | input（缺省 select）
      "pick": 2,                        // 选几个（缺省 multi=2 / 单=1）
      "option_labels": ["门店A", "门店B"],// 选什么；不填则取面板前 N 项（自洽比对）
      "gate": "指定门店",                // 选对象前先点的单选/复选项
      "picker": "modal",                // select（表单内下拉）| modal（按钮唤起弹窗）
      "picker_button": "选\\s*择|添\\s*加",
      "confirm_button": "添加并关闭|添\\s*加|确\\s*定|保\\s*存|OK",
      "row_text_column": "门店名称",     // 弹窗行里取哪列作文本（缺省整行）
      "echo_scope": "item",             // item（表单项内）| subtable（表单下展开子表）
      "echo_column": "门店名称"          // 子表回显取哪列（缺省整行文本）
    }
  ],
  "required_fields": [                  // 保存前预填；value 兼作重开时的行定位锚
    {"label": "题目标题", "kind": "input", "value": "回归{{RUN_TAG}}"},
    {"label": "选项内容", "kind": "checkbox_group", "value": "选项A、选项B"}
  ],
  "save_button": "保存|确定|Save",
  "row_match": "回归{{RUN_TAG}}",        // 重开时定位记录行的文本；缺省取首个 required_fields.value
  "case_id": "TC-XXXX", "rule_id": "R-XX"
}
```

三条硬约束：

1. **`label` 必须是页面文案**。文档写「题目标题」、页面标签是「标题」时，
   字段定位会落空（TR-806）。要么改 `label`，要么在 `field_locators.aliases` 里登记
   文档名→页面名（rev195 起预填项会走别名解析，并标注 `via`）。
2. **`value` 要带 `{{RUN_TAG}}`**。不带就每次都写同一个编号，第二次跑撞唯一约束，
   期望受理通道会把它判成「疑似缺陷坐实」= 假缺陷。
3. **组类必填（checkbox_group）要配 `value`**。不配时执行器会**构造最小合法值**
   （勾第一个可用选项）并标 `via: "auto_first_option"`，只为让链路能往下走——
   自动选中的值不参与回显比对。要验证"选中的确实是业务要求的那些"，必须显式写 `value`。

## 4. 一个完整范本：问卷「新增题目」

```jsonc
"echo_checks": [{
  "name": "echo:新增题目回显",
  "list_url": "#/survey/list",
  "fields": [{
    "label": "适用门店",
    "kind": "multi_select",
    "pick": 2,
    "option_labels": ["小杨生煎-测试门店01", "小杨生煎-测试门店02"],
    "gate": "指定门店",
    "picker": "modal",
    "picker_button": "选\\s*择",
    "confirm_button": "添加并关闭|确\\s*定",
    "row_text_column": "门店名称",
    "echo_scope": "item"
  }],
  "required_fields": [
    {"label": "标题", "kind": "input", "value": "回显验证{{RUN_TAG}}"},
    {"label": "选项内容", "kind": "checkbox_group", "value": "选项A、选项B"}
  ],
  "save_button": "保存"
}]
```

实测结果（TR-808，rev196）：

```
ECHO_STAGE {"stage":"prefill","trace":[{"label":"标题","via":"selector"}, ...]}
SAVE_RESULT {"left_form":true,"notices":[]}
ECHO_TRACE {"picked":{"适用门店":["…01","…02"]},"echoed":{...},"missing":[],"ok":true}
ECHO_PHASE {"configured":1,"executed":1,"pass":1,"unverified":0}
```

`picked == echoed` 且 `missing` 为空 = 这条链路真的对上了。
`tried:["locator","locator","mouse-on-link"]` 说明编辑入口用了第 2 策略才点开——
这不是缺陷，是本 SUT 的动作列结构。

## 5. unverified 的六种原因与处置

| 输出特征 | 真实含义 | 怎么改 |
|---|---|---|
| `prefillMiss: [{label, reason:"字段未找到"}]` | `label` 与页面文案不一致 | 改 `label` 或在 `aliases` 登记 |
| `notices: ["题目1：请输入标题"]` | 前端拦截，**保存请求没发** | 补 `required_fields`（尤其组类必填） |
| `prefillMiss: ["业务类型（勾上 1/3 个选项…）"]` | 组类必填没勾全 / 点击被覆盖层挡住 | 核对 `value` 的选项文本；看 `via` 是否 `auto_first_option` |
| `save_result.err_items` 非空 | 服务端拒绝 | 看 `err_items` 文案，多半是缺必填/唯一约束 |
| `编辑入口点击无效` | 三策略都没点开编辑页 | 配 `row_text_column` / 检查动作表结构 |
| `回显缺「X」（实际 [...]）` | 保存成功但回显不一致 | **这是真缺陷**，按 §6 登记 |
| `编辑页字段不存在` | 重开页确实没有该字段 | 确认 `echo_scope`、`echo_column` 是否正确 |

判定口径：`missing` 非空 → `unverified`（不判 pass，也不直接当缺陷）——
必须先把上面六种原因排掉，剩下的才是业务问题。

## 6. 什么时候这条算缺陷

`missing` 非空且**已排除**下列测试侧原因：

- `label` 拼写/繁简/全半角差异；
- 组类必填没配（前端拦截，请求未发出）；
- 编辑入口没点开（看 `tried` 与 URL）；
- `echo_scope` / `echo_column` 选错（回显在子表里却按表单项读）。

排掉之后，「保存成功 + 重开值不同」就是被测系统缺陷，按六要素登记（场景/步骤/预期/实际/
账号/截图 + 接口与 requestId）。纯前端拦截类问题要写明"未产生服务端请求 ⇒ 无接口/reqid"。

## 7. 当前边界（明确不覆盖）

- 只比对**回显文本包含**，不做业务规则校验（价格、库存、可见性）；
- 单选/多选按"选项文本"比，不校验 ID 与顺序；
- 子表回显只读**首屏**，翻页/懒加载的行看不到；
- 不覆盖"取消保存后不落库"这类反向场景（属负路径通道）；
- `checkbox_group` 只验证回显含这些选项，不验证选项顺序与勾选态样式。

## 8. 相关实现位置

| 关心什么 | 看哪里 |
|---|---|
| 配置解析（字段域、缺省值） | `web_case_compiler.parse_echo_checks` |
| 三阶段执行 | `web_case_compiler._FORM_EXECUTOR_TS` 的「配置保真·回显」段 |
| 预填字段定位 | rev195：`required_fields` 下发 `selector/scope`，走 `aliases` 解析 |
| 编辑入口 | rev196：三策略点击 + 效果验证 |
| 保存判据 | rev194：`left_form / toast / notices` 三者任一 |
| 回归测试 | `tests/test_channel_rev19{4,5,6}_*.py`、`tests/test_echo_*.py` |
