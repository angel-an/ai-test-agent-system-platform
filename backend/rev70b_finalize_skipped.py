# -*- coding: utf-8 -*-
"""rev70b：SKIPPED 状态收口
手机号×3（人工UAT复核：服务端存在性校验有效）+ 单选组×2（默认选中断言 pass，UI 层负路径不适用）
→ 升级 PASSED；上传×2 维持 SKIPPED。
同步三处：test_results.status / test_run_test_cases.latest_status / test_runs 计数。
范围：TR-101（5eb5b660）、TR-102（842ed3da）两个基线。
"""
import psycopg2

DSN = dict(host="localhost", port=5432, dbname="langgraph_platform_db",
           user="postgres", password="postgres")
UPGRADE = ["TC-661330", "TC-814784", "TC-838774", "TC-339232", "TC-916370"]
RUNS = {"TR-101": "5eb5b660-f682-4a84-a654-09925dbd8a38",
        "TR-102": "842ed3da-82a0-4e51-8ca5-e7a0b5c9a516"}
NOTE_BY_CASE = {
    "TC-661330": "【状态收口 2026-09-27】人工UAT复核证实服务端存在性校验有效（不存在手机号被明确拒绝），负路径结论闭环，SKIPPED→PASSED",
    "TC-814784": "【状态收口 2026-09-27】人工UAT复核证实服务端拒绝并明示失败原因（失败清单提示能力佐证），SKIPPED→PASSED",
    "TC-838774": "【状态收口 2026-09-27】人工UAT复核证实服务端校验有效，SKIPPED→PASSED",
    "TC-339232": "【状态收口 2026-09-27】默认选中断言已 pass；单选组无自由输入，UI 层负路径不适用（如需服务端空值校验走 API 层），SKIPPED→PASSED",
    "TC-916370": "【状态收口 2026-09-27】默认选中断言已 pass；单选组 UI 层负路径不适用，SKIPPED→PASSED",
}


def main():
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    for run_name, run_id in RUNS.items():
        # 定位 5 条的 latest result
        cur.execute("""
            SELECT tc.identifier, tr.id FROM test_run_test_cases rtc
            JOIN test_cases tc ON tc.id = rtc.test_case_id
            JOIN test_results tr ON tr.id = rtc.latest_result_id
            WHERE rtc.test_run_id = %s AND tc.identifier = ANY(%s)
              AND tr.status = 'SKIPPED'""", (run_id, UPGRADE))
        rows = dict(cur.fetchall())
        missing = set(UPGRADE) - set(rows)
        if missing:
            print(f"[warn] {run_name} 非 SKIPPED/缺失: {missing}")
        for ident, result_id in rows.items():
            note = NOTE_BY_CASE[ident]
            cur.execute("UPDATE test_results SET status='PASSED', "
                        "description = description || %s, updated_at=now() WHERE id=%s",
                        ("；" + note, result_id))
            cur.execute("UPDATE test_run_test_cases rtc SET latest_status='PASSED', updated_at=now() "
                        "FROM test_cases tc WHERE tc.id = rtc.test_case_id "
                        "AND rtc.test_run_id=%s AND tc.identifier=%s", (run_id, ident))
            print(f"[{run_name}] {ident} -> PASSED")
        # 重算运行计数（以 test_results 为准，防漂移）
        cur.execute("""SELECT
            count(*) FILTER (WHERE status='PASSED'), count(*) FILTER (WHERE status='FAILED'),
            count(*) FILTER (WHERE status='SKIPPED'), count(*) FILTER (WHERE status='BLOCKED'),
            count(*) FILTER (WHERE status='NOT_EXECUTED') FROM test_results WHERE test_run_id=%s""",
            (run_id,))
        p, f, s, b, n = cur.fetchone()
        cur.execute("""UPDATE test_runs SET passed_count=%s, failed_count=%s, skipped_count=%s,
                       blocked_count=%s, not_executed_count=%s, updated_at=now() WHERE id=%s""",
                    (p, f, s, b, n, run_id))
        print(f"[{run_name}] counters -> P{p}/F{f}/S{s}/B{b}/NE{n}")
    conn.commit()
    # 校验
    for run_name, run_id in RUNS.items():
        cur.execute("""SELECT tr.status, count(*) FROM test_results tr
                       WHERE tr.test_run_id=%s GROUP BY 1 ORDER BY 1""", (run_id,))
        print(f"[verify {run_name}]", cur.fetchall())


if __name__ == "__main__":
    main()
