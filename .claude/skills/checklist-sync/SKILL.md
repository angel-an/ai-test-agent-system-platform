---
name: checklist-sync
description: 把「上线Checklist」腾讯在线文档的表格行同步进平台（幂等 upsert + 行漂移信号）。当用户要求同步/导入 Checklist 行、检测清单变更时使用。
---

# checklist-sync

把腾讯在线文档《上线Checklist》(`https://docs.qq.com/sheet/DVWVLUFFnYVhwdGdH?tab=1h8nmv`) 的表格行同步进平台 checklist_rows 表。

## 数据源（重要）

腾讯文档的读取**依赖 WorkBuddy 的腾讯文档授权**：由 WorkBuddy 打开该文档，读出/导出当前 tab 的全部表格行为 JSON 或 CSV，存成临时文件后交给脚本。不要在无授权的环境尝试直抓 docs.qq.com。

## 执行步骤

1. 通过 WorkBuddy 打开上述文档，导出表格行（含表头）为 `/tmp/checklist-rows.json`（数组，元素为列名→值的字典）。
2. 运行：
   ```bash
   python .claude/skills/checklist-sync/sync_to_platform.py \
     --input /tmp/checklist-rows.json --project PR-2 \
     --base http://127.0.0.1:8000
   ```
3. 读输出：`created/updated/skipped` 计数。退出码 3 = 存在新增/变更行（行漂移信号），把变更行反馈给维护者核对；0 = 与平台一致，无漂移。

## 列映射

脚本内置常见列名别名（主模块/子模块/功能点/预期结果/优先级/探针类型/数据模式/标题/行号），未映射的列原样进 `extra`。腾讯文档实际列名若不同，先 `--dry-run` 检查映射结果再正式提交。

## 锚定规则（勿改）

- `row_id = "CK-" + md5(f"{主模块}|{子模块}|{功能点}")[:8]` —— 与库内既有 96 行 96/96 吻合
- `content_md5 = md5(f"{标题}|{预期}|{优先级}|{探针类型}|{数据模式}")` —— 2026-10-28 重建版公式，库内已按此回填

平台只 upsert 不删除：表格删行不会同步到平台，需人工在平台处理。
