from __future__ import annotations

import os
from typing import Any, Iterator

import httpx
import pytest

from endpoints import CONSOLE_GW, MARKETING_GW


def _env_gate(name: str, fallback: str | None = None) -> bool:
    """门控开关：优先使用平台标准命名，兼容旧 PR2_ 前缀。

    示例：_env_gate("LIVE_API") 会先查 LIVE_API，再查 PR2_LIVE_API。
    """
    val = os.getenv(name)
    if val is not None:
        return val == "1"
    if fallback is not None:
        return os.getenv(fallback, "0") == "1"
    # 自动推导 PR2_ 前缀兼容
    return os.getenv(f"PR2_{name}", "0") == "1"

API_BASE_URL = os.getenv("PR2_API_BASE_URL", MARKETING_GW)
PROJECT_ID = os.getenv("PR2_PROJECT_ID", "PR-2")
STORAGE_ACCOUNT_NO = os.getenv("PR2_STORAGE_ACCOUNT_NO", "18320101265")
NON_MEMBER_NO = os.getenv("PR2_NON_MEMBER_NO", "19900000001")
# 门控开关：优先使用平台标准命名 LIVE_API，兼容旧命名 PR2_LIVE_API
LIVE = _env_gate("LIVE_API")
REQUEST_TIMEOUT_MS = int(os.getenv("PR2_REQUEST_TIMEOUT_MS", "60000"))
ACCESS_TOKEN = os.getenv("PR2_RUNTIME_ACCESS_TOKEN", os.getenv("PR2_ACCESS_TOKEN", "")).strip()
COOKIE_HEADER = os.getenv("PR2_RUNTIME_COOKIE_HEADER", os.getenv("PR2_COOKIE_HEADER", "")).strip()
FORCE_BROWSER_LOGIN = os.getenv("PR2_FORCE_BROWSER_LOGIN", "0") == "1"
REFERER_URL = os.getenv(
    "PR2_RUNTIME_REFERER_URL",
    os.getenv(
    "PR2_REFERER_URL",
    "https://imarketing-uat.xysjg.com/front/saas-imarketing-web-pc/?userName=andan",
    )
).strip()
_USER = os.getenv("PR2_API_USER", "lianqiao")
_PWD = os.getenv("PR2_API_PASSWORD", "lianqiao123")
_LOGIN = os.getenv("PR2_CONSOLE_LOGIN_URL",
                   "https://console-uat.xysjg.com/front/dtcloud-console-web-pc/#/login")


class Resp:
    """极简响应包装（status_code + json()）。"""

    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        return self._body


class PlaywrightApiClient:
    """浏览器会话内 API 客户端：同 cookies/TLS；网关按 referer(含 accessToken) 识别实例。"""

    def __init__(self, context, marketing_url: str, access_token: str):
        self._ctx = context
        self._base = MARKETING_GW
        self._headers = {
            "access-token": access_token,
            "referer": marketing_url,
            "content-type": "application/json;charset=UTF-8",
        }

    def _url(self, path: str, params: dict | None = None) -> str:
        url = self._base + path
        if params:
            import urllib.parse
            url += "?" + urllib.parse.urlencode(params)
        return url

    def post(self, path: str, json: Any = None, **kwargs) -> Resp:
        import json as _json
        resp = self._ctx.request.post(self._url(path), data=_json.dumps(json),
                                      headers=self._headers, timeout=REQUEST_TIMEOUT_MS)
        return Resp(resp.status, _safe(resp))

    def get(self, path: str, params: dict | None = None, **kwargs) -> Resp:
        resp = self._ctx.request.get(self._url(path, params), headers=self._headers,
                                     timeout=REQUEST_TIMEOUT_MS)
        return Resp(resp.status, _safe(resp))

    def put(self, url: str, **kwargs) -> Resp:
        kwargs.setdefault("timeout", REQUEST_TIMEOUT_MS)
        resp = self._ctx.request.put(url, **kwargs)
        return Resp(resp.status, _safe(resp))


