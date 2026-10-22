# 列表通道横向推广盘点（filter_checks / action_matrix_checks）

> 日期：2026-10-22　范围：项目 PR-2（项目 uuid `9930b156-…`）全部 60 个 web 子功能
> 目的：确认「状态筛选结果集」与「状态×操作矩阵」两条通道还能复制到哪些子功能，给出范围与工作量，供确认后执行。

## 1. 通道现状（已固化在编译器 rev175/176）

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
| `purchase-limit-management/purchase-limit-form-probe` | 47 | 用例含「列表完整展示五个业务字段」「列表操作区删除/编辑」「状态切换为不启用后列表与 C 端同步」「新建时状态默认启用」 | 无 `field_locators`：需探针拿列表 URL、表头、状态列、状态下拉、行操作 | **首选候选**（A 类） |
| `stored-value-enterprise-recharge/stored-value-enterprise-recharge-crud` | 24 | 全为表单/上传/流水类，无列表状态列迹象 | 不适用 | 不推广 |
| `dtcloud-micro-mall/{order-list,item-list,netstore-list,after-sale}` | 0 | 命名即列表页，交易云域 | 无用例、无 locator | B 类（先建用例） |
| `member-management/member-list-search` | 0 | 命名含 list-search | 同上 | B 类 |
| `coupon-template-management/coupon-template-list`、`/coupon-template-pagination` | 0 | 列表 + 分页 | 同上 | B 类 |
| `tag-management/tag-query` | 0 | 查询页 | 同上 | B 类 |
| 菜单遍历类（`*-menu-check`、`marketing-cloud-menu-auto-test`） | 0 | 只做菜单可达性 | 无业务列表 | 不适用 |
| 登录/门户类（`login-function`） | 0 | 无列表 | — | 不适用 |

## 3. 工作量分层

- **A 类（有用例，仅缺 locator 配置）**：`purchase-limit` 1 个。
  预估：1 次探针（列表结构/状态值域/行操作）→ 写 2~4 条 checks → 建 3~4 条用例 → 1 轮 TR 验收。
  不改代码（通道已固化），只动配置 + 用例。
- **B 类（列表存在但无用例/无 locator）**：micro-mall 4 个列表、member-list-search、coupon-template-list、tag-query。
  每个都需先补需求侧用例，属「扩覆盖」而非「通道推广」，工作量大一个量级。
- **不建议**：表单/上传类、菜单遍历类、登录类。

## 4. 待确认

1. 本轮只推 `purchase-limit`（A 类）是否够？还是连 B 类一起排？
2. `purchase-limit` 的状态值域是「启用/不启用」，矩阵断言按几态做（全态 vs 只做启用/不启用两态）？
3. 探针阶段允许直接在 UAT 列表页做只读结构采样（不新建/不删除数据）——确认后我按此执行。
