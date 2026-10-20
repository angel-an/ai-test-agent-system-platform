# -*- coding: utf-8 -*-
"""脚本化能力与断言注入率（修正版：去 JOIN 放大 + 用断言级指标）。

指标口径（重要）：
  - 每个用例只取**最新一个** web_tests（同用例多行是重复生成产物，不能累加）；
  - `assertion_count`         = 该用例声明并被编译的断言数（用例级）
  - `skipped_assertion_count` = 编译期被拒的断言数（如"本版不支持类型 enum"）
  - `form_skipped_no_locator` = 因无定位器被跳过的断言数
  - `form_executable_checks`  = **计划里的检查数，含子功能级共享检查（不是用例断言数）**
                                ⇒ 只能看每个用例的分布，不能跨用例求和
  - 断言可编译率 = (assertion_count - skipped_assertion_count - form_skipped_no_locator) / assertion_count
产出 F_script_quality.json / G_skipped_reasons.json / H_script_detail.json
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2

OUT = Path(__file__).parent / "out_yl_case_audit"
OUT.mkdir(exist_ok=True)

SFS = {
    "e1c94163-d221-4ac8-afdc-c2103f989e5f": "WSF-YL-STORE",
    "d90c2034-0ac2-4f12-91c7-a0be5f2f2103": "WSF-YL-GOODS",
    "af320aac-6d37-4cc9-b1d7-1213aa5eced4": "WSF-YL-PAY",
    "f04ac33d-eb21-477e-8b2c-944c571193bb": "WSF-YL-STORE-RW",
    "bb36f2e0-4a5b-408e-950a-8a34dc504e95": "WSF-YL-GOODS-RW",
    "b5b8569a-0238-42eb-9cc3-99c2e9c8a97c": "WSF-YL-PAY-RW",
}


def main() -> None:
    conn = psycopg2.connect(host="127.0.0.1", port=5432, user="postgres",
                            password="postgres", dbname="langgraph_platform_db")
    cur = conn.cursor()
    # 每个用例取最新一个 web_test（DISTINCT ON）
    cur.execute("""
        select tc.identifier, tc.custom_fields->>'sub_function_id' sf,
               tc.custom_fields->>'generated_from' gen,
               wt.identifier, wt.generation_params
        from test_cases tc
        left join lateral (
            select identifier, generation_params from web_tests w
            where w.test_case_id = tc.id
            order by w.created_at desc limit 1
        ) wt on true
        where tc.custom_fields->>'sub_function_id' = any(%s)
        order by tc.identifier
    """, (list(SFS),))
    rows = cur.fetchall()
    print("用例数（去重后）=", len(rows))

    agg = defaultdict(lambda: dict(
        total=0, scripted=0, no_script=0,
        assertion_total=0, skipped_assert_total=0, no_locator_total=0,
        cases_with_skipped=0, checks_dist=[], pending_confirmation=0))
    skipped_reasons = Counter()
    skipped_examples = defaultdict(list)
    detail = []

    for ident, sf, gen, wtid, gp in rows:
        sf_name = SFS.get(sf, str(sf))
        gen = gen or "(未标注)"
        a = agg[(sf_name, gen)]
        a["total"] += 1
        gp = gp or {}
        cc = gp.get("compiled_cases") or {}
        if gp.get("web_path_match_status") == "pending_confirmation":
            a["pending_confirmation"] += 1
        if wtid:
            a["scripted"] += 1
        else:
            a["no_script"] += 1
        n_ass = int(cc.get("assertion_count") or 0)
        n_sk = int(cc.get("skipped_assertion_count") or 0)
        n_nl = int(cc.get("form_skipped_no_locator") or 0)
        n_chk = int(cc.get("form_executable_checks") or 0)
        sk = cc.get("skipped_assertions") or []
        a["assertion_total"] += n_ass
        a["skipped_assert_total"] += n_sk
        a["no_locator_total"] += n_nl
        a["checks_dist"].append(n_chk)
        if sk:
            a["cases_with_skipped"] += 1
        for s in sk:
            r = str(s.get("reason") or "?")
            skipped_reasons[r] += 1
            if len(skipped_examples[r]) < 4:
                skipped_examples[r].append(f'{ident}·{s.get("field")}')
        detail.append(dict(
            identifier=ident, sub_function=sf_name, source=gen, web_test=wtid or "",
            assertion_count=n_ass, skipped_assertion_count=n_sk,
            form_skipped_no_locator=n_nl, form_executable_checks=n_chk,
            path_status=gp.get("web_path_match_status"),
            template=gp.get("web_template_key"),
        ))

    out = {}
    for (sf_name, gen), a in agg.items():
        a = dict(a)
        eff = a["assertion_total"] - a["skipped_assert_total"] - a["no_locator_total"]
        a["assertion_compilable"] = eff
        a["assertion_compilable_rate"] = (round(eff / a["assertion_total"], 4)
                                          if a["assertion_total"] else None)
        cd = a.pop("checks_dist")
        a["checks_per_case"] = dict(
            min=min(cd) if cd else None, max=max(cd) if cd else None,
            median=statistics.median(cd) if cd else None,
            dist=dict(Counter(cd)))
        out.setdefault(sf_name, {})[gen] = a
    (OUT / "F_script_quality.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "G_skipped_reasons.json").write_text(json.dumps(
        {"by_reason": dict(skipped_reasons),
         "examples": {k: v for k, v in skipped_examples.items()}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "H_script_detail.json").write_text(
        json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("\n=== 断言被拒原因 ===")
    for k, v in skipped_reasons.most_common():
        print(f"  {v:3}  {k}   e.g. {skipped_examples[k]}")


if __name__ == "__main__":
    main()
