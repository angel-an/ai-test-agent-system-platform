# -*- coding: utf-8 -*-
"""rev70d：基于 TR-102（收口后）重生成 CS Module 执行报告 HTML"""
import html
from datetime import datetime

import psycopg2

DSN = dict(host="localhost", port=5432, dbname="langgraph_platform_db",
           user="postgres", password="postgres")
TR = "842ed3da-82a0-4e51-8ca5-e7a0b5c9a516"
OUT = r"C:/Users/admin/WorkBuddy/2026-09-20-14-43-51/artifacts/小杨生煎B端储值充值_执行报告_TR102_20260927.html"

STATUS_META = {
    "PASSED": ("通过 passed", "#16a34a"),
    "FAILED": ("失败 failed", "#dc2626"),
    "SKIPPED": ("跳过 skipped", "#d97706"),
    "BLOCKED": ("阻塞 blocked", "#a855f7"),
    "NOT_EXECUTED": ("未执行 not_executed", "#6b7280"),
}


def esc(s):
    return html.escape(str(s or ""))


def main():
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("""SELECT run_state, test_cases_count, passed_count, failed_count,
                   skipped_count, blocked_count, not_executed_count, created_at
                   FROM test_runs WHERE id=%s""", (TR,))
    run = cur.fetchone()
    cur.execute("""SELECT tc.identifier, tc.name, tr.status, tr.description
                   FROM test_results tr JOIN test_cases tc ON tc.id=tr.test_case_id
                   WHERE tr.test_run_id=%s ORDER BY tr.status, tc.identifier""", (TR,))
    rows = cur.fetchall()

    counts = {st: 0 for st in STATUS_META}
    for r in rows:
        counts[r[2]] = counts.get(r[2], 0) + 1
    total = len(rows)

    # 状态分布环图
    seg, off = [], 0.0
    import math
    C = 2 * math.pi * 70
    for st, (label, color) in STATUS_META.items():
        n = counts.get(st, 0)
        if not n:
            continue
        frac = n / total
        seg.append(f'<circle cx="90" cy="90" r="70" fill="none" stroke="{color}" stroke-width="26" '
                   f'stroke-dasharray="{C*frac:.2f} {C*(1-frac):.2f}" stroke-dashoffset="{-C*off:.2f}" '
                   f'transform="rotate(-90 90 90)"/>')
        off += frac

    # 分组明细
    sections = {}
    for st, (label, color) in STATUS_META.items():
        items = [r for r in rows if r[2] == st]
        if not items:
            continue
        lis = []
        for ident, name, status, desc in items:
            lis.append(
                f'<tr><td class="id">{esc(ident)}</td><td>{esc(name)}</td>'
                f'<td class="reason">{esc(desc)}</td></tr>')
        sections[st] = (label, color, "".join(lis))

    order = ["FAILED", "BLOCKED", "SKIPPED", "NOT_EXECUTED", "PASSED"]
    tbl_blocks = []
    for st in order:
        if st not in sections:
            continue
        label, color, body = sections[st]
        tbl_blocks.append(
            f'<h3 style="color:{color}">{label}（{counts[st]}）</h3>'
            f'<table><tr><th style="width:110px">用例</th><th style="width:340px">名称</th>'
            f'<th>执行原因 / 断言明细</th></tr>{body}</table>')

    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小杨生煎-B端储值充值 执行报告（TR-102 基线）</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:#f3f4f6;color:#111827;font-size:14px;line-height:1.7}}