class DirectHttpApiClient:
    """已知 access-token/cookie 时的直连客户端。"""

    def __init__(self, access_token: str, cookie_header: str, referer_url: str):
        self._client = httpx.Client(
            base_url=MARKETING_GW,
            timeout=REQUEST_TIMEOUT_MS / 1000.0,
            headers={
                "access-token": access_token,
                "referer": referer_url,
                "origin": "https://imarketing-uat.xysjg.com",
                "content-type": "application/json;charset=UTF-8",
                "accept": "application/json, text/plain, */*",
                "cookie": cookie_header,
            },
            follow_redirects=True,
        )

    def post(self, path: str, json: Any = None, **kwargs) -> Resp:
        resp = self._client.post(path, json=json, **kwargs)
        return Resp(resp.status_code, _safe_httpx(resp))

    def get(self, path: str, params: dict | None = None, **kwargs) -> Resp:
        resp = self._client.get(path, params=params, **kwargs)
        return Resp(resp.status_code, _safe_httpx(resp))

    def put(self, url: str, **kwargs) -> Resp:
        resp = self._client.put(url, **kwargs)
        return Resp(resp.status_code, _safe_httpx(resp))

    def close(self) -> None:
        self._client.close()


def _safe(resp) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text()[:500]}


def _safe_httpx(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:500]}


@pytest.fixture(scope="session")
def api_base_url() -> str:
    return API_BASE_URL


@pytest.fixture(scope="session")
def pr2_project_id() -> str:
    return PROJECT_ID


@pytest.fixture(scope="session")
def storage_account_no(request) -> str:
    if os.getenv("PYTEST_XDIST_WORKER"):
        pytest.fail("ACCOUNT_ISOLATION: PR2 shared-account writes require serial execution (-n0)")
    if not os.getenv("PR2_BALANCE_FIELD"):
        pytest.fail("Configure PR2_BALANCE_FIELD before any account mutation")
    return STORAGE_ACCOUNT_NO


@pytest.fixture(scope="session")
def non_member_no() -> str:
    return NON_MEMBER_NO


def _browser_login():
    """UI 登录到营销云，返回 (playwright, browser, context, marketing_url, access_token)。"""
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True, args=["--force-device-scale-factor=1.25"])
    ctx = browser.new_context(viewport={"width": 1680, "height": 950})
    page = ctx.new_page()
    page.goto(_LOGIN, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    page.locator("#login_username").fill(_USER)
    pw = page.locator("#login_password")
    pw.fill(_PWD)
    pw.press("Enter")
    page.wait_for_timeout(5000)

    def _body():
        try:
            return page.locator("body").inner_text()
        except Exception:
            return ""

    def _click(text, timeout=8000):
        for f in [page] + page.frames:
            try:
                if f.get_by_text(text, exact=False).count() > 0:
                    f.get_by_text(text, exact=False).first.click(timeout=timeout)
                    return True
            except Exception:
                continue
        return False

    if "login" in page.url and "登 录" in _body():
        _click("登 录")
        page.wait_for_timeout(6000)
    with page.expect_popup(timeout=20000) as pi:
        _click("数盈·营销云")
    mkt = pi.value
    mkt.wait_for_timeout(9000)
    url = mkt.url
    tok = mkt.evaluate("() => new URL(location.href).searchParams.get('accessToken') || ''")
    return p, browser, ctx, url, tok


def _browser_login_with_token(access_token: str):
    """已知 marketing access-token 时，直接打开营销云页面，跳过控制台登录。"""
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True, args=["--force-device-scale-factor=1.25"])
    ctx = browser.new_context(viewport={"width": 1680, "height": 950})
    page = ctx.new_page()
    url = f"https://imarketing-uat.xysjg.com/front/saas-imarketing-web-pc/?accessToken={access_token}&userName={_USER}#/home"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    return p, browser, ctx, page.url, access_token


@pytest.fixture(scope="session")
def marketing_client() -> Iterator[PlaywrightApiClient]:
    """浏览器会话内 API 客户端（UAT 网关要求浏览器会话 + referer 识别实例）。"""
    if not LIVE:
        pytest.skip("LIVE_API / PR2_LIVE_API != 1")
    if ACCESS_TOKEN and not FORCE_BROWSER_LOGIN:
        client = DirectHttpApiClient(ACCESS_TOKEN, COOKIE_HEADER, REFERER_URL)
        yield client
        client.close()
        return
    p, browser, ctx, url, tok = _browser_login()
    if not tok:
        browser.close()
        p.stop()
        pytest.skip("浏览器登录未取得 access-token")
    yield PlaywrightApiClient(ctx, url, tok)
    browser.close()
    p.stop()
