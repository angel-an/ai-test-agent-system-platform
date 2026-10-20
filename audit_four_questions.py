"""四问审计（只读）：需求直生成 vs 人工导入 —— 结构完备度/覆盖/脚本化/执行/重叠。

Q1 需求直生成：准确度(可追溯/断言来源) 覆盖度(PRD分节/模块) 可脚本执行度 人工可执行度(steps/expected)
Q2 人工导入：脚本化覆盖度 执行度
Q3 双路：功能点交集(冗余) vs 差集(互补)
"""
import json
import re
import sys
from collections import Counter, defaultdict

import psycopg2

PROJ = "942226d9-6071-43fa-b2bb-f7cbc93a0028"
PRD_MODS = ("店鋪主數據管理", "商品主數據", "支付方式主數據")

c = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                     password="postgres", dbname="langgraph_platform_db")
c.set_session(autocommit=True)
cur = c.cursor()

# ---------- 取出两族全量用例 ----------
cur.execute("""
select t.id::text, t.identifier, t.name, t.module, t.priority::text, t.state::text,
       coalesce(t.preconditions,''), coalesce(t.description,''), coalesce(t.scenario,''),
       t.custom_fields
from test_cases t
where t.project_id = %s
  and (t.module = any(%s) or t.module like 'YL-主數據管理%%')
""", (PROJ, list(PRD_MODS)))
rows = cur.fetchall()

cases = []
for (cid, ident, name, module, prio, state, pre, desc, scen, cf) in rows:
    cf = cf or {}
    fam = "prd" if module in PRD_MODS else "human"
    assertions = cf.get("assertions") or []
    steps = cf.get("test_steps") or cf.get("steps")
    exp = cf.get("expected_result") or cf.get("expected")
    cases.append(dict(
        id=cid, ident=ident, name=name or "", module=module, fam=fam, prio=prio,
        pre_len=len(pre.strip()), desc=desc, desc_len=len(desc.strip()),
        scen_len=len((scen or "").strip()),
        has_steps=bool(steps) and len(json.dumps(steps, ensure_ascii=False)) > 10,
        has_expected=bool(exp and str(exp).strip()),
        n_assert=len(assertions) if isinstance(assertions, list) else 0,
        assert_types=[a.get("kind") for a in assertions if isinstance(a, dict)] if isinstance(assertions, list) else [],
        assert_source=cf.get("assertions_source"),
        has_rule=bool(cf.get("requirement_rule_id")),
        has_rule_text=bool(cf.get("requirement_rule")),
        sub_fn=cf.get("sub_function_id"),
        web_test_id=cf.get("web_test_id"),
        cf_keys=list(cf.keys()),
    ))
by_id = {x["id"]: x for x in cases}

# ---------- 归集/脚本/执行 ----------
cur.execute("""
select w.test_case_id::text, w.identifier, w.script_path,
       w.test_config->>'web_template_key',
       (w.test_config->>'form_executable_checks')::int,
       (w.test_config->>'pending_locator')::int
from web_tests w
where w.project_id = %s
""", (PROJ,))
wt = {}
for cid, wid, sp, tpl, fe, pl in cur.fetchall():
    wt[cid] = dict(wid=wid, script=sp, tpl=tpl, form_exec=fe, pending=pl)

# 执行覆盖（test_run_test_cases）
cur.execute("""
select rtc.test_case_id::text, count(distinct r.id), max(rtc.latest_status::text)
from test_run_test_cases rtc join test_runs r on r.id = rtc.test_run_id
where r.project_id = %s
group by 1
""", (PROJ,))
run_cov = {r[0]: dict(runs=r[1], last_status=r[2]) for r in cur.fetchall()}

# ---------- 结构完备度 ----------
def pct(a, b):
    return f"{a}/{b} ({a*100/b:.1f}%)" if b else "0/0"

out = {"run_at": "2026-10-17", "families": {}}

