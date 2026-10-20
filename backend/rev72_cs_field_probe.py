# -*- coding: utf-8 -*-
"""rev72：CS 建单表单字段定位器探测（补 NE19 的「无定位器」缺口）。

背景：TR-109 清单 form_skipped_no_locator=19，字段 Subject/Description/Attachment/
Call From/Call To/Case/Reasons/reply 在子功能 field_locators（9/24 probe，仅覆盖
New Case Summary 上半屏 13 字段）里没有条目。本脚本按同一登录/导航链路实测：
1) CS Cases → Create Case 页面三个页签（Summary / Retro Claim / GoodWill）
2) CS Retro Claim 菜单入口
逐页签导出：全部带 id 的控件 + .ant-form-item 的 label→id 映射 + 整页截图。
输出：backend/artifacts/rev72_cs_locators_probe.json
"""
import json
import sys
import time
from pathlib import Path

import ddddocr
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://console.uat.eatizen.com.hk/dtcloud-console-web-pc/#/login"
USERNAME = "lianqiao"
PASSWORD = "lianqiao123"
ART = Path(__file__).parent / "artifacts"
ART.mkdir(exist_ok=True)

ocr = ddddocr.DdddOcr(show_ad=False)


def ocr_captcha(page) -> str | None:
    img = page.locator('img[src^="data:image"]').first
    if not img.count():
        return None
    try:
        shot = img.screenshot(timeout=5000)
        text = ocr.classification(shot).decode("utf-8", "ignore").strip() if isinstance(
            ocr.classification(shot), bytes) else str(ocr.classification(shot)).strip()
        return text if len(text) >= 4 else None
    except Exception:
        return None


def login(page):
    for attempt in range(6):
        resp = page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        if resp and (resp.status >= 500 or resp.status in (403,)):
            print(f"ENVIRONMENT_BLOCKED http={resp.status}")
            return False
        user = page.locator("#login_username")
        user.wait_for(state="visible", timeout=30000)
        user.fill(USERNAME)
        page.locator("#login_password").fill(PASSWORD)
        # 元素截图 + ddddocr（base64 直取会损坏）
        img = page.locator('img[src^="data:image"]').first
        shot = img.screenshot(timeout=5000)
        code = ocr.classification(shot)
        code = code.decode("utf-8", "ignore") if isinstance(code, bytes) else str(code)
        code = (code or "").strip()
        if len(code) < 4:
            continue
        page.locator("#login_validateCode").fill(code)
        page.locator('button:has-text("Login Immediately"), button.ant-btn-primary').first.click()
        page.wait_for_timeout(6000)
        if "/login" not in page.url:
            print(f"login OK (attempt {attempt + 1}) url={page.url}")
            return True
    return False


def open_crm(page, context):
    with context.expect_page(timeout=20000) as pop:
        pass  # 仅占位：expect_page 不能这样用，改轮询
    return None


def open_crm_poll(page, context):
    new_page = {}
    context.on("page", lambda p: new_page.setdefault("p", p))
    for _ in range(3):
        clicked = False
        for loc in (
            page.get_by_text("CRM", exact=True).first,
            page.get_by_role("link", name="CRM").first,
            page.get_by_role("button", name="CRM").first,
        ):
            try:
                if loc.count() and loc.is_visible():
                    loc.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue
        page.wait_for_timeout(4000)
        if clicked:
            break
    app = new_page.get("p") or page
    app.wait_for_load_state("domcontentloaded", timeout=60000)
    app.wait_for_function(
        "() => /Customer Service|Member Center/.test(document.body.innerText) || document.querySelectorAll('iframe').length > 0",
        timeout=60000,
    )
    app.wait_for_timeout(3000)
    return app


def get_frame(app):
    for _ in range(10):
        el = app.query_selector("iframe")
        if el:
            fr = el.content_frame()
            if fr is not None:
                return fr
        app.wait_for_timeout(1000)
    return app  # 未嵌入 iframe 时退回主页面


