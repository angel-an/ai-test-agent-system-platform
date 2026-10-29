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
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

def tab_pages_of(dev_conf) -> set:
    """tabBar 页名单来自项目配置 tab_pages（各小程序不同，框架不内置）。

    名单用于区分「只能 switchTab 不能 navigateTo 的页面」。
    device 模式取 _device.tab_pages；IDE 模式取配置顶层 tab_pages；缺省空集。
    """
    if not dev_conf:
        return set()
    return set(dev_conf.get("tab_pages") or [])


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

def goto_page(mini, page_path: str, timeout: float = 15.0, dev_conf=None) -> bool:
    """导航到页面并断言落地。tabBar 页微信禁 navigateTo，依次尝试
    switch_tab → relaunch → navigate_to；Uncaught 异步报错一律吞掉
    （导航可能实际成功），最终以 page path 裁决，不轻信任何 SDK 返回值。"""
    app = mini.app
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cur = (getattr(app.get_current_page(), "path", "") or "").strip("/")
            if cur == page_path.strip("/"):
                return True
            try:
                if page_path in tab_pages_of(dev_conf):
                    app.switch_tab(page_path, is_wait_url_change=True,
                                   wait_route_done_time=10)
                else:
                    app.navigate_to(page_path)
            except Exception:
                # 方法失败不立即放弃：吞错后看实际落点（异步 JS 报错会伪装成命令异常）
                pass
            time.sleep(2.0)
        except Exception:
            time.sleep(1.5)
    return (getattr(app.get_current_page(), "path", "") or "").strip("/") \
        == page_path.strip("/")


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


# ---------------------------------------------------------------------------
# 真机混合通道（device 模式）：automator 桥（test_port）+ u2 坐标点击 + adb 截图。
# 前提：手机经 auto-preview 真机调试二维码挂进 IDE 自动化会话，wx API 经 mock 打桩。
# ---------------------------------------------------------------------------

def adb_shot(adb_path: str, serial: str, out: str) -> None:
    with open(out, "wb") as f:
        subprocess.run([adb_path, "-s", serial, "exec-out", "screencap", "-p"],
                       stdout=f, check=False)


def current_activity(adb_path: str, serial: str) -> str:
    r = subprocess.run(
        [adb_path, "-s", serial, "shell",
         "dumpsys activity activities | grep -m1 topResumedActivity"],
        capture_output=True, text=True, timeout=15)
    return r.stdout or ""


def in_miniprogram(adb_path: str, serial: str) -> bool:
    act = current_activity(adb_path, serial)
    return "com.tencent.mm" in act and "AppBrandUI" in act


def reopen_miniprogram(mini, u2d, dev_conf, adb_path: str, serial: str) -> bool:
    """小程序被完全退出时自动重进：回微信首页 → 下拉最近小程序 → 点图标。

    全程坐标化（荣耀系无障碍树不暴露微信 UI 文本），实现无人值守。
    """
    reopen = dev_conf.get("reopen") or {}
    (x1, y1), (x2, y2) = reopen.get("swipe", [[500, 400], [500, 1150]])
    ix, iy = reopen.get("icon", [105, 470])
    try:
        u2d.app_start("com.tencent.mm")
        time.sleep(3)
        # 退到微信首页（聊天列表）：不在 LauncherUI 就按返回
        for _ in range(5):
            if "LauncherUI" in current_activity(adb_path, serial):
                break
            u2d.press("back")
            time.sleep(1.2)
        # 下拉出「最近的小程序」面板并点目标小程序图标
        u2d.swipe(x1, y1, x2, y2, duration=0.3)
        time.sleep(1.5)
        u2d.click(ix, iy)
        time.sleep(5)
        for _ in range(5):
            try:
                _ = mini.app.get_current_page().path
                return True
            except Exception:
                time.sleep(2)
        return False
    except Exception:
        return False


