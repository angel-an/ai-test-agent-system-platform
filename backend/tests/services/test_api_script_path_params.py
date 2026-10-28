"""路径参数消费 endpoint.parameters（api-rev2）。

锁三件事：
1. ``in: path`` 的声明被消费：default → 枚举首值 → 类型样本值；
2. 声明缺失的路径段维持 "1" 回落（老行为不回退）；
3. ``{{var}}`` 双大括号是运行期变量：原样保留进脚本，且纳入
   missingTemplateVariables 缺失检查（缺 TEST_DATA 时 skip 而不是打出字面 URL）。

背景：此前 ``_sample_path`` 无条件 ``re.sub(r"\\{[^{}]+\\}", "1", path)``，
接口声明的路径参数语义全部丢失 —— ``/page/{pageNo}}`` 这类分页段也被打成 1，
与 query 参数同一套样本逻辑却不共用（api-rev2 修正）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.api_case_link_service import APICaseLinkService, script_template_digest
from app.services.api_script_template import API_SCRIPT_TEMPLATE_REV, fingerprint_line


def _svc() -> APICaseLinkService:
    # 渲染链路不碰数据库，绕过构造函数的三个 repo
    return object.__new__(APICaseLinkService)


def _render(path: str, method: str = "GET", parameters: list | None = None) -> str:
    endpoint = SimpleNamespace(
        method=method,
        path=path,
        request_body=None,
        parameters=parameters if parameters is not None else [],
        display_name=f"{method} {path}",
    )
    return _svc()._generate_playwright_script(endpoint)


def _sample(path: str, parameters: list | None = None) -> str:
    return _svc()._sample_path(path, parameters)


# --------------------------------------------------------------------------
# 声明的路径参数被消费
# --------------------------------------------------------------------------

def test_declared_path_param_uses_default():
    params = [{"name": "id", "in": "path", "required": True, "default": "42"}]
    assert _sample("/a/{id}/b", params) == "/a/42/b"


def test_declared_path_param_enum_first_value():
    params = [{"name": "level", "in": "path", "required": True, "enum": ["vip", "svip"]}]
    assert _sample("/member/{level}", params) == "/member/vip"


def test_declared_path_param_type_integer_samples_1():
    params = [{"name": "pageNo", "in": "path", "required": True, "type": "integer"}]
    assert _sample("/page/{pageNo}", params) == "/page/1"


def test_declared_path_param_type_boolean():
    params = [{"name": "flag", "in": "path", "required": True, "schema": {"type": "boolean"}}]
    assert _sample("/switch/{flag}", params) == "/switch/false"


def test_declared_path_param_schema_type_number():
    params = [{"name": "n", "in": "path", "required": True, "schema": {"type": "number"}}]
    assert _sample("/x/{n}", params) == "/x/1"


def test_declared_path_param_renders_into_script():
    script = _render("/api/v1/items/{id}", parameters=[{"name": "id", "in": "path", "default": "77"}])
    assert 'buildUrl("/api/v1/items/77")' in script


# --------------------------------------------------------------------------
# 声明缺失回落老行为
# --------------------------------------------------------------------------

def test_undeclared_path_param_falls_back_to_1():
    assert _sample("/a/{id}/b", []) == "/a/1/b"


def test_partially_declared_path_params():
    """声明了 id 没声明 code：各走各的。"""
    params = [{"name": "id", "in": "path", "default": "9"}]
    assert _sample("/a/{id}/c/{code}", params) == "/a/9/c/1"


def test_malformed_parameters_list_is_ignored():
    assert _sample("/a/{id}/b", "not-a-list") == "/a/1/b"


# --------------------------------------------------------------------------
# {{var}} 运行期变量
# --------------------------------------------------------------------------

def test_double_brace_runtime_var_preserved_verbatim():
    assert _sample("/u/{{userId}}/orders") == "/u/{{userId}}/orders"


def test_double_brace_not_sampled_even_if_declared():
    """{{var}} 与单大括号同名的声明参数互不干扰。"""
    params = [{"name": "userId", "in": "path", "default": "5"}]
    assert _sample("/u/{userId}/o/{{userId}}", params) == "/u/5/o/{{userId}}"


def test_runtime_var_renders_into_script_and_missing_check():
    script = _render("/u/{{userId}}/orders")
    assert 'buildUrl("/u/{{userId}}/orders")' in script
    # 缺失检查覆盖路径段：缺 TEST_DATA 时 skip，而不是把字面 {{userId}} 打进 URL
    assert ".concat(missingTemplateVariables(" in script
    assert '"/u/{{userId}}/orders"' in script


def test_mixed_declared_single_and_runtime_double_brace():
    script = _render(
        "/t/{tid}/u/{{uid}}",
        parameters=[{"name": "tid", "in": "path", "default": "3"}],
    )
    assert 'buildUrl("/t/3/u/{{uid}}")' in script


# --------------------------------------------------------------------------
# 版本指纹
# --------------------------------------------------------------------------

def test_fingerprint_is_rev2():
    script = _svc().render_script(
        SimpleNamespace(
            method="GET", path="/a/{id}", request_body=None,
            parameters=[{"name": "id", "in": "path", "default": "8"}],
            display_name="GET /a/{id}",
        )
    )
    first_line = script.splitlines()[0]
    assert first_line == fingerprint_line(
        rev="api-rev2", digest=script_template_digest()
    )
    assert API_SCRIPT_TEMPLATE_REV == "api-rev2"