def expand_menu(fr, parent, child_labels, timeout_ms=20000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        titles = fr.locator(".ant-menu-submenu-title", has_text=parent)
        if titles.count():
            t = titles.first
            if t.get_attribute("aria-expanded") == "true":
                break
            try:
                t.click(timeout=5000)
            except Exception:
                try:
                    t.hover(timeout=3000)
                except Exception:
                    pass
        fr.wait_for_timeout(1200)
    # 点子菜单
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for label in child_labels:
            for sel in (f'.ant-menu-item:has-text("{label}")',):
                item = fr.locator(sel).first
                try:
                    if item.count() and item.is_visible():
                        item.click(timeout=5000)
                        fr.wait_for_timeout(3000)
                        return True
                except Exception:
                    continue
        fr.wait_for_timeout(800)
    return False


def dump_page(fr, tag, shots):
    fr.wait_for_timeout(2500)
    result = {"tag": tag, "url": None, "ids": [], "labels": []}
    try:
        result["url"] = fr.evaluate("() => location.href")
    except Exception:
        pass
    try:
        result["ids"] = fr.eval_on_selector_all(
            "[id]",
            "els => els.map(e => ({id: e.id, tag: e.tagName,"
            " type: e.getAttribute('type'), ph: e.getAttribute('placeholder'),"
            " ro: e.getAttribute('readonly') !== null || e.getAttribute('disabled') !== null,"
            " cls: (e.className||'').toString().slice(0,50), vis: !!(e.offsetWidth||e.offsetHeight)}))",
        )
    except Exception as exc:
        result["ids_error"] = str(exc)
    try:
        result["labels"] = fr.eval_on_selector_all(
            ".ant-form-item",
            "items => items.map(it => {"
            " const lb = it.querySelector('.ant-form-item-label label');"
            " const ctl = it.querySelector('input, textarea, [id]');"
            " return {label: lb ? lb.innerText.trim() : '',"
            " id: ctl ? (ctl.id || '') : '',"
            " tag: ctl ? ctl.tagName : '',"
            " ro: ctl ? (ctl.getAttribute('readonly') !== null) : false};})",
        )
    except Exception as exc:
        result["labels_error"] = str(exc)
    try:
        # 页签列表（供后续逐页签枚举）
        result["tabs"] = fr.eval_on_selector_all(
            ".ant-tabs-tab, [role='tab']",
            "els => els.map(e => e.innerText.trim()).filter(Boolean)",
        )
    except Exception:
        result["tabs"] = []
    shot = ART / f"rev72_probe_{tag}.png"
    try:
        fr.page.screenshot(path=str(shot), full_page=False)
        shots.append(str(shot))
    except Exception:
        pass
    return result


def click_tab(fr, text):
    tab = fr.locator(f".ant-tabs-tab:has-text('{text}')").first
    if tab.count():
        try:
            tab.click(timeout=5000)
            fr.wait_for_timeout(2000)
            return True
        except Exception:
            pass
    return False


def main():
    out = {"pages": [], "screenshots": []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        if not login(page):
            print("LOGIN_FAILED")
            browser.close()
            sys.exit(2)
        app = open_crm_poll(page, context)
        fr = get_frame(app)
        print("frame url:", fr.evaluate("() => location.href") if fr != app else "(main page)")

        # 1) CS Cases → Create Case
        expand_menu(fr, "Customer Service", ["CS Cases", "Cases", "Case List"])
        fr.wait_for_timeout(3000)
        out["pages"].append(dump_page(fr, "cs_cases_list", out["screenshots"]))
        # 列表页 Create 按钮
        clicked = False
        for sel in ("button:has-text('Create')", "button:has-text('New')", "button:has-text('Add')"):
            btn = fr.locator(sel).first
            try:
                if btn.count() and btn.is_visible():
                    btn.click(timeout=5000)
                    clicked = True
                    fr.wait_for_timeout(4000)
                    break
            except Exception:
                continue
        print("create clicked:", clicked)
        page_create = dump_page(fr, "cs_case_create", out["screenshots"])
        out["pages"].append(page_create)
        # 逐页签
        for tab in ("Retro Claim", "GoodWill"):
            if click_tab(fr, tab):
                out["pages"].append(dump_page(fr, f"cs_case_create_{tab.replace(' ', '_')}", out["screenshots"]))

        # 2) CS Retro Claim 菜单
        expand_menu(fr, "Customer Service", ["CS Retro Claim", "Retro Claim"])
        fr.wait_for_timeout(3000)
        out["pages"].append(dump_page(fr, "cs_retro_list", out["screenshots"]))
        for sel in ("button:has-text('Create')", "button:has-text('New')", "button:has-text('Add')"):
            btn = fr.locator(sel).first
            try:
                if btn.count() and btn.is_visible():
                    btn.click(timeout=5000)
                    fr.wait_for_timeout(4000)
                    break
            except Exception:
                continue
        out["pages"].append(dump_page(fr, "cs_retro_create", out["screenshots"]))

        browser.close()
    dst = ART / "rev72_cs_locators_probe.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", dst)
    # 摘要：目标字段的命中情况
    targets = ["Subject", "Description", "Attachment", "Attachments", "Call From", "Call To", "Case", "Reasons", "reply", "Reply"]
    for p in out["pages"]:
        hits = [lb for lb in p.get("labels", []) if any(t.lower() in (lb.get("label") or "").lower() for t in targets)]
        print(f"== {p['tag']} labels={len(p.get('labels', []))} ids={len(p.get('ids', []))} 目标命中: {hits}")


if __name__ == "__main__":
    main()
