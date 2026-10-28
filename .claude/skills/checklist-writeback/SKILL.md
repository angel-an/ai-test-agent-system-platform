---
name: checklist-writeback
description: 把平台最近一次 Checklist 回归执行结果生成写回腾讯在线文档的行载荷。当用户要求把执行结果写回清单文档、登记自动化执行记录时使用。
---

# checklist-writeback

平台只负责存执行结果；**写文档由 WorkBuddy 用腾讯文档授权完成**。本 skill 生成要插入的那一行载荷。

## 执行步骤

1. 生成载荷（缺省取最近一次执行）：
   ```bash
   python .claude/skills/checklist-writeback/build_writeback_payload.py \
     --project PR-2 --base http://127.0.0.1:8000 --out /tmp/writeback-row.json
   ```
2. 通过 WorkBuddy 打开《上线Checklist》文档，把 JSON 对象的值按列名映射**插入一行**到「自动化执行」记录区（表格末尾或专门的执行记录 tab）。
3. 若失败行非空，`失败行` 列会列出 `row_id 功能点(原因)`，逐条核对。

## 载荷列

执行时间 / 执行方式（默认"自动化执行"）/ 项目画像 / 判定 / 通过 / 失败 / 未验证 / 流程类跳过 / 失败行 / 报告链接 / run_id

指定某次执行用 `--run-id`；执行方式文案用 `--label` 覆盖。