def is_home(mini, u2d, dev_conf, adb_path: str, serial: str,
            refs_dir: str, shot: str) -> bool:
    """以首页特征区金标图像判定是否真在首页（automator 在真机是影子栈，不可信）。

    用 home_rect（堂食/到店取/小杨送入口带，通知横幅遮不到），连拍 3 次
    （间隔 2.5s）任一命中即通过——容忍负一屏通知横幅的瞬时遮挡。
    """
    home_ref = os.path.join(refs_dir, "home.png")
    if not os.path.exists(home_ref):
        try:
            adb_shot(adb_path, serial, shot)
            from shutil import copyfile
            copyfile(crop_title(shot, dev_conf, "home_rect"), home_ref)
            return True  # 基线轮视为通过
        except Exception:
            return False
    threshold = float(dev_conf.get("rmse_threshold", 14.0))
    for attempt in range(3):
        try:
            adb_shot(adb_path, serial, shot)
            if rmse(crop_title(shot, dev_conf, "home_rect"), home_ref) <= threshold:
                return True
        except Exception:
            pass
        if attempt < 2:
            time.sleep(2.5)  # 等通知横幅消散
    return False


def ensure_session(mini, u2d, dev_conf, adb_path: str, serial: str,
                   refs_dir: str) -> bool:
    """每行探针前恢复会话：原生页（扫码/支付等）按 BACK 退回小程序；
    小程序被完全退出则自动重进（reopen 流程），无需人工留守。
    落点判定一律用截图与 home 金标比对（automator 路由不穿透真机渲染层）。"""
    tmp_shot = os.path.join(tempfile.gettempdir(), "ensure-session.png")
    for _ in range(3):
        if in_miniprogram(adb_path, serial):
            break
        u2d.press("back")
        time.sleep(1.5)
    else:
        print("[info] 小程序已退出，自动重进…", flush=True)
        if not reopen_miniprogram(mini, u2d, dev_conf, adb_path, serial):
            return False
    w, h = dev_conf.get("screen", [720, 1612])
    x, y = dev_conf.get("tab_home", [0.086, 0.957])
    # 恢复策略：第一步 tab 点击（有 tab 栏的页直接回首页）；
    # 之后每轮先截图判定，不在小程序就自动重进，在但不在首页就按 BACK
    # （二级页/表单无 tab 栏，BACK 链可能 3+ 层）。判定先于动作，绝不误退首页。
    tab_tried = False
    consecutive_backs = 0
    MAX_ATTEMPTS = 14          # 深层表单（新增地址等）BACK 链 3+ 层，10 轮曾偶发不够
    MAX_CONSECUTIVE_BACKS = 8  # 连续 BACK 上限：逼近小程序栈底前必须改走重进
    for attempt in range(MAX_ATTEMPTS):
        if in_miniprogram(adb_path, serial) and is_home(
                mini, u2d, dev_conf, adb_path, serial, refs_dir, tmp_shot):
            return True
        if not in_miniprogram(adb_path, serial):
            print("[info] 小程序已退出，自动重进…", flush=True)
            reopen_miniprogram(mini, u2d, dev_conf, adb_path, serial)
            consecutive_backs = 0
        elif not tab_tried:
            u2d.click(int(w * x), int(h * y))
            tab_tried = True
            consecutive_backs = 0
        elif consecutive_backs >= MAX_CONSECUTIVE_BACKS:
            # 连续 BACK 已到上限仍未回首页：栈底在望，重进比重叠 BACK 更稳
            print("[info] 连续 BACK 未回首页，改走自动重进…", flush=True)
            reopen_miniprogram(mini, u2d, dev_conf, adb_path, serial)
            consecutive_backs = 0
            tab_tried = False
        else:
            u2d.press("back")
            consecutive_backs += 1
        time.sleep(2.2)
    return False


def go_home_device(mini, u2d, dev_conf: dict) -> bool:
    """tabBar API 导航在本应用对 automator 抛错，改用 u2 坐标点「首页」tab。"""
    w, h = dev_conf.get("screen", [720, 1612])
    x, y = dev_conf.get("tab_home", [0.086, 0.957])
    for _ in range(2):
        u2d.click(int(w * x), int(h * y))
        time.sleep(2.0)
        try:
            if (mini.app.get_current_page().path or "").strip("/") == "pages/home/index":
                return True
        except Exception:
            time.sleep(1.0)
    return False


