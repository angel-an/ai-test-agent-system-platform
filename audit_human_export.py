# -*- coding: utf-8 -*-
"""导出《人工用例自动化可行性-20261017.xlsx》：385 条逐条明细 + 分档 + 可立即执行清单。"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent
AUD = ROOT / "out_yl_case_audit"

cov = json.loads((AUD / "human_coverage_20261017.json").read_text(encoding="utf-8"))
tier = json.loads((AUD / "human_feasibility_20261017.json").read_text(encoding="utf-8"))

tier_map = {r["case"]: r for r in tier["rows"]}
cov_rows = {r["case"]: r for r in cov["rows"]}

HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
HEAD_FONT = Font(color="FFFFFF", bold=True, size=10)
TITLE_FONT = Font(bold=True, size=12, color="1F4E79")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

FINAL = {"PASSED", "PARTIAL", "UNVERIFIED", "FAILED_ASSERT"}


def verdict_label(v: str) -> str:
    return {
        "PASSED": "通过",
        "PARTIAL": "部分通过",
        "UNVERIFIED": "UI层无法判定",
        "FAILED_ASSERT": "断言失败",
        "BLOCKED_EXEC": "执行阻断",
        "NOT_EXECUTED": "零可执行检查",
        "NOT_RUN": "未执行",
        "ERROR": "执行异常",
        "TIMEOUT": "超时",
    }.get(v, v)


def status_label(has_assert: bool, has_asset: bool, verdict: str) -> str:
    if not has_assert:
        return "缺断言（无法自动判定）"
    if not has_asset:
        return "缺脚本"
    if verdict in FINAL:
        return "已出结论"
    if verdict == "NOT_RUN":
        return "有脚本未执行"
    if verdict == "NOT_EXECUTED":
        return "有断言但编译零检查"
    return "执行侧问题"


def style_header(ws, row: int, ncol: int) -> None:
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEAD_FILL
        cell.font = HEAD_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER


def autosize(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


wb = Workbook()

# ---------------- Sheet 1 汇总 ----------------
ws = wb.active
ws.title = "汇总"
ws["A1"] = "人工编写用例自动化可行性评估（美心-HK，2026-10-17）"
ws["A1"].font = TITLE_FONT
ws["A2"] = "口径：custom_fields.content_source in ('human_xlsx','self_defined_real_write')，共 %d 条" % cov["human_total"]
ws["A2"].font = Font(size=9, color="595959")

rows = [
    ["模块", "人工用例", "有断言", "有脚本", "已出结论", "覆盖率", "缺断言", "有断言未执行"],
]
by_mod: dict[str, Counter] = defaultdict(Counter)
for cid, r in cov_rows.items():
    mod = r["module"]
    mod = ("店铺" if "店鋪" in mod or "店铺" in mod else
           "商品" if "商品" in mod else
           "支付方式" if "支付" in mod else "通用")
    by_mod[mod]["total"] += 1
    by_mod[mod]["assert"] += 1 if r["assertion_cnt"] > 0 else 0
    by_mod[mod]["asset"] += 1 if r["has_asset"] else 0
    by_mod[mod]["final"] += 1 if r["verdict"] in FINAL else 0
    if r["assertion_cnt"] == 0:
        by_mod[mod]["no_assert"] += 1
    elif r["verdict"] == "NOT_RUN":
        by_mod[mod]["assert_notrun"] += 1

order = ["店铺", "商品", "支付方式", "通用"]
tot = Counter()
for mod in order:
    c = by_mod[mod]
    rows.append([mod, c["total"], c["assert"], c["asset"], c["final"],
                 f"{c['final'] / c['total']:.1%}", c["no_assert"], c["assert_notrun"]])
    tot.update(c)
rows.append(["合计", tot["total"], tot["assert"], tot["asset"], tot["final"],
             f"{tot['final'] / tot['total']:.1%}", tot["no_assert"], tot["assert_notrun"]])

start = 4
for i, r in enumerate(rows):
    for j, v in enumerate(r, start=1):
        cell = ws.cell(row=start + i, column=j, value=v)
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center" if j > 1 else "left")
        if i == 0:
            cell.fill = HEAD_FILL
            cell.font = HEAD_FONT
        if i == len(rows) - 1:
            cell.font = Font(bold=True)

note_row = start + len(rows) + 2
ws.cell(row=note_row, column=1, value="关键结论").font = TITLE_FONT
notes = [
    "1. 385 条人工用例中只有 73 条带结构化断言（19%），这是现在唯一能自动执行的部分。",
    "2. 已出结论 21 条，全部在商品/支付两模块；店铺 33 条、通用 11 条有断言有脚本，但从未执行。",
    "3. 312 条无断言用例分档：A 字段级 20 条 / B 流程级 261 条（导入 143、列表 72、提交 42、上传 4）/ C 权限 8 条 / D 待判 23 条。",
    "4. 补断言不等于能执行：字段名必须命中子功能 field_locators（商品仅配 6 个字段、支付仅配 3 个）。",
    "5. 历史 web_test_runs 的 completed/passed 记录无对应执行作业与 stdout，不能作为覆盖证据。",
    "6. 已执行的 PASSED 含义是「该用例那 1 个字段规则通过」，不是整条用例业务流程通过。",
]
for i, n in enumerate(notes):
    ws.cell(row=note_row + 1 + i, column=1, value=n).font = Font(size=9)
autosize(ws, [16, 12, 10, 10, 12, 10, 10, 14])

# ---------------- Sheet 2 用例明细 ----------------
ws2 = wb.create_sheet("用例明细")
headers = ["用例编号", "模块", "用例名称", "优先级", "有断言", "断言类型",
           "有脚本", "执行结论", "检查通过", "状态归类", "自动化档位", "缺什么"]
ws2.append(headers)
style_header(ws2, 1, len(headers))

for cid, r in sorted(cov_rows.items(), key=lambda kv: (kv[1]["module"], kv[0])):
    t = tier_map.get(cid)
    mod = ("店铺" if "店鋪" in r["module"] or "店铺" in r["module"] else
           "商品" if "商品" in r["module"] else
           "支付方式" if "支付" in r["module"] else "通用")
    has_assert = r["assertion_cnt"] > 0
    ws2.append([
        cid, mod, r["name"], r["priority"],
        "是" if has_assert else "否",
        r["rule_type"] or ("字段级" if has_assert else "—"),
        "是" if r["has_asset"] else "否",
        verdict_label(r["verdict"]),
        "",  # 由执行结果补
        status_label(has_assert, r["has_asset"], r["verdict"]),
        t["tier"] if t else "—",
        t["why"] if t else "—",
    ])

for row in ws2.iter_rows(min_row=2, max_row=ws2.max_row, max_col=len(headers)):
    for cell in row:
        cell.border = BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=False)
autosize(ws2, [12, 10, 46, 10, 8, 14, 8, 14, 10, 20, 12, 16])
ws2.freeze_panes = "A2"
ws2.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws2.max_row}"

# ---------------- Sheet 3 可立即执行 ----------------
ws3 = wb.create_sheet("可立即执行")
ws3["A1"] = "已有断言 + 已有脚本、只差执行的人工用例（建议优先跑）"
ws3["A1"].font = TITLE_FONT
ready = [cid for cid, r in cov_rows.items()
         if r["assertion_cnt"] > 0 and r["has_asset"] and r["verdict"] in ("NOT_RUN", "TIMEOUT", "ERROR")]
h3 = ["用例编号", "模块", "用例名称", "优先级", "当前状态"]
ws3.append([])
ws3.append(h3)
style_header(ws3, 3, len(h3))
for cid in sorted(ready):
    r = cov_rows[cid]
    mod = ("店铺" if "店鋪" in r["module"] or "店铺" in r["module"] else
           "商品" if "商品" in r["module"] else
           "支付方式" if "支付" in r["module"] else "通用")
    ws3.append([cid, mod, r["name"], r["priority"], verdict_label(r["verdict"])])
for row in ws3.iter_rows(min_row=4, max_row=ws3.max_row, max_col=len(h3)):
    for cell in row:
        cell.border = BORDER
ws3.cell(row=ws3.max_row + 2, column=1,
         value=f"合计 {len(ready)} 条：店铺 32 + 通用 11 条从未执行，商品/支付 3 条执行异常。").font = Font(bold=True)
autosize(ws3, [12, 10, 46, 10, 14])

# ---------------- Sheet 4 无断言分档 ----------------
ws4 = wb.create_sheet("缺断言分档")
ws4["A1"] = "312 条无断言用例：按「缺什么能力」分档"
ws4["A1"].font = TITLE_FONT
tier_rows = [r for r in tier["rows"]]
h4 = ["档位", "缺什么", "模块", "条数", "现有能力"]
ws4.append([])
ws4.append(h4)
style_header(ws4, 3, len(h4))

cap = {
    "A-字段级": "有生成器（补断言即可）",
    "B-流程级": "无生成器，需建设",
    "C-需人工": "无多账号体系",
    "D-待判": "需人工判读",
}
grp: dict[tuple[str, str, str], int] = Counter()
for r in tier_rows:
    grp[(r["tier"], r["why"], r["module"])] += 1
for (t, why, mod), n in sorted(grp.items()):
    ws4.append([t, why, mod, n, cap.get(t, "")])
for row in ws4.iter_rows(min_row=4, max_row=ws4.max_row, max_col=len(h4)):
    for cell in row:
        cell.border = BORDER
tot4 = ws4.max_row + 2
ws4.cell(row=tot4, column=1, value="合计").font = Font(bold=True)
ws4.cell(row=tot4, column=4, value=len(tier_rows)).font = Font(bold=True)
ws4.cell(row=tot4 + 1, column=1,
         value="注：A 类 20 条中仅 10 条字段已配定位器，其余需先补 field_locators。"
         ).font = Font(size=9, color="C00000")
autosize(ws4, [12, 18, 10, 8, 26])

out = ROOT / "人工用例自动化可行性-20261017.xlsx"
wb.save(out)
print(f"[saved] {out}")
print(f"明细 {ws2.max_row-1} 行；可立即执行 {len(ready)} 条；缺断言分档 {len(tier_rows)} 条")
