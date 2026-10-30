---
name: checklist-sync
description: 把清单源的表格行同步进平台（幂等 upsert + 行漂移信号）。支持任意清单源（腾讯/飞书/钉钉在线文档、Excel）导出的 JSON/CSV/xlsx。当用户要求同步/导入 Checklist 行、检测清单变更时使用。
---

# checklist-sync（框架能力，源无关）

把「清单源」的表格行同步进平台 checklist_rows 表。**清单源不限于腾讯文档**——
飞书/钉钉在线文档、本地 Excel 皆可，只要整理/导出为模板格式（模板在
`ui/public/docs/checklist-回归验证-导入模板.xlsx` / `.csv`，页面「清单同步」卡
也有下载链接）或带相同列名的 JSON/CSV/xlsx。

## 数据源

脚本不直连任何在线文档 API（授权与 API 差异是坑，ROI 低）。标准路径：
从清单源导出/下载为文件 → 本脚本导入。在线文档的读取由 WorkBuddy 等
授权通道完成，导出格式按模板对齐。

## 执行步骤

1. 从清单源导出表格行（含表头）为 JSON / CSV / xlsx，如 `/tmp/checklist-rows.xlsx`。
2. 运行（`--token` 必填，checklist 端点强制鉴权）：
   ```bash
   python .claude/skills/checklist-sync/sync_to_platform.py \
     --input /tmp/checklist-rows.xlsx --project PR-2 \
     --base http://127.0.0.1:8000 --token "$CHECKLIST_TOKEN"
   ```
3. 读输出：`created/updated/skipped` 计数。退出码 3 = 存在新增/变更行（行漂移信号），
   把变更行反馈给维护者核对；0 = 与平台一致，无漂移。
4. 页面路径亦可：「回归验证 → 清单同步」上传同一文件，先预览 diff 再确认导入。

## 列映射

脚本内置常见列名别名（主模块/子模块/功能点/预期结果/优先级/探针类型/数据模式/标题/行号），
未映射的列原样进 `extra`。列名与模板不一致时，先 `--dry-run` 检查映射结果再正式提交。

## 锚定规则（勿改）

- `row_id = "CK-" + md5(f"{主模块}|{子模块}|{功能点}")[:8]`（utf-8）
- `content_md5 = md5(f"{标题}|{预期}|{优先级}|{探针类型}|{数据模式}")`（utf-8）

平台只 upsert 不删除：源表删行不会同步到平台，需人工处理。

## 项目参考

小杨生煎 UAT 的清单源是腾讯文档（URL 与导出口径见
`.claude/skills/miniprogram-probe-run/projects/xysjg/README.md`）。
