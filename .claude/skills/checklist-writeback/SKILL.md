---
name: checklist-writeback
description: 把平台最近一次回归执行结果生成写回清单文档的行载荷（12 列契约，源无关）。当用户要求把执行结果写回清单文档、登记自动化执行记录时使用。
---

# checklist-writeback（框架能力，源无关）

平台只负责存执行结果、算载荷；**写入清单文档由外部执行器完成**（腾讯文档走
WorkBuddy 授权；钉钉/飞书等有对应执行器时按同一契约写入）。本 skill 生成要插入的那一行载荷。

> 平台「回归验证」页已有「结果回写」卡片（数据源 `GET /checklist/writeback-preview`），
> 页面上可直接复制/下载载荷。本脚本用于脱离浏览器的场景（CI、定时任务），两者列契约一致。

## 执行步骤

1. 生成载荷（缺省取最近一次执行；`--token` 必填，checklist 端点强制鉴权）：
   ```bash
   python .claude/skills/checklist-writeback/build_writeback_payload.py \
     --project PR-2 --base http://127.0.0.1:8000 \
     --token "$CHECKLIST_TOKEN" --out /tmp/writeback-row.json
   ```
2. 由执行器把 JSON 对象的值**按列名映射**插入清单文档的执行记录区
   （表格末尾或专门的执行记录 tab；插入必须按列名，不按位置）。
3. 若失败行非空，`失败行` 列会列出 `row_id 功能点(原因)`，逐条核对。

## 载荷列（12 列契约，与平台 WRITEBACK_COLUMNS 同步，单测锁死）

执行时间 / 执行方式（默认"自动化执行"）/ 项目画像 / **主模块** / 判定 / 通过 / 失败 /
未验证 / 流程类跳过 / 失败行 / 报告链接 / run_id

- 主模块：run 显式 `main_module`，或 results 各行去重拼接
- `--by-module`：按主模块逐行出载荷（模块汇总视角），同一 12 列契约，缺省维度填空串
- 指定某次执行用 `--run-id`；执行方式文案用 `--label` 覆盖
- 列契约任何变动须同步平台 `checklist_sync.WRITEBACK_COLUMNS`，
  且必须更新 `backend/tests/api/test_checklist_writeback_contract.py`（契约单测）

## 项目参考

小杨生煎 UAT 的写回目标是腾讯文档「自动化执行记录」区（表结构与协作约定见
`.claude/skills/miniprogram-probe-run/projects/xysjg/README.md`）。