for fam in ("prd", "human"):
    xs = [x for x in cases if x["fam"] == fam]
    n = len(xs)
    d = {
        "total": n,
        "has_description_gt50": sum(1 for x in xs if x["desc_len"] > 50),
        "has_preconditions": sum(1 for x in xs if x["pre_len"] > 0),
        "has_steps": sum(1 for x in xs if x["has_steps"]),
        "has_expected": sum(1 for x in xs if x["has_expected"]),
        "has_rule_id": sum(1 for x in xs if x["has_rule"]),
        "has_assertions": sum(1 for x in xs if x["n_assert"] > 0),
        "assert_total": sum(x["n_assert"] for x in xs),
        "bound_subfn": sum(1 for x in xs if x["sub_fn"]),
        "has_web_asset": sum(1 for x in xs if x["id"] in wt),
        "has_compiled_assert": sum(1 for x in xs if wt.get(x["id"]) and (wt[x["id"]]["form_exec"] or 0) > 0),
        "executed": sum(1 for x in xs if run_cov.get(x["id"], {}).get("runs", 0) > 0),
        "assert_type_dist": dict(Counter(t for x in xs for t in x["assert_types"] if t)),
        "assert_source_dist": dict(Counter(str(x["assert_source"]) for x in xs)),
        "module_dist": dict(Counter(x["module"] for x in xs)),
    }
    d["pct"] = {
        "steps": pct(d["has_steps"], n),
        "expected": pct(d["has_expected"], n),
        "assertions": pct(d["has_assertions"], n),
        "bound_subfn": pct(d["bound_subfn"], n),
        "web_asset": pct(d["has_web_asset"], n),
        "compiled_assert": pct(d["has_compiled_assert"], n),
        "executed": pct(d["executed"], n),
    }
    out["families"][fam] = d

# ---------- 功能点分类（用于 Q3 冗余/互补） ----------
FEATURES = {
    "新建/创建": r"新建|創建|创建|新增|create",
    "编辑/更新": r"編輯|编辑|更新|修改|edit|update",
    "删除": r"刪除|删除|delete",
    "必填校验": r"必填|必輸|required|空值|為空|为空",
    "长度/字符类型": r"長度|长度|字元|字符類型|字符类型|最大|邊界|边界|max_length|特殊字元|特殊字符",
    "唯一性/重复": r"唯一|重複|重复|duplicate|已存在",
    "默认值": r"默認|默认|default|預設|预设",
    "显示/可见": r"顯示|显示|可見|可见|隱藏|隐藏|visibility",
    "联动": r"聯動|联动|切換|切换|帶出|带出|顯示欄位|显示字段",
    "筛选/查询": r"篩選|筛选|查詢|查询|搜索|搜尋",
    "分页/排序": r"分頁|分页|排序|pagination|sort",
    "导入": r"導入|导入|import|上傳|上传",
    "导出": r"導出|导出|export|下載|下载",
    "权限": r"權限|权限|角色|permission|無權",
    "操作日志": r"操作日誌|操作日志|審計|audit|log",
    "列表字段": r"列表|欄位|栏位|列顯示",
    "保存/提交": r"保存|儲存|提交|save|submit",
    "图片/文件": r"圖片|图片|文件|檔案|附件|image|file",
}

feat = defaultdict(lambda: defaultdict(int))
feat_ids = defaultdict(lambda: defaultdict(list))
BOILER = re.compile(r"基于上传需求文档[^。]*。")
for x in cases:
    clean_desc = BOILER.sub("", x["desc"])
    text = f"{x['name']} {clean_desc[:400]}"
    hit = False
    for k, pat in FEATURES.items():
        if re.search(pat, text, re.I):
            feat[x["fam"]][k] += 1
            feat_ids[x["fam"]][k].append(x["ident"])
            hit = True
    if not hit:
        feat[x["fam"]]["(未归类)"] += 1

out["feature_matrix"] = {k: {"prd": feat["prd"][k], "human": feat["human"][k],
                             "both": feat["prd"][k] > 0 and feat["human"][k] > 0}
                         for k in sorted(set(feat["prd"]) | set(feat["human"]))}

# ---------- 精确重名 ----------
name_c = Counter((x["fam"], re.sub(r"\s+", "", x["name"])) for x in cases)
out["exact_dup_names"] = {
    "prd_internal": sum(v - 1 for (f, _), v in name_c.items() if f == "prd" and v > 1),
    "human_internal": sum(v - 1 for (f, _), v in name_c.items() if f == "human" and v > 1),
    "cross_family": len({nm for (f, nm), v in name_c.items() if f == "prd"} &
                        {nm for (f, nm), v in name_c.items() if f == "human"}),
}

# ---------- 字段级交叉（PRD desc 里的"字段：X" vs 人工用例名/描述命中） ----------
def prd_fields(x):
    m = re.findall(r"字段[:：]\s*([^；;\n（(]{1,30})", x["desc"])
    return [s.strip() for s in m]

pf = Counter()
for x in cases:
    if x["fam"] == "prd":
        for f in prd_fields(x):
            pf[f] += 1
out["prd_field_top"] = dict(pf.most_common(25))
out["prd_cases_with_field_mention"] = sum(1 for x in cases if x["fam"] == "prd" and prd_fields(x))

# 人工用例中同字段被覆盖的次数
human_text = [(x["ident"], f"{x['name']} {x['desc'][:300]}") for x in cases if x["fam"] == "human"]
field_both = {}
for f, n in pf.most_common(40):
    key = f.replace(" ", "")
    hh = [i for i, t in human_text if key and key in t.replace(" ", "")]
    if hh:
        field_both[f] = {"prd": n, "human": len(hh), "human_samples": hh[:3]}
