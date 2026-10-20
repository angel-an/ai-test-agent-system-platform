# -*- coding: utf-8 -*-
"""rev74：CS 表单 DOM 探针（扩展 rev72）。
覆盖：1) Create Case 整页（等加载完，含 Retro Claim / GoodWill 页签）
      2) Case 详情 Timeline：New Post / Task / PhoneCall / Note 各弹窗/新页
输出：backend/artifacts/rev74_cs_locators_probe.json + 截图
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


def ocr_captcha(page):
    img = page.locator('img[src^="data:image"]').first
    if not img.count():
        return None
    try:
        shot = img.screenshot(timeout=5000)
        text = ocr.classification(shot)
        text = text.decode("utf-8", "ignore") if isinstance(text, bytes) else str(text)
        return text.strip() if len(text.strip()) >= 4 else None
    except Exception:
        return None


def login(page):
    for attempt in range(6):
        resp = page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        if resp and (resp.status >= 500 or resp.status == 403):
            print(f"ENVIRONMENT_BLOCKED http={resp.status}")
            return False
        user = page.locator("#login_username")
        user.wait_for(state="visible", timeout=30000)
        user.fill(USERNAME)
        page.locator("#login_password").fill(PASSWORD)
        code = ocr_captcha(page)
        if not code:
            continue
        page.locator("#login_validateCode").fill(code)
        page.locator('button:has-text("Login Immediately"), button.ant-btn-primary').first.click()
        page.wait_for_timeout(6000)
        if "/login" not in page.url:
            print(f"login OK (attempt {attempt + 1})")
            return True
    return False


def open_crm_poll(page, context):
    new_page = {}
    context.on("page", lambda p: new_page.setdefault("p", p))
    for _ in range(3):
        clicked = False
        for loc in (page.get_by_text("CRM", exact=True).first,
                    page.get_by_role("link", name="CRM").first,
                    page.get_by_role("button", name="CRM").first):
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
        timeout=60000)
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
    return app


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
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for label in child_labels:
            item = fr.locator(f'.ant-menu-item:has-text("{label}")').first
            try:
                if item.count() and item.is_visible():
                    item.click(timeout=5000)
                    fr.wait_for_timeout(3000)
                    return True
            except Exception:
                continue
        fr.wait_for_timeout(800)
    return False


def wait_ready(fr, timeout_ms=150000):
    """等页面真正渲染出表单（转圈消失 + 有表单控件或大量 id）。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            n_items = fr.locator(".ant-form-item").count()
            spinning = fr.locator(".ant-spin-spinning").count()
            if n_items > 0 and spinning == 0:
                return True
        except Exception:
            pass
        fr.wait_for_timeout(1000)
    return False


def dump(fr, tag, shots):
    fr.wait_for_timeout(1500)
    result = {"tag": tag}
    try:
        result["url"] = fr.evaluate("() => location.href")
    except Exception:
        pass
    try:
        result["labels"] = fr.eval_on_selector_all(
            ".ant-form-item",
            "items => items.map(it => {"
            " const lb = it.querySelector('.ant-form-item-label label');"
            " const ctl = it.querySelector('input, textarea, [id]');"
            " return {label: lb ? lb.innerText.trim() : '',"
            " id: ctl ? (ctl.id || '') : '',"
            " tag: ctl ? ctl.tagName : '',"
            " ro: ctl ? (ctl.getAttribute('readonly') !== null) : false};})")
    except Exception as exc:
        result["labels_error"] = str(exc)
    try:
        result["ids"] = fr.eval_on_selector_all(
            "[id]",
            "els => els.map(e => ({id: e.id, tag: e.tagName,"
            " type: e.getAttribute('type'), ph: e.getAttribute('placeholder'),"
            " ro: e.getAttribute('readonly') !== null})).filter(x => x.id)")
    except Exception:
        pass
    try:
        result["buttons"] = fr.eval_on_selector_all(
            "button", "els => els.map(e => e.innerText.trim()).filter(Boolean)")
    except Exception:
        pass
    try:
        result["clickables"] = fr.eval_on_selector_all(
            "a, [role='button'], .ant-btn, .ant-tabs-tab",
            "els => els.map(e => e.innerText.trim()).filter(t => t && t.length < 30)")
    except Exception:
        pass
    shot = ART / f"rev74_{tag}.png"
    try:
        fr.page.screenshot(path=str(shot))
        shots.append(str(shot))
    except Exception:
        pass
    return result


