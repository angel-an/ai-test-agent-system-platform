# 列表通道横向推广盘点（filter_checks / action_matrix_checks）

> 日期：2026-10-22　范围：项目 PR-2（项目 uuid `9930b156-…`）全部 60 个 web 子功能
> 目的：确认「状态筛选结果集」与「状态×操作矩阵」两条通道还能复制到哪些子功能，给出范围与工作量，供确认后执行。

## 1. 通道现状（已固化在编译器 rev179）

配置入口只有一个：`web_sub_functions.custom_config.field_locators`（子功能级 override）。

```jsonc
"filter_checks": [{            // 状态筛选结果集 + 重置
  "option": "启用", "option_labels": ["启用","啟用"],
  "expect_column": "状态", "expect_in": ["启用"],
  "control_placeholder": ["请输入状态","狀態","状态"],
  "reset": true, "reset_label": "重\\s*置|重\\s*設|Reset",
  "case_id": "TC-…"
}],
"action_matrix_checks": [{     // 某状态行的操作集 + 更多菜单 + 确认框
  "status_in": ["启用"], "status_column": "状态",
  "expect_row": ["查看","复制","禁用","更多"], "forbid_row": ["编辑","启用"],
  "expect_menu": ["删除","导出明细"],
  "confirm": {"label": "删除", "via": "menu"},   // via: menu | row
  "filter": false, "filter_option": "启用",       // 目标状态不在第 1 页时置 true
  "case_id": "TC-…"
}]
```

执行口径（实测结论，推广时必须遵守）：
- 筛选下拉选中后**必须点搜索**才刷新，`reset` 的语义是置回「全部」而非清空；
- 表头用站点实际语言（本项目是简体「状态」）；
- 行操作可能渲染在**独立操作行 tr**，定位需过滤隐藏克隆表；
- 点业务动作必须走 Playwright 真实点击（locator），evaluate 合成 click 对 antd 菜单项无效；
- 断言集合只从 `a/button` 收集标签，`.ant-tag`（状态单元格）会污染 forbid 判定。

## 2. 盘点结果（依据：DB 用例/配置清单 + 用例名语义，**未做新 UI 探针**）

| 子功能 | 现有用例 | 列表/状态迹象 | 缺什么 | 判断 |
|---|---|---|---|---|
| `marketing-cloud-survey-management/survey-operation` | 21 | 已配 `fields=9 / filter=2 / matrix=3 / export=1 / list=2 / negative=1` | — | **基线，已完成**（TR-795 全绿 13/13） |
| `purchase-limit-management/purchase-limit-form-probe` | 47 | 用例含「列表完整展示五个业务字段」「列表操作区删除/编辑」「状态切换为不启用后列表与 C 端同步」「新建时状态默认启用」 | 原有 47 条 `web_blocked`（无业务模板）；已改走列表通道 | **已完成**（rev177–179，TR-798 3/3 pass；见 §4） |
| `stored-value-enterprise-recharge/stored-value-enterprise-recharge-crud` | 24 | 全为表单/上传/流水类，无列表状态列迹象 | 不适用 | 不推广 |
| `dtcloud-micro-mall/{order-list,item-list,netstore-list,after-sale}` | 0 | 命名即列表页，交易云域 | 无用例、无 locator | B 类（先建用例） |
| `member-management/member-list-search` | 0 | 命名含 list-search | 同上 | B 类 |
| `coupon-template-management/coupon-template-list`、`/coupon-template-pagination` | 0 | 列表 + 分页 | 同上 | B 类 |
| `tag-management/tag-query` | 0 | 查询页 | 同上 | B 类 |
| 菜单遍历类（`*-menu-check`、`marketing-cloud-menu-auto-test`） | 0 | 只做菜单可达性 | 无业务列表 | 不适用 |
| 登录/门户类（`login-function`） | 0 | 无列表 | — | 不适用 |

## 3. 工作量分层

- **A 类（有用例，仅缺 locator 配置）**：`purchase-limit` 1 个 —— **已完成**（见 §4）。
  实际工作量比预估多一步：纯列表子功能暴露了 3 个通道缺口，需 3 个 rev（177/178/179）修。
  后续同类纯列表子功能**不再需要改代码**（缺口已补），只配 `list_url` + `action_matrix_checks` 即可。
- **B 类（列表存在但无用例/无 locator）**：micro-mall 4 个列表、member-list-search、coupon-template-list、tag-query。
  每个都需先补需求侧用例，属「扩覆盖」而非「通道推广」，工作量大一个量级。
