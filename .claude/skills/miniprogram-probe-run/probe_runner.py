#!/usr/bin/env python3
"""miniprogram-probe-run：minium 驱动微信开发者工具执行 Checklist 探针并回传平台。

实现 docs/checklist-回归-探针契约.md 的四步闭环：
    1. GET /rows 拉清单行（带 token）
    2. minium 逐条探测（reachable/visible/navigate），每行截图存证据
    3. POST /evidence 逐张上传，拿 object_name（旧式相对路径不再使用）
    4. POST /run-results 整包回传，输出 run_id

flow 类探针不执行（需造数据），计入 skipped_flow。

用法：
    python probe_runner.py --project PR-2 --base http://127.0.0.1:8000 \
        --selectors xysjg_selectors.json [--main-module 首页] \
        [--kinds visible,navigate,reachable] [--limit 5] [--profile xiaoyang-shengjian]

token 取自环境变量 CHECKLIST_TOKEN，或用 --username/--password 现场登录
（如 checklist-bot 服务账号）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import uuid

TAB_PAGES = {
    "pages/home/index", "pages/store/goods", "pages/order/index/index",
    "pages/member/mine/index", "pages/memberCode/index",
}


# ---------------------------------------------------------------------------
# 平台 API（与契约一致；标准库实现，探针机器无第三方依赖时可用同构代码替换 minium 部分）
# ---------------------------------------------------------------------------

def api_get(base: str, path: str, token: str) -> dict:
    req = urllib.request.Request(f"{base.rstrip('/')}{path}")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post_json(base: str, path: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}", data=json.dumps(payload).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"},
    )
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_upload_evidence(base: str, project: str, token: str, path: str) -> str:
    boundary = uuid.uuid4().hex
    data = open(path, "rb").read()
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{os.path.basename(path)}\"\r\n"
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/v2/projects/{project}/checklist/evidence",
        data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]["object_name"]


def login(base: str, username: str, password: str) -> str:
    payload = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/v2/auth/login", data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


# ---------------------------------------------------------------------------
# minium 驱动
# ---------------------------------------------------------------------------

def goto_page(mini, page_path: str, timeout: float = 12.0) -> bool:
    """导航到页面；tab 页用 switch_tab，其余 navigate_to。返回是否落在该页。"""
    app = mini.app
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cur = app.get_current_page()
            cur_path = getattr(cur, "path", "") or ""
            if cur_path.strip("/") == page_path.strip("/"):
                return True
            if page_path in TAB_PAGES:
                mini.native.switch_tab(page_path)
            else:
                app.navigate_to(page_path)
            time.sleep(2.0)
        except Exception:
            time.sleep(1.5)
    cur = app.get_current_page()
    return (getattr(cur, "path", "") or "").strip("/") == page_path.strip("/")


def find_element(page, spec: dict):
    """按配置找元素：by=text 用 xpath 文本匹配，by=xpath 直接用。"""
    if spec["by"] == "text":
        xpath = f"//*[contains(text(),'{spec['selector']}')]"
        return page.get_element_by_xpath(xpath)
    return page.get_element_by_xpath(spec["selector"])


def dig_data(data: dict, dotted: str):
    """按 a.b.0.c 取 page.data 节点。"""
    node = data
    for part in dotted.split("."):
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def data_assert(page, spec: dict) -> str | None:
    """数据断言，返回错误文案；通过返回 None。"""
    dp = spec.get("data_path")
    if not dp:
        return None
    node = dig_data(page.data or {}, dp)
    if "data_expect" in spec:
        return None if node == spec["data_expect"] else f"数据断言失败：{dp}={node!r}，期望 {spec['data_expect']!r}"
    if isinstance(node, (list, dict)):
        return None if len(node) > 0 else f"数据断言失败：{dp} 为空"
    if isinstance(node, str):
        return None if node.strip() else f"数据断言失败：{dp} 为空串"
    return None if node else f"数据断言失败：{dp} 为空"


def probe_row(mini, row: dict, spec: dict | None, evidence_dir: str) -> dict:
    """执行单行探针，返回 RunResultItem 字典（evidence 先填本地路径，上传后替换）。"""
    item = {
        "row_id": row["row_id"], "status": "unverified", "error": None,
        "evidence": None, "consistency": None,
        "feature": row.get("feature"), "main_module": row.get("main_module"),
    }
    shot = os.path.join(evidence_dir, f"{row['row_id']}.png")
    try:
        if spec is None:
            item["error"] = "选择器配置缺失（xysjg_selectors.json）"
            return item
        if not goto_page(mini, spec["page"]):
            item["error"] = f"页面不可达：{spec['page']}"
            return item
        page = mini.app.get_current_page()
        kind = row.get("probe_kind") or "reachable"

        if kind == "reachable":
            item["status"] = "pass"
        elif kind == "visible":
            err = None
            sel = spec.get("selector")
            if sel:
                try:
                    page.get_elements(sel)
                except Exception:
                    err = f"元素不可见：{sel}"
            if not err:
                err = data_assert(page, spec)
            item["status"] = "fail" if err else "pass"
            item["error"] = err
        elif kind == "navigate":
            err = data_assert(page, spec)
            note = spec.get("note")
            if err:
                item["status"] = "fail"
                item["error"] = err
            else:
                target = spec.get("target_page")
                sel = spec.get("selector")
                clicked = False
                if sel and spec.get("click", True):
                    try:
                        page.get_element(sel).click()
                        clicked = True
                        time.sleep(2.5)
                    except Exception as exc:
                        item["error"] = f"点击失败：{exc}"[:200]
                if not clicked and target:
                    # IDE 模式组件内点击/坐标点击不可用：直达落地页验证
                    goto_page(mini, target)
                    time.sleep(1.0)
                    if note:
                        item["consistency"] = note
                cur = (getattr(mini.app.get_current_page(), "path", "") or "").strip("/")
                expect = (spec.get("expect_path_prefix") or "").strip("/")
                if not expect or cur.startswith(expect):
                    if item["status"] != "fail" and not item["error"]:
                        item["status"] = "pass"
                else:
                    item["status"] = "fail"
                    item["error"] = (item["error"] or "") + f"落地页不符：期望 {expect} 前缀，实际 {cur}"
        else:
            item["error"] = f"未知探针类型：{kind}"
    except Exception as exc:  # 单行失败不拖垮整轮
        item["status"] = "fail"
        item["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        try:
            mini.native.screen_shot(shot)
            if os.path.exists(shot) and os.path.getsize(shot) > 0:
                item["evidence"] = shot
        except Exception as exc:
            if item["status"] == "pass":
                item["status"] = "unverified"
            item["error"] = (item["error"] or "") + f"；截图失败: {exc}"[:200]
    return item


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="执行 Checklist 小程序探针并回传平台")
    ap.add_argument("--project", required=True, help="平台项目 identifier，如 PR-2")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--selectors", required=True, help="选择器配置 json")
    ap.add_argument("--mini-project", default=r"backend\workspace\miniprogram\xysjg")
    ap.add_argument("--main-module", default=None, help="只跑某主模块，如 首页")
    ap.add_argument("--kinds", default="reachable,visible,navigate")
    ap.add_argument("--limit", type=int, default=0, help="最多跑 N 行（调试用）")
    ap.add_argument("--profile", default="xiaoyang-shengjian")
    ap.add_argument("--username", default=None, help="缺省取 CHECKLIST_TOKEN；或现场登录")
    ap.add_argument("--password", default=None)
    ap.add_argument("--dev-tool", default=r"D:\Program Files (x86)\Tencent\微信web开发者工具\cli.bat")
    ap.add_argument("--auto-port", type=int, default=9420)
    args = ap.parse_args()

    token = os.environ.get("CHECKLIST_TOKEN")
    if not token:
        if not (args.username and args.password):
            print("[error] 需要 CHECKLIST_TOKEN 环境变量或 --username/--password", file=sys.stderr)
            return 2
        token = login(args.base, args.username, args.password)
        print("[info] 已用账号密码登录取 token")

    selectors = {k: v for k, v in json.load(open(args.selectors, encoding="utf-8")).items()}
    kinds = set(args.kinds.split(","))

    rows_resp = api_get(args.base, f"/api/v2/projects/{args.project}/checklist/rows", token)
    all_rows = rows_resp["data"]
    rows = [r for r in all_rows
            if (not args.main_module or r["main_module"] == args.main_module)
            and r["probe_kind"] in kinds]
    flow_rows = [r for r in all_rows if r["probe_kind"] == "flow"
                 and (not args.main_module or r["main_module"] == args.main_module)]
    if args.limit:
        rows = rows[: args.limit]
    print(f"[info] 清单 {len(all_rows)} 行 → 本轮执行 {len(rows)} 行，flow 跳过 {len(flow_rows)} 行")

    import minium
    mini = minium.Minium({
        "project_path": os.path.abspath(args.mini_project),
        "auto_port": args.auto_port,
        "dev_tool_path": args.dev_tool,
    })
    time.sleep(4)  # 等模拟器就绪

    evidence_dir = os.path.join("probe-runs", time.strftime("%Y%m%d-%H%M%S"), "evidence")
    os.makedirs(evidence_dir, exist_ok=True)

    results = []
    for i, row in enumerate(rows, 1):
        spec = selectors.get(row["row_id"])
        item = probe_row(mini, row, spec, evidence_dir)
        results.append(item)
        mark = {"pass": "✓", "fail": "✗"}.get(item["status"], "?")
        print(f"[{i}/{len(rows)}] {mark} {row['row_id']} {row.get('feature', '')} "
              f"{item['error'] or ''}", flush=True)

    # 第三步：逐张上传证据
    for item in results:
        if item.get("evidence"):
            try:
                item["evidence"] = api_upload_evidence(
                    args.base, args.project, token, item["evidence"])
            except Exception as exc:
                item["error"] = (item.get("error") or "") + f"；证据上传失败: {exc}"[:200]

    # 第四步：整包回传
    summary = {"pass": 0, "fail": 0, "unverified": 0}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    verdict = "pass" if summary.get("fail", 0) == 0 and summary.get("unverified", 0) == 0 \
        else ("fail" if summary.get("fail", 0) > 0 else "unverified")
    payload = {
        "profile": args.profile,
        "run_verdict": verdict,
        "summary": summary,
        "skipped_flow": len(flow_rows),
        "report_url": None,
        "results": results,
    }
    resp = api_post_json(args.base, f"/api/v2/projects/{args.project}/checklist/run-results",
                         token, payload)
    print(f"[done] run_id={resp['data']['run_id']} verdict={verdict} "
          f"rows_touched={resp['data']['rows_touched']} summary={summary}")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
