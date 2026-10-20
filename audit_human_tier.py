# -*- coding: utf-8 -*-
"""只读：把 312 条无断言的人工用例按「能否自动化、缺什么」分档。

分档优先级（先命中先归类）：
  C-需人工  : 权限/多账号、异常注入、跨模块前置
  B-流程级  : 导入导出、列表/筛选/分页、新增保存删除落库、上传附件
  A-字段级  : 必填/长度/字符类型/边界/范围/默认值/下拉选项/开关/显隐
  D-待判    : 其余
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
SRC = ["human_xlsx", "self_defined_real_write"]
DSN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres",
           dbname="langgraph_platform_db")
ROOT = Path(__file__).resolve().parent

C_KW = ("權限", "許可權", "用户权限", "多账号", "帳號", "登錄", "登录", "會話", "同時操作", "併發")
B_IO = ("導入", "导出", "導出", "上傳Excel", "上传Excel", "上傳excel", "模板下載", "下載模板")
B_LIST = ("列表", "分頁", "分页", "篩選", "筛选", "搜索", "排序", "每頁", "查询结果")
B_WRITE = ("保存", "提交", "新增", "新建", "刪除", "删除", "編輯", "编辑", "落庫", "落库", "審批")
B_UPLOAD = ("上傳", "上傳圖片", "圖片", "附件", "文件")
A_FIELD = ("必填", "字元", "字元數", "長度", "长度", "邊界", "边界", "正整數", "正数",
           "默認", "默认", "下拉", "選項", "选项", "開關", "显示", "顯示", "隱藏", "隱藏")


def fam(module: str) -> str:
    if "店鋪" in module or "店铺" in module:
        return "店铺"
    if "商品" in module:
        return "商品"
    if "支付" in module:
        return "支付方式"
    if "通用" in module:
        return "通用"
    return module


def classify(name: str, steps: str) -> tuple[str, str]:
    text = f"{name} {steps}"
    if any(k in text for k in C_KW):
        return "C-需人工", "权限/账号/并发类"
    if any(k in text for k in B_IO):
        return "B-流程级", "导入导出流程"
    if any(k in text for k in B_LIST):
        return "B-流程级", "列表/筛选/分页"
    if any(k in text for k in B_UPLOAD):
        return "B-流程级", "附件上传"
    if any(k in text for k in B_WRITE):
        return "B-流程级", "提交/落库流程"
    if any(k in text for k in A_FIELD):
        return "A-字段级", "单字段规则"
    return "D-待判", "需人工看用例"


def assertion_hint(name: str, steps: str) -> str:
    """从标题推断可写的断言类型（只做可行性信号，不写库）"""
    text = f"{name} {steps}"
    hints = []
    if re.search(r"(超過|超过|最大長度|最大长度)\s*\d+\s*(個|个)?字元", text):
        hints.append("max_length:reject")
    if re.search(r"小於等於\s*\d+\s*(個)?字元|小于等于\s*\d+\s*(个)?字元", text):
        hints.append("max_length:accept")
    if re.search(r"\d+\s*(個)?字元(邊界|边界)值?", text):
        hints.append("boundary")
    if re.search(r"不輸入|为空|為空|空值", text):
        hints.append("required:reject")
    if re.search(r"中文|漢字|汉字|特殊字元|特殊字符", text):
        hints.append("charset:reject")
    if re.search(r"默認開啟|默认开启|默認關閉|默认关闭", text):
        hints.append("default")
    if re.search(r"下拉|選項|选项", text):
        hints.append("enum")
    if re.search(r"正整數|正整数|範圍內|范围内", text):
        hints.append("range/boundary:accept")
    return ",".join(hints) or "-"


conn = psycopg2.connect(**DSN)
cur = conn.cursor()
cur.execute("""
select t.identifier, t.module, t.name,
       coalesce(t.custom_fields->>'test_steps',''),
       coalesce(t.priority::text,''),
       exists(select 1 from web_tests w where w.test_case_id=t.id)
  from test_cases t
 where t.project_id=%s and t.custom_fields->>'content_source'=any(%s)
   and coalesce(jsonb_array_length(t.custom_fields->'assertions'),0)=0
 order by t.module, t.identifier
""", (PROJ, SRC))
rows = cur.fetchall()

print("=" * 100)
print("312 条无断言人工用例：自动化分档")
print("=" * 100)
tier = Counter()
tier_mod: dict[str, Counter] = {}
reason = Counter()
for ident, mod, name, steps, prio, has_asset in rows:
    t, why = classify(name, steps)
    tier[t] += 1
    tier_mod.setdefault(t, Counter())[fam(mod)] += 1
    reason[(t, why)] += 1

print(f"{'档位':<12}{'条数':>6}   模块分布")
for t in ("A-字段级", "B-流程级", "C-需人工", "D-待判"):
    print(f"{t:<12}{tier[t]:>6}   {dict(tier_mod.get(t, {}))}")
print(f"{'合计':<12}{sum(tier.values()):>6}")

print("\n细分原因：")
for (t, why), n in sorted(reason.items()):
    print(f"  {t:<12}{why:<20}{n:>4}")

print("\n" + "=" * 100)
print("A 类（字段级）：标题可直接推断的断言类型")
print("=" * 100)
a_rows = [(i, m, n, s) for i, m, n, s, p, h in rows if classify(n, s)[0] == "A-字段级"]
hint_cnt = Counter()
for ident, mod, name, steps in a_rows:
    h = assertion_hint(name, steps)
    for part in h.split(","):
        if part != "-":
            hint_cnt[part] += 1
    print(f"  {fam(mod):<8}{ident:<10}{h:<34}{name[:44]}")
print(f"\n  A 类共 {len(a_rows)} 条；可按标题直接推断断言的：{sum(1 for r in a_rows if assertion_hint(r[2], r[3]) != '-')} 条")
print(f"  推断类型分布：{dict(hint_cnt)}")

print("\n" + "=" * 100)
print("B 类（流程级）样例")
print("=" * 100)
b_rows = [(i, m, n, s) for i, m, n, s, p, h in rows if classify(n, s)[0] == "B-流程级"]
for ident, mod, name, steps in b_rows[:15]:
    print(f"  {fam(mod):<8}{ident:<10}{name[:48]}")

print("\n" + "=" * 100)
print("C 类（需人工）样例")
print("=" * 100)
c_rows = [(i, m, n, s) for i, m, n, s, p, h in rows if classify(n, s)[0] == "C-需人工"]
for ident, mod, name, steps in c_rows[:15]:
    print(f"  {fam(mod):<8}{ident:<10}{name[:48]}")

out = {
    "total": len(rows),
    "tier": dict(tier),
    "tier_by_module": {k: dict(v) for k, v in tier_mod.items()},
    "rows": [
        {"case": r[0], "module": fam(r[1]), "name": r[2], "priority": r[4],
         "has_asset": r[5], "tier": classify(r[2], r[3])[0],
         "why": classify(r[2], r[3])[1], "hint": assertion_hint(r[2], r[3])}
        for r in rows
    ],
}
p = ROOT / "out_yl_case_audit" / "human_feasibility_20261017.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n[saved] {p}")
conn.close()