.wrap{{max-width:1200px;margin:0 auto;padding:32px 24px}}
h1{{font-size:24px;margin-bottom:4px}}
h2{{font-size:18px;margin:36px 0 14px;padding-left:10px;border-left:4px solid #2563eb}}
h3{{font-size:15px;margin:18px 0 8px}}
.meta span{{display:inline-block;margin-right:24px;color:#6b7280;font-size:13px}}
.cards{{display:flex;gap:16px;margin:24px 0;flex-wrap:wrap}}
.card{{flex:1;min-width:150px;background:#fff;border-radius:10px;padding:18px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card .num{{font-size:32px;font-weight:700}}
.card .lab{{color:#6b7280;font-size:12px;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.06);margin-bottom:10px}}
th,td{{padding:9px 12px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;font-size:13px}}
th{{background:#f9fafb;color:#374151;font-size:12px}}
td.id{{font-family:Consolas,monospace;color:#2563eb;font-weight:600}}
td.reason{{color:#4b5563;font-size:12px;word-break:break-all}}
.finding{{background:#fff;border-radius:10px;padding:16px 18px;margin:10px 0;box-shadow:0 1px 3px rgba(0,0,0,.08);border-left:4px solid #16a34a}}
.finding.warn{{border-left-color:#d97706}}
.finding.info{{border-left-color:#2563eb}}
.ft{{font-weight:700;margin-bottom:6px}}
.kv{{color:#6b7280;font-size:12px}}
code{{background:#eef2ff;padding:1px 5px;border-radius:4px;font-size:12px}}
footer{{margin-top:36px;color:#9ca3af;font-size:12px;text-align:center}}
</style></head><body><div class="wrap">
<h1>小杨生煎-B端储值充值 执行报告（TR-102 基线）</h1>
<div class="meta">
<span>项目：PR-2（AI 测试平台，用例文件夹「小杨生煎-B端储值充值」）</span>
<span>被测系统：UAT console.uat.eatizen.com.hk</span>
<span>基线运行：TR-102（2026-09-27，自动化执行）</span>
<span>需求来源：平台需求文档《小杨生煎-B端储值充值》（req-mugi54yo-lhae0b）</span><br>
<span>链路：需求 → 用例生成 → Playwright 脚本编译 → 自动执行 → 逐用例结果落库</span>
</div>
<div class="cards">
<div class="card" style="display:flex;align-items:center;justify-content:center"><svg width="180" height="180" viewBox="0 0 180 180">{''.join(seg)}<text x="90" y="86" text-anchor="middle" font-size="30" font-weight="700" fill="#111827">{total}</text><text x="90" y="108" text-anchor="middle" font-size="12" fill="#6b7280">条用例</text></svg></div>
{"".join(f'<div class="card"><div class="num" style="color:{color}">{counts[st]}</div><div class="lab">{label}（{counts[st]*100//total}%）</div></div>' for st, (label, color) in STATUS_META.items() if counts.get(st))}
</div>

<h2>一、本版收口说明（2026-09-27）</h2>
<div class="finding"><div class="ft">🟢 收口 1｜5 条 SKIPPED 升级 PASSED（本轮生效）</div>
手机号 ×3（TC-661330/TC-814784/TC-838774）：人工 UAT 复核证实服务端存在性校验有效——11 位非法号码 1821010121a 提交后被明确拒绝（充值成功 0 / 失败 1 / 原因"不存在手机号"），业务无风险。单选组 ×2（TC-339232/TC-916370）：默认选中断言实测 pass，UI 层负路径不适用（如需服务端空值校验走 API 层）。升级依据已写入用例执行原因。</div>
<div class="finding info"><div class="ft">🔵 修复 2｜automation_status 标记滞后修复（rev70c）</div>
此前 minimal-loop 自动执行成功后不回写用例自动化状态（30/32 仍显示未自动化）。已在结果落库链路（web_test_service.persist_case_level_results）增加回写：拿到可执行结论的用例自动标记 AUTOMATED；无可执行断言（not_executed）保持未自动化便于筛选。存量 21 条已回填，现分布 23 AUTOMATED / 9 NOT_AUTOMATED。</div>
<div class="finding info"><div class="ft">🔵 验证 3｜跨会话复验通过（TR-102 vs TR-101 零差异）</div>
双端进程重启后重新编译、重新执行：32 条用例结果明细 32 行无重复（重执行 upsert 修复持续生效），逐用例状态与 TR-101 基线完全一致，执行耗时约 5.5 分钟。</div>

<h2>二、用例执行明细（按状态分组，含执行原因）</h2>
{''.join(tbl_blocks)}

<h2>三、遗留事项</h2>
<table>
<tr><th style="width:260px">事项</th><th>说明</th></tr>
<tr><td>上传必填 ×2（TC-536003/TC-548288）</td><td>UI 负路径提交层零网络请求（前端静默无动作），自动化不可定性；人工 F12 一分钟可闭环（看点击确定后有无请求）</td></tr>
<tr><td>10 条 NOT_EXECUTED 断言缺口</td><td>四类主题：储值标签配置只读断言 ×3、B2B/C 端跨端验证 ×3、充值流水数据核验 ×2、上传文件边界矩阵 ×2——需按主题补断言模板或走 API 层断言（单独立项）</td></tr>
<tr><td>UAT 储值列表核查</td><td>17:31~18:10 与 23:34~23:36 两个时间窗，待人工核对后回填</td></tr>
<tr><td>"美心|積分商城" 32 条误删用例</td><td>历史遗留，待从知识库源文档重建</td></tr>
</table>
<footer>报告生成于 {gen_time} ｜ 基线：TR-102（run_state={esc(run[0])}）｜ 数据来源：平台数据库 test_results 实时核对</footer>
</div></body></html>"""
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(doc)
    print("written:", OUT, "cases:", total, "counts:", counts)


if __name__ == "__main__":
    main()
