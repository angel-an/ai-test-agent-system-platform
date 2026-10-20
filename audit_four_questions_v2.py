"""四问审计 v2（只读）：按内容来源正确分族，量化 Q1/Q2/Q3。

族定义（YL 三模块范围）：
  HUMAN   : custom_fields.content_source = 'human_xlsx'
  PRD     : module_source in (prd_section, prd_section_evidence) 或 description 以「基于上传需求文档」开头
  OTHER   : 其余
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
SCOPE = ("(module in ('店鋪主數據管理','商品主數據','支付方式主數據') "
         "or module like 'YL-主數據管理%%' or module like 'YL-主数据管理%%')")

c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                     password="postgres", dbname="langgraph_platform_db")
c.set_session(autocommit=True)
cur = c.cursor()

cur.execute(f"""
select t.id::text, t.identifier, t.name, t.module, coalesce(t.preconditions,''),
       coalesce(t.description,''), t.custom_fields
from test_cases t where t.project_id = %s and {SCOPE}
""", (PROJ,))

cases = []
for cid, ident, name, module, pre, desc, cf in cur.fetchall():
    cf = cf or {}
    if cf.get("content_source") == "human_xlsx":
        fam = "HUMAN"
    elif cf.get("module_source") in ("prd_section", "prd_section_evidence") or desc.startswith("基于上传需求文档"):
        fam = "PRD"
    else:
        fam = "OTHER"
    a = cf.get("assertions") or []
    cases.append(dict(
        id=cid, ident=ident, name=name or "", module=module, fam=fam,
        pre=pre.strip(), desc=desc,
        steps=cf.get("test_steps"), expected=cf.get("expected_result"),
        asserts=a if isinstance(a, list) else [],
        bound=bool(cf.get("sub_function_id")),
        rule_id=cf.get("requirement_rule_id"),
        rule_txt=cf.get("requirement_rule"),
        src=cf.get("module_source") or cf.get("content_source"),
    ))

cur.execute("select test_case_id::text, identifier, script_path from web_tests where project_id=%s", (PROJ,))
wt = {r[0]: dict(wid=r[1], script=r[2]) for r in cur.fetchall()}

cur.execute("""
select rtc.test_case_id::text, count(distinct r.id)
from test_run_test_cases rtc join test_runs r on r.id = rtc.test_run_id
where r.project_id=%s group by 1
""", (PROJ,))
runs = {r[0]: r[1] for r in cur.fetchall()}

sys.path.insert(0, r"D:/code/Pyproject/ai-test-agent-system-platform/backend")
from app.config.settings import settings  # noqa: E402

BACKUP = os.path.join(settings.web_cli_workspace_root, "artifacts_backup")
KIND = defaultdict(Counter)
for x in cases:
    x["n_assert"] = len(x["asserts"])
    x["kinds"] = [a.get("kind") for a in x["asserts"] if isinstance(a, dict)]
    x["fields"] = [a.get("field") for a in x["asserts"] if isinstance(a, dict) and a.get("field")]
    w = wt.get(x["id"])
    x["has_asset"] = bool(w)
    if w and w["script"]:
        p = os.path.join(BACKUP, w["script"])
        if os.path.isfile(p):
            txt = open(p, encoding="utf-8", errors="ignore").read()
            n = txt.count("COMPILED_ASSERTION ")
            x["compiled_n"] = n
            KIND[x["fam"]]["a0" if n == 0 else ("a1_2" if n <= 2 else "a3+")] += 1
        else:
            x["compiled_n"] = None
    else:
        x["compiled_n"] = None
    x["runs"] = runs.get(x["id"], 0)

out = {"run_at": "2026-10-17", "scope_total": len(cases), "families": {}}


def pct(a, b):
    return f"{a}/{b} ({a * 100.0 / b:.1f}%)" if b else "0/0"


for fam in ("PRD", "HUMAN", "OTHER"):
    xs = [x for x in cases if x["fam"] == fam]
    n = len(xs)
    d = dict(
        total=n,
        module_dist=dict(Counter(x["module"] for x in xs)),
        pre=pct(sum(1 for x in xs if x["pre"]), n),
        steps=pct(sum(1 for x in xs if x["steps"]), n),
        expected=pct(sum(1 for x in xs if x["expected"]), n),
        rule=pct(sum(1 for x in xs if x["rule_id"]), n),
        has_assert=pct(sum(1 for x in xs if x["n_assert"]), n),
        assert_total=sum(x["n_assert"] for x in xs),
        bound=pct(sum(1 for x in xs if x["bound"]), n),
        asset=pct(sum(1 for x in xs if x["has_asset"]), n),
        executed=pct(sum(1 for x in xs if x["runs"]), n),
        kind_dist=dict(Counter(k for x in xs for k in x["kinds"] if k)),
        top_fields=Counter(f for x in xs for f in x["fields"]).most_common(15),
    )
    out["families"][fam] = d
out["compiled_buckets"] = {k: dict(v) for k, v in KIND.items()}

# ---- 语义-断言一致性（PRD 族） ----
SEM = [
    ("必填/空值", r"必填|必輸|为空|為空|不填", {"required", "required_error"}),
    ("边界/长度", r"边界|邊界|最长|最長|字元|长度|長度|字符", {"max_length", "boundary", "min_length", "pattern"}),
    ("唯一/重复", r"唯一|重複|重复|已存在", {"unique", "duplicate"}),
    ("默认值", r"默認|默认|預設|预设", {"default", "default_selected"}),
    ("取值/枚举", r"取值|枚舉|枚举|下拉|選項|选项", {"enum", "select_options", "radio_options"}),
    ("显示/联动", r"顯示|显示|可見|可见|聯動|联动|切換|切换", {"visibility"}),
    ("格式/字符类型", r"格式|字符類型|字符类型|非法", {"pattern", "max_length", "boundary"}),
]
verdict = Counter()
mismatch = []
for x in cases:
    if x["fam"] != "PRD":
        continue
    kinds = set(x["kinds"])
    for label, pat, expect in SEM:
        if re.search(pat, x["name"]):
            v = "无断言" if not kinds else ("匹配" if kinds & expect else "错配")
            verdict[v] += 1
            if v == "错配" and len(mismatch) < 20:
                mismatch.append({"ident": x["ident"], "sem": label,
                                 "kinds": sorted(kinds), "name": x["name"][:70]})
            break
out["semantic"] = dict(verdict)
out["semantic_mismatch"] = mismatch

# ---- Q3：字段×断言类型 覆盖集合 ----
def fset(fam):
    s = Counter()
    for x in cases:
        if x["fam"] != fam:
            continue
        for a in x["asserts"]:
            if isinstance(a, dict) and a.get("field") and a.get("kind"):
                s[(str(a["field"]).strip(), a["kind"])] += 1
    return s


ps, hs = fset("PRD"), fset("HUMAN")
inter = set(ps) & set(hs)
out["redundancy"] = {
    "prd_field_kind_pairs": len(ps),
    "human_field_kind_pairs": len(hs),
    "intersection_pairs": len(inter),
    "prd_only": len(set(ps) - set(hs)),
    "human_only": len(set(hs) - set(ps)),
    "overlap_ratio_vs_human": round(len(inter) * 100.0 / len(hs), 1) if hs else None,
    "sample_overlap": sorted(f"{f}·{k}" for f, k in list(inter))[:20],
    "sample_human_only": sorted(f"{f}·{k}" for f, k in list(set(hs) - set(ps)))[:15],
    "sample_prd_only": sorted(f"{f}·{k}" for f, k in list(set(ps) - set(hs)))[:15],
}

# ---- 名称重复 ----
nm = Counter(re.sub(r"\s+", "", x["name"]) for x in cases if x["fam"] == "PRD")
out["prd_dup_names"] = {"distinct": len(nm), "dup_groups": sum(1 for v in nm.values() if v > 1),
                        "redundant_rows": sum(v - 1 for v in nm.values() if v > 1),
                        "top": nm.most_common(10)}

print("== 族规模 ==", {k: v["total"] for k, v in out["families"].items()}, "scope=", len(cases))
for fam in ("PRD", "HUMAN", "OTHER"):
    d = out["families"][fam]
    print(f"\n[{fam}] n={d['total']}")
    for k in ("pre", "steps", "expected", "rule", "has_assert", "bound", "asset", "executed"):
        print(f"   {k:10s} {d[k]}")
    print("   assert_total=", d["assert_total"], "kinds=", d["kind_dist"])
print("\n== 编译分桶 ==", out["compiled_buckets"])
print("\n== 语义一致性 ==", out["semantic"])
for m in out["semantic_mismatch"][:10]:
    print("   X", m["ident"], m["sem"], m["kinds"], m["name"])
print("\n== 冗余/互补（字段×断言类型） ==")
for k, v in out["redundancy"].items():
    if not str(k).startswith("sample"):
        print(f"   {k}: {v}")
print("   overlap:", out["redundancy"]["sample_overlap"][:10])
print("   human_only:", out["redundancy"]["sample_human_only"])
print("\n== PRD 重名 ==", {k: v for k, v in out["prd_dup_names"].items() if k != "top"})
print("   top:", out["prd_dup_names"]["top"][:6])

json.dump(out, open("out_yl_case_audit/four_questions_v2_20261017.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nsaved -> out_yl_case_audit/four_questions_v2_20261017.json")
