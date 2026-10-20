# -*- coding: utf-8 -*-
"""只读：查清 (1) 44 条 NOT_RUN 的脚本来源  (2) 309 条无脚本用例的可自动化条件"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
DSN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres",
           dbname="langgraph_platform_db")
HUMAN_SRC = ("human_xlsx", "self_defined_real_write")
ROOT = Path(__file__).resolve().parent


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


conn = psycopg2.connect(**DSN)
cur = conn.cursor()

# 1. 所有人工用例 + 其资产
cur.execute("""
    select t.identifier, t.module, t.name, t.priority::text,
           coalesce(t.custom_fields->>'content_source',''),
           coalesce(t.description,''),
           coalesce(t.preconditions,''),
           coalesce(t.custom_fields::text,'{}')
      from test_cases t
     where t.project_id = %s and t.custom_fields->>'content_source' = any(%s)
     order by t.module, t.identifier
""", (PROJ, list(HUMAN_SRC)))
human = cur.fetchall()

cur.execute("""
    select t.identifier, w.id::text, w.identifier, w.generated_by_agent,
           coalesce((w.generation_params->'compiled_cases'->>'assertion_count')::int, 0),
           coalesce(w.generation_params->>'navigation_path',''),
           w.updated_at
      from web_tests w join test_cases t on t.id = w.test_case_id
     where w.project_id = %s and t.custom_fields->>'content_source' = any(%s)
""", (PROJ, list(HUMAN_SRC)))
assets = defaultdict(list)
for r in cur.fetchall():
    assets[r[0]].append(r)

print("=" * 100)
print("1. 有脚本用例的资产来源（谁生成的、有没有编译产物）")
print("=" * 100)
buckets: dict[tuple, int] = Counter()
for cid, mod, name, prio, src, desc, pre, cf in human:
    for a in assets.get(cid, []):
        buckets[(fam(mod), a[3], "有编译产物" if a[4] > 0 else "无编译产物")] += 1
for k, v in sorted(buckets.items()):
    print(f"  {k[0]:<8}{k[1]:<24}{k[2]:<12}{v:>4}")

print("\n  从未执行过的用例，其资产明细（前 12 条）：")
cnt = 0
for cid, mod, name, prio, src, desc, pre, cf in human:
    if not assets.get(cid):
        continue
    if cnt >= 12:
        break
    flag = [a for a in assets[cid] if a[3] != "web_case_link_service"]
    if not flag:
        continue
    for a in assets[cid]:
        print(f"    {fam(mod):<8}{cid:<10}asset={a[2]:<14}gen={a[3]:<24}"
              f"assertion={a[4]:<3}{name[:30]}")
    cnt += 1

# 2. 无脚本用例：描述能否解析出「字段 + 规则」
print()
print("=" * 100)
print("2. 无脚本用例（309 条）的可自动化条件")
print("=" * 100)
no_asset = [r for r in human if not assets.get(r[0])]
print(f"  总数 {len(no_asset)}，模块分布 {dict(Counter(fam(r[1]) for r in no_asset))}")
print(f"  有 custom_fields 键的：{sum(1 for r in no_asset if r[7] not in ('{}', ''))} 条")
key_cnt = Counter()
for r in no_asset:
    try:
        cf = json.loads(r[7])
    except json.JSONDecodeError:
        cf = {}
    for k in cf:
        key_cnt[k] += 1
print(f"  custom_fields 键分布（前 10）：{dict(key_cnt.most_common(10))}")

# 描述里是否含「字段名 + 规则关键词」模式
rule_kw = {
    "必填": ("不輸入", "為空", "必填", "提示必填"),
    "长度/边界": ("字元", "個字", "邊界", "長度"),
    "字符类型": ("中文", "特殊字元", "數字", "字母", "大小寫"),
    "默认值": ("默認", "默認開啟", "默認關閉"),
    "下拉/选项": ("下拉", "選擇", "選項"),
    "上传": ("上傳", "圖片"),
    "列表/落库": ("落庫", "列表可見", "導入", "導出"),
    "权限/流程": ("權限", "審批", "提交", "保存"),
}
pattern = Counter()
samples = defaultdict(list)
for cid, mod, name, prio, src, desc, pre, cf in no_asset:
    hit = [k for k, kws in rule_kw.items() if any(w in name or w in desc for w in kws)]
    pattern[tuple(hit)] += 1
    for k in hit:
        if len(samples[k]) < 2:
            samples[k].append((cid, name, desc[:110].replace("\n", " ")))
print("\n  按可识别规则类型分组（一条用例可命中多类）：")
for k in rule_kw:
    n = sum(v for kk, v in pattern.items() if k in kk)
    print(f"    {k:<10}{n:>4} 条")

print("\n  描述样例：")
for k, items in samples.items():
    print(f"  -- {k}")
    for cid, name, desc in items:
        print(f"     {cid} {name[:34]}")
        print(f"        desc: {desc}")

# 3. 通用模块是什么
print()
print("=" * 100)
print("3. 「YL-主數據管理-通用」137 条是什么")
print("=" * 100)
cur.execute("""
    select identifier, name, left(coalesce(description,''),120)
      from test_cases
     where project_id=%s and module='YL-主數據管理-通用'
       and custom_fields->>'content_source'='human_xlsx'
     order by identifier limit 8
""", (PROJ,))
for r in cur.fetchall():
    print(f"  {r[0]} {r[1][:44]}")
    print(f"      {r[2].replace(chr(10),' ')}")

conn.close()
