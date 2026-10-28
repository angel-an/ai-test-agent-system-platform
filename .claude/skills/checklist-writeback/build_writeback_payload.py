#!/usr/bin/env python3
"""checklist-writeback：拉取平台最近一次 Checklist 执行，生成写回腾讯文档的行载荷。

分工（与平台「只算载荷、写文档由外部执行」的原则一致）
------------------------------------------------------
平台负责：存执行结果、本脚本算载荷。
WorkBuddy 负责：用腾讯文档授权打开文档，把本脚本输出的 JSON 行插入表格
（约定插到「自动化执行」区，见 --label）。

用法
----
    python build_writeback_payload.py --project PR-2 \
        --base http://127.0.0.1:8000 [--run-id UUID] [--out payload.json]

输出：单行 JSON 对象，键为表格列名，值均为字符串/数字，可直接映射插入。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))  # 平台部署在国内，统一东八区展示


def fmt_time(iso: str) -> str:
    """ISO 时间（可能带时区）→ 'YYYY-MM-DD HH:MM:SS' 东八区。不带时区按原样截断。"""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:19].replace("T", " ")
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")

VERDICT_LABEL = {"pass": "通过", "fail": "失败", "unverified": "未验证", "empty": "无结果"}


def api_get(base: str, path: str, token: str | None) -> dict:
    url = f"{base.rstrip('/')}{path}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Checklist 执行结果写回载荷")
    ap.add_argument("--project", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default=None)
    ap.add_argument("--run-id", default=None, help="缺省取最近一次执行")
    ap.add_argument("--label", default="自动化执行", help="执行方式列的文案")
    ap.add_argument("--out", default=None, help="输出文件（缺省 stdout）")
    args = ap.parse_args()

    runs = api_get(args.base, f"/api/v2/projects/{args.project}/checklist/runs?limit=50", args.token)
    run_list = runs.get("data", [])
    if not run_list:
        print("[error] 平台无执行记录，无可回写内容", file=sys.stderr)
        return 1

    summary_run = next((r for r in run_list if args.run_id in (None, "", r.get("run_id"))), None)
    if summary_run is None:
        print(f"[error] 找不到 run_id={args.run_id}", file=sys.stderr)
        return 1

    detail = api_get(args.base,
                     f"/api/v2/projects/{args.project}/checklist/runs/{summary_run['run_id']}",
                     args.token)
    d = detail.get("data", {})
    s = d.get("summary") or {}
    verdict = d.get("run_verdict", "empty")
    failed = [r for r in d.get("results", []) if r.get("status") == "fail"]

    # created_at 只在 /runs 列表返回，详情接口不带——从列表记录取
    created_at = summary_run.get("created_at") or d.get("created_at") or ""
    payload = {
        "执行时间": fmt_time(created_at),
        "执行方式": args.label,
        "项目画像": d.get("profile_name") or "",
        "判定": VERDICT_LABEL.get(verdict, verdict),
        "通过": s.get("pass", 0),
        "失败": s.get("fail", 0),
        "未验证": s.get("unverified", 0),
        "流程类跳过": d.get("skipped_flow", 0),
        "失败行": ", ".join(f"{r.get('row_id')} {r.get('feature') or ''}({r.get('error') or '无原因'})"
                          for r in failed),
        "报告链接": d.get("report_url") or "",
        "run_id": d.get("run_id") or "",
    }

    out = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        print(f"[done] 载荷已写入 {args.out}（{len(failed)} 个失败行）")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
