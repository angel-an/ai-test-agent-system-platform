# -*- coding: utf-8 -*-
"""断言级执行归因：把 test_results.description 里的逐条断言结果拆出来统计。

每条描述形如 `<check>:<field>=<status>（<原因>）; ...`，用例级 custom_fields 里有
{passed, assertions, unverified}。本脚本：
  - 统计断言级 status 分布（pass / unverified / fail ...）
  - 提取 unverified 的原因族（归一化后计数）
产出 out_yl_case_audit/I_assertion_attribution.json
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2

OUT = Path(__file__).parent / "out_yl_case_audit"
SFS = {
    "e1c94163-d221-4ac8-afdc-c2103f989e5f": "WSF-YL-STORE",
    "d90c2034-0ac2-4f12-91c7-a0be5f2f2103": "WSF-YL-GOODS",
    "af320aac-6d37-4cc9-b1d7-1213aa5eced4": "WSF-YL-PAY",
    "f04ac33d-eb21-477e-8b2c-944c571193bb": "WSF-YL-STORE-RW",
    "bb36f2e0-4a5b-408e-950a-8a34dc504e95": "WSF-YL-GOODS-RW",
    "b5b8569a-0238-42eb-9cc3-99c2e9c8a97c": "WSF-YL-PAY-RW",
}
# 原因归一化：把括号里的自由文本压成族
REASON_FAMILIES = [
    ("填值后清空仍未出现必填提示", "必填校验未在 UI 层出现（疑仅提交时校验）"),
    ("未找到提交按钮", "提交按钮定位失败（modal footer 与顶栏候选均空）⇒ 未能提交"),
    ("未定位到输入框", "输入框定位失败"),
    ("字段为只读选择器", "只读选择器无法经输入触发校验"),
    ("控件未保留输入", "控件未保留输入（失焦后为空）"),
    ("展开/取消下拉未触发必填提示", "下拉交互未触发必填提示"),
    ("下拉中不存在", "下拉中不存在该非法值 ⇒ 用户无法选中（按拒绝判定）"),
    ("仅提交时校验", "判定为仅提交时校验"),
]


def normalize(text: str) -> str:
    for pat, fam in REASON_FAMILIES:
        if pat in text:
            return fam
    if not text.strip():
        return "(无原因)"
    return text.strip()[:40]


def main() -> None:
    conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()
    cur.execute("""
        select tc.identifier, tc.custom_fields->>'sub_function_id' sf,
               tc.custom_fields->>'generated_from' gen,
               rtr.identifier, res.status, res.description, res.custom_fields
        from test_results res
        join test_runs rtr on rtr.id = res.test_run_id
        join test_cases tc on tc.id = res.test_case_id
        where tc.custom_fields->>'sub_function_id' = any(%s)
        order by rtr.identifier, tc.identifier
    """, (list(SFS),))
    rows = cur.fetchall()

    per = defaultdict(Counter)          # (sub_function, source) -> case status counts
    assert_stat = defaultdict(Counter)  # (sub_function, source) -> assertion status counts
    reasons = defaultdict(Counter)      # (sub_function, source) -> reason family counts
    ck_reasons = Counter()              # 全局 check 类型 × 原因
    for ident, sf, gen, tr, status, desc, cf in rows:
        key = (SFS.get(sf, str(sf)), gen or "(未标注)")
        per[key][str(status)] += 1
        cf = cf or {}
        assert_stat[key]["assertions"] += int(cf.get("assertions") or 0)
        assert_stat[key]["passed"] += int(cf.get("passed") or 0)
        assert_stat[key]["unverified"] += int(cf.get("unverified") or 0)
        for part in re.split(r";\s*", desc or ""):
            part = part.strip()
            if not part:
                continue
            # 形如 required_error:字段=unverified（原因）
            m = re.match(r"^([a-z_]+):(.*?)=(pass|unverified|fail|blocked|skipped)(?:（(.*)）)?$",
                         part, re.S)
            if not m:
                continue
            ck, field, st, why = m.group(1), m.group(2), m.group(3), m.group(4) or ""
            assert_stat[key][f"断言:{st}"] += 1
            if st != "pass":
                fam = normalize(why)
                reasons[key][fam] += 1
                ck_reasons[(ck, fam)] += 1

    out = {}
    for key in sorted(per):
        sf_name, gen = key
        d = dict(
            case_status=dict(per[key]),
            assertion_stats=dict(assert_stat[key]),
            unverified_reasons=dict(reasons[key].most_common(12)),
        )
        out.setdefault(sf_name, {})[gen] = d
    (OUT / "I_assertion_attribution.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("\n=== check 类型 × 未验证原因（全局）===")
    for (ck, fam), n in ck_reasons.most_common(20):
        print(f"  {n:4}  {ck:20} {fam}")


if __name__ == "__main__":
    main()
