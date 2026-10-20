# -*- coding: utf-8 -*-
"""rev79：创建 3 个探针实测子功能（RetroClaim/GoodWill 创建页 + Timeline PhoneCall）。"""
import json
import uuid
from datetime import datetime

import psycopg2

conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                        password="postgres", dbname="langgraph_platform_db")
cur = conn.cursor()
cur.execute("SELECT id, project_id FROM web_functions WHERE identifier='WF-1033'")
fid, pid = cur.fetchone()
print("function_id:", fid, "project_id:", pid)

PROBE_NOTE = "rev72-78 探针实测 2026-09-28（登录→CRM iframe→逐页导出控件id），probe_verified=true"

subs = [
    {
        "identifier": "WSF-RC-CREATE", "display_name": "CS RetroClaim 创建页",
        "name": "cs-retro-claim-create",
        "description": "RetroClaim 独立创建页 #/cs/retro-claim/create。" + PROBE_NOTE,
        "custom_config": {"field_locators": {
            "flow": "direct_page", "name": "cs_module_retro_claim_create",
            "direct_url": "#/cs/retro-claim/create", "modal": "form", "form_button": [],
            "probe_source": "rev72_cs_locators_probe.json cs_retro_create", "probe_verified": True,
            "fields": {
                "Member": {"kind": "search", "selector_hint": "#memberLabel"},
                "Case": {"kind": "search", "selector_hint": "#caseLabel"},
                "Business Date": {"kind": "date", "selector_hint": "#businessDate"},
                "Branch code": {"kind": "search", "selector_hint": "#branchCode"},
                "Invoice Number": {"kind": "input", "selector_hint": "#invoiceNumber"},
                "Invoice Amount": {"kind": "input", "selector_hint": "#invoiceAmount"},
                "Reasons": {"kind": "textarea", "selector_hint": "#reasons"}}}},
    },
    {
        "identifier": "WSF-GW-CREATE", "display_name": "CS GoodWill 创建页",
        "name": "cs-goodwill-create",
        "description": "GoodWill 独立创建页 #/cs/goodwill/create。" + PROBE_NOTE,
        "custom_config": {"field_locators": {
            "flow": "direct_page", "name": "cs_module_goodwill_create",
            "direct_url": "#/cs/goodwill/create", "modal": "form", "form_button": [],
            "probe_source": "rev76_timeline_probe.json goodwill_create", "probe_verified": True,
            "fields": {
                "Member": {"kind": "search", "selector_hint": "#memberLabel"},
                "Case": {"kind": "search", "selector_hint": "#caseLabel"},
                "Business Date": {"kind": "date", "selector_hint": "#businessDate"},
                "Branch code": {"kind": "search", "selector_hint": "#branchCode"},
                "GoodWill Type": {"kind": "select", "selector_hint": "#goodwillType"},
                "Select": {"kind": "select", "selector_hint": "#selectType"},
                "Value": {"kind": "input", "selector_hint": "#value"},
                "Reasons": {"kind": "textarea", "selector_hint": "#reasons"}}}},
    },
    {
        "identifier": "WSF-TL-PHONE", "display_name": "CS Timeline PhoneCall 新页",
        "name": "cs-timeline-phonecall",
        "description": "Case 详情 Timeline → +Add → Phone Call 新页 #/cs/timeline/phonecall/create。" + PROBE_NOTE,
        "custom_config": {"field_locators": {
            "flow": "timeline_subpage", "name": "cs_module_timeline_phonecall",
            "entry_labels": ["View", "查看", "详情"],
            "form_button": ["Add"], "menu_item": ["Phone Call"],
            "modal": "form",
            "probe_source": "rev76_timeline_probe.json timeline_Phone", "probe_verified": True,
            "fields": {
                "Subject": {"kind": "input", "selector_hint": "#subject"},
                "Call From": {"kind": "select", "selector_hint": "#callFrom"},
                "Call To": {"kind": "select", "selector_hint": "#callTo"},
                "Description": {"kind": "input", "selector_hint": "#description"}}}},
    },
]

now = datetime.now()
for s in subs:
    cur.execute(
        """INSERT INTO web_sub_functions
           (id, project_id, function_id, identifier, display_name, name, description,
            test_type, target_pages, priority, tags, custom_config, sort_order,
            total_test_cases, total_test_runs, created_at, updated_at,
            compiled_assertions, assertion_mode)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,%s)""",
        (str(uuid.uuid4()), pid, fid, s["identifier"], s["display_name"], s["name"],
         s["description"], "web", "[]", "P0",
         json.dumps(["CS", "探针实测"], ensure_ascii=False),
         json.dumps(s["custom_config"], ensure_ascii=False), 0, now, now, json.dumps(0), "compiled"))
conn.commit()
cur.execute("SELECT identifier, display_name FROM web_sub_functions WHERE function_id=%s ORDER BY created_at", (fid,))
for r in cur.fetchall():
    print(" ", r)