out["field_overlap"] = field_both

# ---------- 语义-断言一致性（准确度代理指标） ----------
SEM = [
    ("必填/空值", r"必填|必輸|为空|為空|不填", {"required", "required_error"}),
    ("边界/长度", r"边界|邊界|最长|最長|字元|长度|長度|字符", {"max_length", "boundary", "min_length", "pattern"}),
    ("唯一/重复", r"唯一|重複|重复|已存在", {"unique", "duplicate"}),
    ("默认值", r"默認|默认|預設|预设", {"default", "default_selected"}),
    ("取值/枚举", r"取值|枚舉|枚举|下拉|選項|选项", {"enum", "select_options", "radio_options"}),
    ("显示/联动", r"顯示|显示|可見|可见|聯動|联动|切換|切换", {"visibility"}),
    ("格式/字符类型", r"格式|字符類型|字符类型|非法", {"pattern", "max_length", "boundary"}),
]
acc = []
for x in cases:
    if x["fam"] != "prd":
        continue
    kinds = set(x["assert_types"])
    for label, pat, expect in SEM:
        if re.search(pat, x["name"]):
            if not kinds:
                acc.append({"ident": x["ident"], "name": x["name"][:60], "sem": label,
                            "kinds": [], "verdict": "无断言"})
            elif kinds & expect:
                acc.append({"ident": x["ident"], "name": x["name"][:60], "sem": label,
                            "kinds": sorted(kinds), "verdict": "匹配"})
            else:
                acc.append({"ident": x["ident"], "name": x["name"][:60], "sem": label,
                            "kinds": sorted(kinds), "verdict": "错配"})
            break
out["semantic_consistency"] = dict(Counter(a["verdict"] for a in acc))
out["semantic_mismatch_samples"] = [a for a in acc if a["verdict"] == "错配"][:15]
out["semantic_checked"] = len(acc)

# ---------- 编译产物断言（读脚本文件） ----------
sys.path.insert(0, r"D:/code/Pyproject/ai-test-agent-system-platform/backend")
from app.config.settings import settings  # noqa: E402
import os  # noqa: E402

BACKUP = os.path.join(settings.web_cli_workspace_root, "artifacts_backup")
comp = defaultdict(lambda: Counter())
for x in cases:
    w = wt.get(x["id"])
    if not w or not w["script"]:
        comp[x["fam"]]["no_script"] += 1
        continue
    p = os.path.join(BACKUP, w["script"])
    if not os.path.isfile(p):
        comp[x["fam"]]["file_missing"] += 1
        continue
    txt = open(p, encoding="utf-8", errors="ignore").read()
    n = txt.count("COMPILED_ASSERTION ")
    comp[x["fam"]]["assert_0" if n == 0 else ("assert_1_2" if n <= 2 else "assert_3plus")] += 1
out["compiled_buckets"] = {k: dict(v) for k, v in comp.items()}


print("== Q1/Q2 结构完备度 ==")
for fam in ("prd", "human"):
    d = out["families"][fam]
    print(f"\n[{fam}] total={d['total']}")
    for k, v in d["pct"].items():
        print(f"   {k:16s} {v}")
    print("   assert_types:", d["assert_type_dist"])
    print("   modules:", d["module_dist"])

print("\n== Q3 功能点矩阵 ==")
for k, v in out["feature_matrix"].items():
    flag = "← 双路重叠" if v["both"] else ("仅PRD" if v["prd"] else "仅人工")
    print(f"   {k:12s} prd={v['prd']:4d} human={v['human']:4d}  {flag}")

print("\n== 编译产物断言分桶 ==")
for k, v in out["compiled_buckets"].items():
    print(f"   {k}: {v}")

print("\n== 语义-断言一致性（PRD 用例） ==")
print("   ", out["semantic_consistency"], " checked=", out["semantic_checked"])
for a in out["semantic_mismatch_samples"][:8]:
    print(f"    X {a['ident']} [{a['sem']}] kinds={a['kinds']} :: {a['name']}")

print("\n== 重名 ==", out["exact_dup_names"])
print("== PRD 字段覆盖 top ==")
for k, v in list(out["prd_field_top"].items())[:15]:
    print(f"   {k}: {v}")
print("== 字段双路交集（前10） ==")
for k, v in list(out["field_overlap"].items())[:10]:
    print(f"   {k}: prd={v['prd']} human={v['human']}")

json.dump(out, open("out_yl_case_audit/four_questions_20261017.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nsaved -> out_yl_case_audit/four_questions_20261017.json")
