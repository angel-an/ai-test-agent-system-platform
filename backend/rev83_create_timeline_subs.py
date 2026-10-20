# -*- coding: utf-8 -*-
"""rev83：创建 Timeline Note / Task / Post 三个探针实测子功能。"""
import json
import uuid
from datetime import datetime

import psycopg2

conn = psycopg2.connect(host="localhost", port=5432, user="postgres",
                        password="postgres", dbname="langgraph_platform_db")
cur = conn.cursor()
cur.execute("SELECT id, project_id FROM web_functions WHERE identifier='WF-1033'")
fid, pid = cur.fetchone()

PROBE_NOTE = "rev75/76/78 探针实测 2026-09-28，probe_verified=true"
COMMON = {
    "flow": "timeline_subpage",
    "entry_labels": [],
    "form_button": ["Add"],
    "list_row_link": True,
    "probe_verified": True,
}

subs = [
    {
        "identifier": "WSF-TL-NOTE", "display_name": "CS Timeline Note 弹窗",
        "name": "cs-timeline-note",
        "description": "Case 详情 Timeline → +Add → Note 弹窗。" + PROBE_NOTE,
        "custom_config": {"field_locators": {
            **COMMON, "name": "cs_module_timeline_note",
            "menu_item": ["Note"], "modal": ".ant-modal-content",
            "probe_source": "rev75/78 timeline_Note",
            "fields": {
                "Subject": {"kind": "input", "selector_hint": "#subject"},
                "Description": {"kind": "textarea", "selector_hint": "#description"},
                "Attachments": {"kind": "file", "selector_hint": "input[type=file]"},
                "Assign": {"kind": "select", "selector_hint": "#assist"}}}},
    },
    {
        "identifier": "WSF-TL-TASK", "display_name": "CS Timeline Task 新页",
        "name": "cs-timeline-task",
        "description": "Case 详情 Timeline → +Add → Task 新页 #/cs/timeline/task/create。" + PROBE_NOTE,
        "custom_config": {"field_locators": {
            **COMMON, "name": "cs_module_timeline_task",
            "menu_item": ["Task"], "modal": "form",
            "probe_source": "rev76 timeline_Task",
            "fields": {
                "Subject": {"kind": "input", "selector_hint": "#subject"},
                "Regarding": {"kind": "input"},
                "Description": {"kind": "input", "selector_hint": "#description"},
                "Assign": {"kind": "select", "selector_hint": "#assist"}}}},
    },
    {
        "identifier": "WSF-TL-POST", "display_name": "CS Timeline Post 弹窗",
        "name": "cs-timeline-post",
        "description": "Case 详情 Timeline → +Add → Post 弹窗（Description 必填500/reply 100）。" + PROBE_NOTE,
        "custom_config": {"field_locators": {
            **COMMON, "name": "cs_module_timeline_post",
            "menu_item": ["Post"], "modal": ".ant-modal-content",
            "probe_source": "rev75 timeline_Post",
            "fields": {
                "Description": {"kind": "textarea", "selector_hint": "#description"},
                "Assign": {"kind": "select", "selector_hint": "#assist"},
                "Attachment": {"kind": "file", "selector_hint": "input[type=file]"}}}},
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
         json.dumps(s["custom_config"], ensure_ascii=False), 0, now, now,
         json.dumps(0), "compiled"))
conn.commit()
cur.execute("SELECT identifier, display_name FROM web_sub_functions WHERE function_id=%s ORDER BY created_at", (fid,))
for r in cur.fetchall():
    print(" ", r)
