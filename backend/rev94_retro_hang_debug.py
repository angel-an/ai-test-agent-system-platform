# -*- coding: utf-8 -*-
"""rev94：定位 retro 页面交互挂点——逐步打时间戳复现执行器动作。"""
import sys
import time
from pathlib import Path

import ddddocr
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://console.uat.eatizen.com.hk/dtcloud-console-web-pc/#/login"
ocr = ddddocr.DdddOcr(show_ad=False)
T0 = time.time()


def log(msg):
    print(f"[{time.time()-T0:7.1f}s] {msg}", flush=True)


def ocr_captcha(page):
    img = page.locator('img[src^="data:image"]').first
    if not img.count():
        return None
    try:
        shot = img.screenshot(timeout=5000)
        t = ocr.classification(shot)
        t = t.decode("utf-8", "ignore") if isinstance(t, bytes) else str(t)
        return t.strip() if len(t.strip()) >= 4 else None
    except Exception:
        return None


def login(page):
    for attempt in range(15):
        resp = page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        if resp and (resp.status >= 500 or resp.status == 403):
            return False
        user = page.locator("#login_username")
        user.wait_for(state="visible", timeout=30000)
        user.fill("lianqiao")
        page.locator("#login_password").fill("lianqiao123")
        code = ocr_captcha(page)
        if not code:
            continue
        page.locator("#login_validateCode").fill(code)
        page.locator('button:has-text("Login Immediately"), button.ant-btn-primary').first.click()
        page.wait_for_timeout(6000)
        if "/login" not in page.url:
            log(f"login OK attempt {attempt+1}")
            return True
    return False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        if not login(page):
            log("LOGIN_FAILED"); browser.close(); sys.exit(2)
        new_page = {}
        context.on("page", lambda p: new_page.setdefault("p", p))
        for _ in range(3):
            clicked = False
            for loc in (page.get_by_text("CRM", exact=True).first,
                        page.get_by_role("link", name="CRM").first):
                try:
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=5000); clicked = True; break
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
        fr = None
        for _ in range(10):
            el = app.query_selector("iframe")
            if el:
                fr = el.content_frame()
                if fr is not None:
                    break
            app.wait_for_timeout(1000)
        if fr is None:
            fr = app
        # 直达 retro 创建页（Frame 级导航，与执行器 openForm 的 direct_url 一致）
        frames = page.frames
        fr2 = next((f for f in frames if f.parent_frame and "eatizen" in f.url), None)
        if fr2 is None:
            log("no crm frame"); browser.close(); sys.exit(3)
        u = fr2.url.split("#")[0]
        fr2.goto(u + "#/cs/retro-claim/create", timeout=30000)
        fr2.wait_for_timeout(6000)
        fr = fr2
        log(f"retro page items={fr.locator('.ant-form-item').count()}")

        # --- Business Date 交互复现 ---
        log("BD: click #businessDate")
        fr.locator("#businessDate").first.click(timeout=10000)
        fr.wait_for_timeout(1200)
        v4 = fr.locator(".ant-picker-dropdown:visible")
        v3 = fr.locator(".ant-calendar-picker-container:visible")
        log(f"BD: panels v4={v4.count()} v3={v3.count()}")
        cell = fr.locator(".ant-picker-dropdown:visible .ant-picker-cell-inner, .ant-calendar-picker-container:visible .ant-calendar-date").first
        log(f"BD: cell count={cell.count()}")
        if cell.count():
            cell.click(timeout=8000)
            fr.wait_for_timeout(800)
            log("BD: date picked")
        root = fr.locator("#businessDate").first
        clear_icon = root.locator(".ant-picker-clear, .ant-calendar-picker-clear").first
        log(f"BD: clear icon count={clear_icon.count()}")
        root.hover(timeout=5000)
        fr.wait_for_timeout(500)
        if clear_icon.count():
            clear_icon.click(timeout=8000)
            fr.wait_for_timeout(500)
            log("BD: cleared")

        # --- Approve select 交互复现 ---
        log("AP: locate Approve form item")
        item = fr.locator('.ant-form-item:has-text("Approve") .ant-select').first
        log(f"AP: root count={item.count()}")
        if item.count():
            item.click(timeout=10000)
            fr.wait_for_timeout(1000)
            dd = fr.locator(".ant-select-dropdown:visible")
            n = dd.count()
            opts = 0
            if n:
                opts = dd.nth(n - 1).locator(".ant-select-item-option, .ant-select-dropdown-menu-item").count()
            log(f"AP: dropdown visible={n} options={opts}")
            fr.page.keyboard.press("Escape")
            fr.wait_for_timeout(400)

        # --- Reasons 填清 ---
        log("RS: fill reasons")
        fr.locator("#reasons").first.fill("1", timeout=8000)
        fr.wait_for_timeout(300)
        fr.locator("#reasons").first.fill("", timeout=8000)
        fr.locator("#reasons").first.blur(timeout=8000)
        fr.wait_for_timeout(500)
        log("RS: done")

        # --- Save 点击观察 ---
        log("SAVE: click top Save")
        save = fr.locator("button:visible").filter(has_text="Save").first
        log(f"SAVE: count={save.count()}")
        if save.count():
            save.click(timeout=8000)
            fr.wait_for_timeout(3000)
            log(f"SAVE: clicked; spinner={fr.locator('.ant-spin-spinning').count()}")
            fr.wait_for_timeout(5000)
            log(f"SAVE: +8s spinner={fr.locator('.ant-spin-spinning').count()}")
        log("done")
        browser.close()


if __name__ == "__main__":
    main()
