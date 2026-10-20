# -*- coding: utf-8 -*-
"""rev92：定位 Brand/Service Mode 下拉读不到选项的根因——点开 #brand 看 dropdown 是否出现。"""
import sys
import time
from pathlib import Path

import ddddocr
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://console.uat.eatizen.com.hk/dtcloud-console-web-pc/#/login"
ART = Path(__file__).parent / "artifacts"
ocr = ddddocr.DdddOcr(show_ad=False)


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
            print(f"login OK attempt {attempt+1}")
            return True
    return False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        if not login(page):
            print("LOGIN_FAILED"); browser.close(); sys.exit(2)
        # 进 CRM
        new_page = {}
        context.on("page", lambda p: new_page.setdefault("p", p))
        clicked = False
        for _ in range(3):
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
        # Customer Service → CS Cases → Create
        for _ in range(12):
            t = fr.locator(".ant-menu-submenu-title", has_text="Customer Service").first
            try:
                if t.count() and t.get_attribute("aria-expanded") != "true":
                    t.click(timeout=4000)
            except Exception:
                pass
            fr.wait_for_timeout(1000)
        for label in ("CS Cases", "Cases"):
            item = fr.locator(f'.ant-menu-item:has-text("{label}")').first
            try:
                if item.count() and item.is_visible():
                    item.click(timeout=5000); break
            except Exception:
                continue
        # 等列表 + 点 Create
        deadline = time.time() + 150
        while time.time() < deadline:
            if fr.locator(".ant-form-item").count() > 0 and fr.locator(".ant-spin-spinning").count() == 0:
                break
            fr.wait_for_timeout(1000)
        fr.wait_for_timeout(2000)
        btn = fr.locator("button:has-text('Create')").first
        if btn.count() and btn.is_visible():
            btn.click(timeout=5000)
        deadline = time.time() + 150
        while time.time() < deadline:
            if fr.locator(".ant-form-item").count() > 5 and fr.locator(".ant-spin-spinning").count() == 0:
                break
            fr.wait_for_timeout(1000)
        fr.wait_for_timeout(2000)
        print("create page form items:", fr.locator(".ant-form-item").count())

        for fid, label in (("#brand", "Brand"), ("#serviceMode", "Service Mode"),
                           ("#businessDate?", "BusinessDate-skip")):
            if "?" in fid:
                continue
            root = fr.locator(fid).first
            print(f"== {label} {fid}: count={await_cnt(root)}")
            if not await_cnt(root):
                continue
            root.click(timeout=8000)
            fr.wait_for_timeout(1500)
            dds = fr.locator(".ant-select-dropdown:visible")
            n = dds.count()
            print(f"   visible dropdowns: {n}")
            if n:
                items = dds.nth(n - 1).locator(".ant-select-item-option")
                cnt = items.count()
                texts = []
                for i in range(min(cnt, 20)):
                    texts.append(items.nth(i).inner_text(timeout=3000).strip())
                print(f"   options({cnt}): {texts}")
            # 关闭
            fr.keyboard.press("Escape")
            fr.wait_for_timeout(500)
        browser.close()


def await_cnt(loc):
    try:
        return loc.count()
    except Exception:
        return 0


if __name__ == "__main__":
    main()
