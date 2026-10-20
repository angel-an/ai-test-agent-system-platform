# rev68 运行详情页验证：登录 → 打开 PR-2 TR-94 详情 → 确认用例行渲染 latest_reason
import json
import os
import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:3000"
PROJECT = "PR-2"
RUN = "TR-94"
# 凭证从环境变量读取（与后端 execution_config 同名约定），不落明文
USERNAME = os.environ.get("WEB_TEST_USERNAME", "")
PASSWORD = os.environ.get("WEB_TEST_PASSWORD", "")

def main():
    if not USERNAME or not PASSWORD:
        print("[SKIP] 未设置 WEB_TEST_USERNAME / WEB_TEST_PASSWORD")
        sys.exit(2)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        # 登录
        page.goto(f"{BASE}/login", wait_until="networkidle")
        page.fill('input[type="text"], input[name="username"], #username', USERNAME)
        page.fill('input[type="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_url("**/projects**", timeout=20000)
        print("[OK] login")
        # 打开运行详情
        page.goto(f"{BASE}/projects/{PROJECT}/test-runs/{RUN}", wait_until="networkidle")
        page.wait_for_timeout(3000)
        body = page.inner_text("body")
        checks = {
            "用例区块标题": "测试用例" in body,
            "用例数文案": ("32 个测试用例" in body) or ("32" in body),
            "原因明细(必填校验提示)": "必填校验提示" in body,
            "原因明细(需人工执行)": "需人工执行" in body,
            "未验证计数": "未验证" in body,
        }
        for k, v in checks.items():
            print(("PASS" if v else "FAIL"), k)
        page.screenshot(path="backend/rev68_run_detail.png", full_page=False)
        print("[SHOT] backend/rev68_run_detail.png")
        # 断言核心两项
        ok = checks["用例区块标题"] and checks["原因明细(必填校验提示)"]
        browser.close()
        sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