def click_tab(fr, text):
    for sel in (f".ant-tabs-tab:has-text('{text}')", f"[role='tab']:has-text('{text}')"):
        tab = fr.locator(sel).first
        try:
            if tab.count() and tab.is_visible():
                tab.click(timeout=5000)
                fr.wait_for_timeout(2000)
                return True
        except Exception:
            continue
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

        # ---- 1) CS Cases 列表 ----
        expand_menu(fr, "Customer Service", ["CS Cases", "Cases", "Case List"])
        wait_ready(fr)
        out["pages"].append(dump(fr, "cs_cases_list", out["screenshots"]))

        # ---- 2) Create Case 整页（等加载完 + 页签） ----
        clicked = False
        for sel in ("button:has-text('Create')", "button:has-text('New Case')",
                    "button:has-text('Add')"):
            btn = fr.locator(sel).first
            try:
                if btn.count() and btn.is_visible():
                    btn.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue
        print("create clicked:", clicked)
        wait_ready(fr)
        out["pages"].append(dump(fr, "cs_case_create", out["screenshots"]))
        for tab in ("Retro Claim", "GoodWill", "RetroClaim"):
            if click_tab(fr, tab):
                out["pages"].append(dump(fr, f"create_tab_{tab.replace(' ', '_')}", out["screenshots"]))

        # ---- 3) 回列表 → 打开第一条 Case 详情 ----
        expand_menu(fr, "Customer Service", ["CS Cases", "Cases", "Case List"])
        wait_ready(fr)
        opened = False
        for sel in (".ant-table-row a", ".ant-table-row td:nth-child(2) a",
                    ".ant-table-tbody tr:first-child a"):
            link = fr.locator(sel).first
            try:
                if link.count() and link.is_visible():
                    link.click(timeout=5000)
                    opened = True
                    break
            except Exception:
                continue
        print("case detail opened:", opened)
        wait_ready(fr)
        fr.wait_for_timeout(3000)
        out["pages"].append(dump(fr, "cs_case_detail", out["screenshots"]))
        detail = out["pages"][-1]
        print("detail buttons:", (detail.get("buttons") or [])[:30])

        # ---- 4) Timeline 各弹窗 ----
        # 4a. Post（弹窗）
        for label in ("New Post", "Post", "新增Post"):
            btn = fr.locator(f"button:has-text('{label}')").first
            try:
                if btn.count() and btn.is_visible():
                    btn.click(timeout=5000)
                    break
            except Exception:
                continue
        fr.wait_for_timeout(3000)
        out["pages"].append(dump(fr, "timeline_post_modal", out["screenshots"]))
        fr.page.keyboard.press("Escape")
        fr.wait_for_timeout(1500)

        # 4b. Task（可能新页/弹窗）
        for label in ("New Task", "Task", "新增Task"):
            btn = fr.locator(f"button:has-text('{label}')").first
            try:
                if btn.count() and btn.is_visible():
                    btn.click(timeout=5000)
                    break
            except Exception:
                continue
        fr.wait_for_timeout(3000)
        out["pages"].append(dump(fr, "timeline_task", out["screenshots"]))
        # 回详情（若跳新页则后退）
        try:
            if "timeline" not in (fr.evaluate("() => location.href") or ""):
                pass
        except Exception:
            pass

        # 4c. PhoneCall
        for label in ("Phone Call", "PhoneCall", "New Phone", "Call"):
            btn = fr.locator(f"button:has-text('{label}')").first
            try:
                if btn.count() and btn.is_visible():
                    btn.click(timeout=5000)
                    break
            except Exception:
                continue
        fr.wait_for_timeout(3000)
        out["pages"].append(dump(fr, "timeline_phonecall", out["screenshots"]))

        # 4d. Note
        for label in ("New Note", "Note", "新增Note"):
            btn = fr.locator(f"button:has-text('{label}')").first
            try:
                if btn.count() and btn.is_visible():
                    btn.click(timeout=5000)
                    break
            except Exception:
                continue
        fr.wait_for_timeout(3000)
        out["pages"].append(dump(fr, "timeline_note_modal", out["screenshots"]))

        browser.close()
    dst = ART / "rev74_cs_locators_probe.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", dst)
    for p in out["pages"]:
        print(f"== {p['tag']} url={p.get('url')} labels={len(p.get('labels') or [])} "
              f"ids={len(p.get('ids') or [])}")


if __name__ == "__main__":
    main()
