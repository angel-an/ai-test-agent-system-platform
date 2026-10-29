#!/usr/bin/env python3
"""checklist-writeback：拉取平台 Checklist 执行，生成写回腾讯文档的行载荷。

分工（与平台「只算载荷、写文档由外部执行」的原则一致）
------------------------------------------------------
平台负责：存执行结果、本脚本算载荷。
WorkBuddy 负责：用腾讯文档授权打开文档，把本脚本输出的 JSON 行插入表格
（约定插到「自动化执行」区，见 --label）。

两种载荷
--------
- run 级（默认）：一次执行的整包结论（11 列单行）
- 按模块分批（--by-module）：全量清单行最新状态逐主模块出载荷行，
  已执行模块给结论、未执行模块如实标未验证

用法
----
    python build_writeback_payload.py --project PR-2 \
        --base http://127.0.0.1:8000 --token "$CHECKLIST_TOKEN" \
        [--run-id UUID] [--by-module] [--out payload.json]

输出：JSON（run 级为对象，by-module 为数组），值均为字符串/数字，可直接映射插入。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

CN_TZ = timezone(timedelta(hours=8))  # 平台部署在国内，统一东八区展示

VERDICT_LABEL = {"pass": "通过", "fail": "失败", "unverified": "未验证", "empty": "无结果"}


def fmt_time(iso: str) -> str:
    """ISO 时间（可能带时区）→ 'YYYY-MM-DD HH:MM:SS' 东八区。不带时区按原样截断。"""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:19].replace("T", " ")
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def api_get(base: str, path: str, token: str | None) -> dict:
    url = f"{base.rstrip('/')}{path}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def emit(payload, args, note: str = "") -> int:
    out = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out + "\n")
        print(f"[done] {note}载荷已写入 {args.out}")
    else:
        print(out)
    return 0


def run_level(args) -> int:
    """run 级载荷：一次执行的整包结论。"""
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
    # 主模块列（2026-10-29 起与腾讯文档 12 列表头对齐——LinkFox 复核发现的错位缺陷；
    # 平台 WRITEBACK_COLUMNS 同改，两侧列契约须保持同步）：
    # run 详情可显式带 main_module；否则取 results 各行 main_module 去重拼接
    modules: list[str] = []
    for r in d.get("results", []):
        m = r.get("main_module") or ""
        if m and m not in modules:
            modules.append(m)
    payload = {
        "执行时间": fmt_time(created_at),
        "执行方式": args.label,
        "项目画像": d.get("profile_name") or "",
        "主模块": d.get("main_module") or "、".join(modules),
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
    return emit(payload, args, note=f"（{len(failed)} 个失败行）")


def by_module(args) -> int:
    """按主模块分批：全量清单行的最新状态逐模块出载荷（每模块一行）。

    已执行的模块给 run 级结论；未执行的模块如实标未验证——不伪造覆盖面。
    """
    rows = api_get(args.base, f"/api/v2/projects/{args.project}/checklist/rows", args.token)
    run_id = (args.run_id or "").strip()
    latest_run_at = ""
    if run_id:
        runs = api_get(args.base, f"/api/v2/projects/{args.project}/checklist/runs?limit=50",
                       args.token)
        hit = next((r for r in runs.get("data", []) if r.get("run_id") == run_id), None)
        if hit:
            latest_run_at = fmt_time(hit.get("created_at") or "")

    modules: dict[str, dict] = {}
    for r in rows.get("data", []):
        m = r.get("main_module") or "（未分组）"
        bucket = modules.setdefault(m, {"pass": 0, "fail": 0, "unverified": 0})
        st = r.get("latest_status") or "unverified"
        bucket[st] = bucket.get(st, 0) + 1

    lines = []
    for m, c in modules.items():
        fails = c.get("fail", 0)
        unv = c.get("unverified", 0)
        verdict = "通过" if fails == 0 and unv == 0 else ("失败" if fails else "未验证")
        lines.append({
            "执行时间": latest_run_at or "-",
            "执行方式": args.label,
            "主模块": m,
            "判定": verdict,
            "通过": c.get("pass", 0),
            "失败": fails,
            "未验证": unv,
            "关联run_id": run_id or "-",
        })

    return emit(lines, args, note=f"{len(lines)} 个模块载荷行")


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 Checklist 执行结果写回载荷")
    ap.add_argument("--project", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default=None, help="必填（checklist 端点强制鉴权）")
    ap.add_argument("--run-id", default=None, help="缺省取最近一次执行")
    ap.add_argument("--label", default="自动化执行", help="执行方式列的文案")
    ap.add_argument("--out", default=None, help="输出文件（缺省 stdout）")
    ap.add_argument("--by-module", action="store_true",
                    help="按主模块分批：全量清单行最新状态逐模块出载荷行")
    args = ap.parse_args()

    return by_module(args) if args.by_module else run_level(args)


if __name__ == "__main__":
    sys.exit(main())