- **不建议**：表单/上传类、菜单遍历类、登录类。

## 4. 执行结论（2026-10-22，A 类已落地）

`purchase-limit-form-probe` 已推广完成并通过验收（**TR-798：MATRIX_PHASE 3/3 pass，0 unverified，balanced=true**）。
过程中暴露并修掉了 3 个「只有列表页的子功能」专属缺口 —— 这 3 个缺口在 survey 上永远不会显形
（survey 同时有字段级配置和表单页），是推广到纯列表子功能才暴露的：

| # | 症状（回执里看到什么） | 根因 | 修复 |
|---|---|---|---|
| 1 | `status_in` 恒 `no_row_matched`，cells 全空 | 状态列是 `.ant-switch`，单元格**无文本**，只有 checked 位 | rev177：状态列有 switch 时按 `ant-switch-checked` 折算标签（`status_on_label`/`status_off_label`，缺省 启用/不启用） |
| 2 | `form_locator_source=project_registration`、`form_executable_checks=0` | `resolve_form_locators` / `build_form_plan` 要求 `fields` 非空，列表通道配置被当「没配置」跳过 | rev178：按 `has_channel_config` 判定（list_url/list_checks/filter_checks/action_matrix_checks/export_checks/negative_scenarios） |
| 3 | 入口阶段硬失败 `无法打开表单：定位器未配置表单入口（flow=recharge_modal）` | `runFormChecks` 只要 `planChecks.length > 0` 就 `openForm()`，列表域检查也被算进去 | rev179：`list_visible/export_check/filter_check/action_matrix` 视为列表域，无表单域检查时跳过 openForm（create 语义场景仍预开） |

### 落地配置（`web_sub_functions.custom_config.field_locators`，子功能 `1fa23da4-…`）

```jsonc
{
  "name": "会员限购商品管理",
  "list_url": "#/decoration/purchaselimit",
  "action_matrix_checks": [
    {"name":"会员限购·启用态行操作集","status_column":"状态","status_in":["启用"],
     "status_on_label":"启用","status_off_label":"不启用","expect_row":["编辑","删除"],
     "case_id":"TC-920382"},
    {"name":"会员限购·不启用态行操作集","status_in":["不启用"],"expect_row":["编辑","删除"],
     "case_id":"TC-650254"},
    {"name":"会员限购·删除必须二次确认","status_in":["不启用"],"expect_row":["删除"],
     "confirm":{"label":"删除","via":"row"},"case_id":"TC-827142"}
  ]
}
```

### 本站实测事实（下次复制到别的列表页直接照用）

- `list_url` 是 SPA hash 路由，**可直连**（`#/decoration/purchaselimit`），不必逐级点菜单；
  页面外壳 `https://imarketing-uat.xysjg.com/front/dtcloud-trade-web-pc/`。
- 表头 6 列：限购商品名称 / 限购标签 / 限购商品 / 限购周期 / **状态** / 操作；
  **没有筛选区**（无 search input、无状态下拉）⇒ `filter_checks` 不适用，`filter:false`。
- 第 1 页 10 条里只有 1 条启用（index 2），9 条不启用 ⇒ 两种状态同页可见，不需要翻页/筛选。
- 首行是**空占位 tr**（无 switch、无标签），定位按「状态列文本匹配」自然跳过。
- 行操作只有 `编辑 / 删除`（无「更多」菜单）⇒ `expect_menu` 不配，`confirm.via` 只能用 `row`。
- 删除确认是 **Popconfirm**「确定要删除吗」（`.ant-popover-buttons`，取消在第一个），取消后行数不变。

### 仍然悬置

- **状态开关（.ant-switch）点击后的行为未验证**：点它会真实改变线上数据，本轮按「不碰写操作」处理，
  所以「切换状态 → 列表/C 端同步」（TC-501561）仍属人工或后续单独评估项。
- B 类子功能（micro-mall 列表族、member-list-search、coupon-template-list、tag-query）仍缺用例，
  通道侧现在已具备条件，属「补用例」而非「改通道」。

## 5. 原始待确认项（已由「按专业意见执行」结案）

1. 本轮只推 `purchase-limit` —— **是**，其余 B 类留待补用例。
2. 状态值域「启用/不启用」—— **两态即全态**，两条矩阵检查已覆盖。
3. 只读采样 —— **已执行**，未新建/未删除任何数据（删除确认框只点开再取消，行数校验不变）。
