# -*- coding: utf-8 -*-
"""rev75：Timeline 「+ Add」菜单 → Post/Task/Phone Call/Note 逐个导出控件。
前置链路同 rev74（登录 → CRM iframe → CS Cases → 首条详情）。"""
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
    for attempt in range(15):
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
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            if fr.locator(".ant-form-item").count() > 0 and fr.locator(".ant-spin-spinning").count() == 0:
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
    # 弹窗优先（ant-modal），其次整页
    scope = fr.locator(".ant-modal-content").first
    try:
        if scope.count() and scope.is_visible():
            result["modal"] = scope.eval_on_selector_all(
                ".ant-form-item",
                "items => items.map(it => {"
                " const lb = it.querySelector('.ant-form-item-label label');"
                " const ctl = it.querySelector('input, textarea, [id]');"
                " return {label: lb ? lb.innerText.trim() : '',"
                " id: ctl ? (ctl.id || '') : '',"
                " tag: ctl ? ctl.tagName : '',"
                " ro: ctl ? (ctl.getAttribute('readonly') !== null) : false};})")
            result["modal_ids"] = scope.eval_on_selector_all(
                "[id]",
                "els => els.map(e => ({id: e.id, tag: e.tagName,"
                " ph: e.getAttribute('placeholder'),"
                " ro: e.getAttribute('readonly') !== null})).filter(x => x.id)")
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
    except Exception:
        pass
    try:
        result["ids"] = fr.eval_on_selector_all(
            "[id]",
            "els => els.map(e => ({id: e.id, tag: e.tagName,"
            " ph: e.getAttribute('placeholder'),"
            " ro: e.getAttribute('readonly') !== null})).filter(x => x.id)")
    except Exception:
        pass
    try:
        result["menu_items"] = fr.eval_on_selector_all(
            ".ant-dropdown-menu-item, .ant-menu-item",
            "els => els.map(e => e.innerText.trim()).filter(Boolean)")
    except Exception:
        pass
    shot = ART / f"rev76_{tag}.png"
    try:
        fr.page.screenshot(path=str(shot))
        shots.append(str(shot))
    except Exception:
        pass
    return result


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

        expand_menu(fr, "Customer Service", ["CS Cases", "Cases", "Case List"])
        wait_ready(fr)
        for sel in (".ant-table-row a", ".ant-table-tbody tr:first-child a"):
            link = fr.locator(sel).first
            try:
                if link.count() and link.is_visible():
                    link.click(timeout=5000)
                    break
            except Exception:
                continue
        wait_ready(fr)
        fr.wait_for_timeout(3000)
        out["pages"].append(dump(fr, "case_detail", out["screenshots"]))

        # Timeline + Add → 菜单
        add = fr.locator("text=+ Add").first
        if not add.count():
            add = fr.get_by_text("Add", exact=True).first
        add.click(timeout=5000)
        fr.wait_for_timeout(2000)
        out["pages"].append(dump(fr, "timeline_add_menu", out["screenshots"]))

        # 逐个点击菜单项（Post 已采集，跳过）
        for item in ("Phone", "Note"):
            menu = fr.locator(
                f".ant-dropdown-menu-item:has-text('{item}'), "
                f".ant-dropdown-menu-submenu-title:has-text('{item}')").first
            try:
                if menu.count() and menu.is_visible():
                    menu.click(timeout=5000)
                    fr.wait_for_timeout(3000)
                    out["pages"].append(dump(fr, f"timeline_{item.replace(' ', '_')}", out["screenshots"]))
                    # 关闭弹窗 / 回详情
                    fr.page.keyboard.press("Escape")
                    fr.wait_for_timeout(1200)
                    if "case/edit" not in (fr.evaluate("() => location.href") or ""):
                        fr.page.go_back()
                        wait_ready(fr)
                        fr.wait_for_timeout(2000)
                        # 重新打开 Add 菜单
                        add = fr.locator("text=+ Add").first
                        if not add.count():
                            add = fr.get_by_text("Add", exact=True).first
                        add.click(timeout=5000)
                        fr.wait_for_timeout(2000)
            except Exception as exc:
                print(f"menu {item} fail: {exc}")

        # ---- GoodWill 独立创建页 ----
        expand_menu(fr, "Customer Service", ["CS GoodWill", "GoodWill"])
        wait_ready(fr)
        fr.wait_for_timeout(2000)
        for sel in ("button:has-text('Create')", "button:has-text('New')", "button:has-text('Add')"):
            btn = fr.locator(sel).first
            try:
                if btn.count() and btn.is_visible():
                    btn.click(timeout=5000)
                    break
            except Exception:
                continue
        wait_ready(fr)
        fr.wait_for_timeout(2500)
        out["pages"].append(dump(fr, "goodwill_create", out["screenshots"]))
        browser.close()
    dst = ART / "rev76_timeline_probe.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", dst)
    for p in out["pages"]:
        print(f"== {p['tag']} url={p.get('url')} modal={len(p.get('modal') or [])} "
              f"labels={len(p.get('labels') or [])} menu={p.get('menu_items')}")


if __name__ == "__main__":
    main()