def wait_route(mini, timeout: float = 6.0) -> str | None:
    """点击后等待路由稳定，返回最终 page path。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = mini.app.get_current_page().path
        except Exception:
            pass
        time.sleep(0.8)
    return last


def device_click(mini, u2d, dev_conf: dict, click: dict) -> None:
    if "css" in click:  # 页面级元素走 automator（带路由事件）
        mini.app.get_current_page().get_element(click["css"]).click()
    elif "coord" in click:  # 组件内元素走 u2 坐标（画布不上报无障碍树）
        w, h = dev_conf.get("screen", [720, 1612])
        x, y = click["coord"]
        u2d.click(int(w * x), int(h * y))
    else:
        raise ValueError("click 配置须含 css 或 coord")


# ---------------------------------------------------------------------------
# 图像级落地断言（真机通道专用）：真机调试 2.0 的 automator 只操作影子栈，
# 路由事件不穿透手机渲染层——落地判定必须对 adb 截图做标题区域基线比对。
# ---------------------------------------------------------------------------

def crop_title(img_path: str, dev_conf: dict, rect_key: str = "title_rect") -> str:
    """按 dev_conf[rect_key] 裁剪判定区域，返回裁剪图路径。"""
    from PIL import Image
    rect = dev_conf.get(rect_key, [0.15, 0.055, 0.7, 0.075])
    x0, y0, w, h = rect
    im = Image.open(img_path).convert("L")
    W, H = im.size
    crop = im.crop((int(W * x0), int(H * y0), int(W * (x0 + w)), int(H * (y0 + h))))
    out = img_path + ".title.png"
    crop.save(out)
    return out


def rmse(a_path: str, b_path: str) -> float:
    """缩放至小图后做平移容忍比对：真机渲染存在亚像素抖动（同名文字边缘差可达 80），
    固定对齐的 RMSE 会把同页误判成异页。对 ±3px 平移取最小差值。"""
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(a_path).convert("L").resize((160, 40)), dtype=float)
    b = np.asarray(Image.open(b_path).convert("L").resize((160, 40)), dtype=float)
    best = float("inf")
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            bs = np.roll(np.roll(b, dy, axis=0), dx, axis=1)
            # 平移卷出的边缘条带不参与比较
            y0, y1 = max(0, dy), min(40, 40 + dy)
            x0, x1 = max(0, dx), min(160, 160 + dx)
            if y1 - y0 < 20 or x1 - x0 < 80:
                continue
            diff = a[y0:y1, x0:x1] - bs[y0:y1, x0:x1]
            best = min(best, float(np.sqrt((diff ** 2).mean())))
    return best


def assert_landing(shot: str, ref_key: str, refs_dir: str, dev_conf: dict,
                   recapture: bool = False,
                   reshoot=None) -> tuple[bool, str]:
    """标题区域与金标图比对。基线缺失时自动采基线并通过（附说明）。

    判定超限且提供 reshoot（重截图函数）时，等通知横幅消散后重拍复核一次。

    Returns: (通过?, 说明)
    """
    ref = os.path.join(refs_dir, f"{ref_key}.png")
    if not os.path.exists(ref) or recapture:
        os.makedirs(refs_dir, exist_ok=True)
        from shutil import copyfile
        copyfile(crop_title(shot, dev_conf), ref)
        return True, f"基线已建立（{ref_key}），本轮不判定"
    title = crop_title(shot, dev_conf)
    score = rmse(title, ref)
    threshold = float(dev_conf.get("rmse_threshold", 12.0))
    if score > threshold and reshoot is not None:
        time.sleep(3.5)  # 等通知横幅自动消散
        shot2 = reshoot()
        if shot2:
            title = crop_title(shot2, dev_conf)
            score = rmse(title, ref)
    if score <= threshold:
        return True, f"标题比对通过 rmse={score:.1f}"
    return False, f"落地页标题不符 rmse={score:.1f}（阈值 {threshold}），期望金标 {ref_key}"


def probe_row_device(mini, u2d, dev_conf, row, spec, evidence_dir,
                     adb_path: str, serial: str, refs_dir: str,
                     recapture_refs: bool = False) -> dict:
    """真机混合通道单行探针：u2 执行 + adb 截图 + 图像级落地断言。
    不可自动化场景自动打 [转人工] 标签（而非误判 fail）。"""
    item = {
        "row_id": row["row_id"], "status": "unverified", "error": None,
        "evidence": None, "consistency": None,
        "feature": row.get("feature"), "main_module": row.get("main_module"),
    }
    shot = os.path.join(evidence_dir, f"{row['row_id']}.png")
    try:
        def _reshot():
            try:
                adb_shot(adb_path, serial, shot)
                return shot if os.path.getsize(shot) > 0 else None
            except Exception:
                return None
        if spec is None:
            item["error"] = "选择器配置缺失（xysjg_selectors.json）"
            return item
        manual = spec.get("manual")
        if manual:  # 配置显式声明转人工（配置评审把关，防把产品缺陷藏进人工队列）
            item["error"] = f"[转人工]{manual.get('reason', '配置声明人工处理')}"
            item["consistency"] = manual.get("guide") or spec.get("note")
            return item
        if not ensure_session(mini, u2d, dev_conf, adb_path, serial, refs_dir):
            item["error"] = "[转人工]小程序不在前台或首页不可达（可能扫码后退出，请人工确认手机状态）"
            return item
        page = mini.app.get_current_page()
        kind = spec.get("probe_as") or row.get("probe_kind") or "reachable"
        dspec = spec.get("device") or {}

        err = None
        if not dspec.get("skip_data_assert"):
            # device 通道下 automator 是影子栈，页面数据不镜像真机——数据断言仅 IDE 模式强制执行；
            # 配置 device.skip_data_assert=true 的行（如素材下发类）在真机完全依赖图像断言。
            err = data_assert(page, spec)
        if err:
            item["status"] = "fail"
            item["error"] = err
            return item

        # wx API 打桩（如 scanCode 返回假桌码）
        for m, cfg in (dspec.get("mock") or {}).items():
            try:
                mini.app.mock_wx_method(m, result=cfg.get("result"),
                                        success=cfg.get("success", True))
            except Exception as exc:
                item["error"] = f"mock {m} 失败: {exc}"[:150]

        click = dspec.get("click")
        if kind == "visible":
            if click:
                device_click(mini, u2d, dev_conf, click)
                time.sleep(2.5)
                adb_shot(adb_path, serial, shot)
                if os.path.exists(shot) and os.path.getsize(shot) > 0:
                    item["evidence"] = shot
                ref_key = dspec.get("assert_img")
                if ref_key:
                    ok, note = assert_landing(shot, ref_key, refs_dir, dev_conf,
                                              recapture=recapture_refs,
                                              reshoot=lambda: _reshot())
                    if not ok:
                        item["status"] = "fail"
                        item["error"] = note
                        return item
                    item["consistency"] = "; ".join(filter(None, [spec.get("note"), note]))
                else:
                    expect = (dspec.get("expect_path_prefix") or "").strip("/")
                    landed = (wait_route(mini) or "").strip("/")
                    if expect and not landed.startswith(expect):
                        item["status"] = "fail"
                        item["error"] = f"落地页不符：期望 {expect}，实际 {landed}"
                        return item
            item["status"] = "pass"
            if not item["consistency"] and spec.get("probe_as") and spec.get("note"):
                item["consistency"] = spec["note"]
        elif kind == "navigate":
            if not click:
                # 自动打标兜底：无点击配置 = 当前不可自动化，转人工而非误报失败
                item["error"] = "[转人工]device 通道无点击配置（组件内入口需坐标，或需人工补验）"
                item["consistency"] = spec.get("note")
                return item
            device_click(mini, u2d, dev_conf, click)
            time.sleep(2.5)  # 真机页面渲染等待
            adb_shot(adb_path, serial, shot)
            if os.path.exists(shot) and os.path.getsize(shot) > 0:
                item["evidence"] = shot
            if not in_miniprogram(adb_path, serial):
                # 点击打开了原生页（扫码/支付等）：记录证据（原生页截图）并转人工
                u2d.press("back")
                time.sleep(1.0)
                item["error"] = "[转人工]点击进入了原生扫码/能力页，真实环节需人工完成"
                item["consistency"] = (spec.get("note") or "") + "；已定位到原生页并回退"
                return item
            # 图像级落地断言：标题区域与金标比对（automator 路由事件不穿透真机渲染层）
            ref_key = dspec.get("assert_img")
            if ref_key:
                ok, note = assert_landing(shot, ref_key, refs_dir, dev_conf,
                                          recapture=recapture_refs,
                                          reshoot=lambda: _reshot())
                if ok:
                    item["status"] = "pass"
                    item["consistency"] = "; ".join(filter(None, [spec.get("note"), note]))
                else:
                    item["status"] = "fail"
                    item["error"] = note
            else:
                landed = (wait_route(mini) or "").strip("/")
                expect = (dspec.get("expect_path_prefix") or "").strip("/")
                if not expect or landed.startswith(expect):
                    item["status"] = "pass"
                    if spec.get("note"):
                        item["consistency"] = spec["note"] + "（注：automator 路由仅影子栈，真机以截图为准）"
                else:
                    item["status"] = "fail"
                    item["error"] = f"落地页不符：期望 {expect} 前缀，实际 {landed}"
    except Exception as exc:
        item["status"] = "fail"
        item["error"] = f"{type(exc).__name__}: {exc}"[:400]
    finally:
        try:
            adb_shot(adb_path, serial, shot)
            if os.path.exists(shot) and os.path.getsize(shot) > 0:
                item["evidence"] = shot
        except Exception as exc:
            if item["status"] == "pass":
                item["status"] = "unverified"
            item["error"] = (item["error"] or "") + f"；截图失败: {exc}"[:200]
    return item


def probe_row(mini, row: dict, spec: dict | None, evidence_dir: str,
              dev_conf=None) -> dict:
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
        if not goto_page(mini, spec["page"], dev_conf=dev_conf):
            item["error"] = f"页面不可达：{spec['page']}"
            return item
        page = mini.app.get_current_page()
        kind = spec.get("probe_as") or row.get("probe_kind") or "reachable"

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
            # probe_as 降级的行必须把验证方式/覆盖边界写进 consistency，页面可查
            if not err and spec.get("probe_as") and spec.get("note"):
                item["consistency"] = spec["note"]
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
                    # tabBar 页 navigateTo 违法；三种合法跳法都试，吞错后以落点裁决
                    ok = False
                    # 非 tab 页 navigate_to 已实测最稳排第一；tab 页 navigateTo 违法，
                    # switch_tab 排第一。relaunch 会毒化会话状态，只作最后兜底。
                    if target in tab_pages_of(dev_conf):
                        attempts = ("switch_tab", "navigate_to", "relaunch")
                    else:
                        attempts = ("navigate_to", "relaunch", "switch_tab")
                    for attempt in attempts:
                        try:
                            if attempt == "switch_tab":
                                mini.app.switch_tab(target, is_wait_url_change=True,
                                                    wait_route_done_time=10)
                            elif attempt == "relaunch":
                                mini.app.relaunch(target)
                            else:
                                mini.app.navigate_to(target)
                        except Exception:
                            pass
                        time.sleep(2.5)
                        try:
                            cur = (getattr(mini.app.get_current_page(), "path", "")
                                   or "").strip("/")
                        except Exception:
                            time.sleep(1.5)
                            cur = (getattr(mini.app.get_current_page(), "path", "")
                                   or "").strip("/")
                        if cur == target.strip("/"):
                            ok = True
                            break
                    if note:
                        item["consistency"] = note
                    if not ok:
                        item["status"] = "fail"
                        item["error"] = (item["error"] or "") + \
                            f"落地页 {target} 经 switchTab/reLaunch/navigateTo 均未到达"
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
    ap.add_argument("--mini-project", default=None,
                    help="IDE 模式必填：小程序工程目录（项目相关，不进框架默认值）")
    ap.add_argument("--main-module", default=None, help="只跑某主模块，如 首页")
    ap.add_argument("--kinds", default="reachable,visible,navigate")
    ap.add_argument("--limit", type=int, default=0, help="最多跑 N 行（调试用）")
    ap.add_argument("--profile", default="miniprogram-probe",
                    help="项目画像名，建议用平台 project identifier")
    ap.add_argument("--username", default=None, help="缺省取 CHECKLIST_TOKEN；或现场登录")
    ap.add_argument("--password", default=None)
    ap.add_argument("--dev-tool", default=r"D:\Program Files (x86)\Tencent\微信web开发者工具\cli.bat")
    ap.add_argument("--auto-port", type=int, default=9420)
    ap.add_argument("--device", default=None,
                    help="真机混合通道：adb 序列号。挂进 IDE 自动化会话的手机，不重拉会话")
    ap.add_argument("--adb", default=r"D:\apps\android_sdk\platform-tools\adb.exe")
    ap.add_argument("--refs-dir", default=None,
                    help="金标图目录，默认 <selectors所在目录>/assert_refs（随项目配置走）")
    ap.add_argument("--recapture-refs", action="store_true",
                    help="重采金标基线（UI 变更后使用），采基线轮全部判定通过")
    args = ap.parse_args()

    if args.refs_dir is None:
        args.refs_dir = os.path.join(os.path.dirname(os.path.abspath(args.selectors)), "assert_refs")

    token = os.environ.get("CHECKLIST_TOKEN")
    if not token:
        if not (args.username and args.password):
            print("[error] 需要 CHECKLIST_TOKEN 环境变量或 --username/--password", file=sys.stderr)
            return 2
        token = login(args.base, args.username, args.password)
        print("[info] 已用账号密码登录取 token")

    selectors = {k: v for k, v in json.load(open(args.selectors, encoding="utf-8")).items()}
    ide_tab_pages = selectors.get("tab_pages", [])  # IDE 模式的 tabBar 名单（项目配置顶层）
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
    if args.device:
        # 真机混合通道：只连现有自动化会话（带 project_path 会重拉 cli auto，会弄断桥接）
        mini = minium.Minium({"test_port": args.auto_port})
        import uiautomator2 as u2
        u2d = u2.connect(args.device)
        dev_conf = selectors.get("_device", {})
        selectors = {k: v for k, v in selectors.items() if not k.startswith("_")}
        # 预检：小程序须在手机前台（荣耀系无障碍树不暴露微信 UI，u2 无法代开）
        for attempt in range(3):
            try:
                _ = mini.app.get_current_page().path
                break
            except Exception:
                time.sleep(2)
        else:
            print("[error] 手机前台未打开小程序（automator 无响应）。请在手机微信里重新打开"
                  "小杨生煎小程序（下拉最近小程序即可）后重试", file=sys.stderr)
            return 2
        print(f"[info] 真机混合通道已就绪 device={args.device} screen={dev_conf.get('screen')}")
    else:
        if not args.mini_project:
            print("[error] IDE 模式须显式 --mini-project <小程序工程目录>", file=sys.stderr)
            return 2
        mini = minium.Minium({
            "project_path": os.path.abspath(args.mini_project),
            "auto_port": args.auto_port,
            "dev_tool_path": args.dev_tool,
        })
        u2d, dev_conf = None, None
        time.sleep(4)  # 等模拟器就绪

    evidence_dir = os.path.join("probe-runs", time.strftime("%Y%m%d-%H%M%S"), "evidence")
    os.makedirs(evidence_dir, exist_ok=True)

    results = []
    for i, row in enumerate(rows, 1):
        spec = selectors.get(row["row_id"])
        if args.device:
            item = probe_row_device(mini, u2d, dev_conf, row, spec, evidence_dir,
                                    args.adb, args.device, args.refs_dir,
                                    recapture_refs=args.recapture_refs)
        else:
            item = probe_row(mini, row, spec, evidence_dir, dev_conf={"tab_pages": ide_tab_pages})
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
