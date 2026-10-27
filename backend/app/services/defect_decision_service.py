"""
缺陷决策服务

MVP 采用规则判断，不引入复杂模型。

创建缺陷的条件：
- API 断言失败
- 实际状态码与预期不一致
- 响应结构与接口契约不一致
- 业务结果明显错误
- 同一问题具备可复现证据

跳过创建的情况：
- 网络不可达
- 测试环境服务未启动
- 测试脚本语法错误
- 认证配置错误
- 测试数据准备失败
- 非稳定性偶发错误
- 已明确标记为非缺陷
"""

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 断言期望值反推
# ---------------------------------------------------------------------------
# Playwright 的 expect(...) 失败消息固定带一段「Expected / Received」：
#
#     Error: expect(received).toBeGreaterThanOrEqual(expected)
#
#     Expected: >= 400
#     Received:    200
#
# 这段就是自动提单真正该写进「预期结果」的内容——它来自**用例的真实断言**，
# 而不是模板套话。旧实现恒写「HTTP 状态码: 200 OK / 响应结构符合接口契约 / 业务逻辑正确」，
# 对「业务失败也返回 HTTP 200」的信封式被测系统（如小杨生煎网关）来说，
# 等于把缺陷写成"现状即预期"，开发看工单会认为无需修复。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_EXPECTED_RE = re.compile(r"^[ \t]*Expected:[ \t]*(?P<v>.+?)[ \t]*$", re.MULTILINE)
_RECEIVED_RE = re.compile(r"^[ \t]*Received:[ \t]*(?P<v>.+?)[ \t]*$", re.MULTILINE)
# 断言表达式：`expect(...).toBe(x)` / `expect(...).not.toBeVisible()` / `... // Object.is equality`
_MATCHER_RE = re.compile(r"\)\.(?:not\.)?(?P<matcher>to[A-Za-z]+)\(")

_PLACEHOLDER_VALUES = {"", "undefined", "null", "<object>"}


def strip_ansi(text: str) -> str:
    """剥离 Playwright 输出里的 ANSI 颜色转义（否则会把 ``\\x1b[31m`` 一起写进工单）。"""
    return _ANSI_RE.sub("", text or "")


def extract_assertion_expectation(
    error_message: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """从断言失败消息反推 ``(期望值, 实际值, 断言表达式)``。

    任一取不到则为 ``None``。取不到时调用方**必须**如实写"待人工补充"，
    不得回落到模板套话——那正是本次修复要消除的行为。

    Examples:
        >>> extract_assertion_expectation("Expected: >= 400\\nReceived:    200")
        ('>= 400', '200', None)
    """
    text = strip_ansi(error_message or "")
    if not text:
        return None, None, None

    m_exp = _EXPECTED_RE.search(text)
    m_rec = _RECEIVED_RE.search(text)
    expected = (m_exp.group("v").strip() if m_exp else "") or None
    observed = (m_rec.group("v").strip() if m_rec else "") or None
    if expected is not None and expected.lower() in _PLACEHOLDER_VALUES:
        expected = None
    if observed is not None and observed.lower() in _PLACEHOLDER_VALUES:
        observed = None

    expr = None
    m_match = _MATCHER_RE.search(text)
    if m_match:
        negated = ".not" if ".not." in m_match.group(0) else ""
        expr = f"expect(...){negated}.{m_match.group('matcher')}()"

    return expected, observed, expr


def extract_failure_headline(
    error_message: Optional[str],
    max_len: int = 200,
) -> Optional[str]:
    """从断言失败消息里取**一行**人类可读的失败摘要。

    Playwright 的 ``error.message`` 形如「``Error: <自定义消息>`` + 空行 +
    ``Expected:/Received:`` 块」，直接塞进工单「问题概述」会让读者面对一屏噪声
    （实测自动提单里出现过整段原始报错）。

    取值优先级：
    1. 自定义断言消息（``Error:`` 后面那一行，且不是 Playwright 自动生成的
       ``expect(...)....`` 描述行）；
    2. 由期望/实测值概括的「断言失败：期望 X，实际 Y」；
    3. 原文首行。
    """
    text = strip_ansi(error_message or "").strip()
    if not text:
        return None

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first = re.sub(r"^Error:\s*", "", lines[0]).strip() if lines else ""
    # Playwright 自动生成的行（如 `expect(locator).toBeVisible() failed`）不含业务信息
    if first and not first.lower().startswith("expect("):
        return first[:max_len]

    expected, observed, _ = extract_assertion_expectation(text)
    if expected is not None or observed is not None:
        return (
            f"断言失败：期望 {expected or '（未提取到）'}，实际 {observed or '（未提取到）'}"
        )[:max_len]
    if first:
        return first[:max_len]
    return text[:max_len]


class DefectPriority(str, Enum):
    """缺陷优先级"""
    LOW = "low"       # 低优先级
    MEDIUM = "medium"  # 中优先级
    HIGH = "high"     # 高优先级


class DefectDecision(str, Enum):
    """缺陷决策结果"""
    CREATE = "create"    # 创建缺陷
    SKIP = "skip"        # 跳过
    PENDING = "pending"  # 待判断


@dataclass
class DefectDecisionResult:
    """缺陷决策结果"""
    decision: DefectDecision
    priority: DefectPriority
    priority_code: str
    priority_id: int
    reason: str
    error_type: str
    failure_summary: str
    #: 缺陷类型（IDP 导入模板要求的问题分类；空串表示未归类）
    defect_type: str = ""
    #: 断言的期望值（从 Playwright 失败消息反推；None = 未能提取，须人工补充）
    expected_result: Optional[str] = None
    #: 断言的实际值（同上）
    observed_result: Optional[str] = None
    #: 断言表达式摘要，如 ``expect(...).toBeGreaterThanOrEqual()``
    assertion_expr: Optional[str] = None


class DefectDecisionService:
    """
    缺陷决策服务

    基于规则判断是否需要创建缺陷
    """

    # 跳过创建的关键词/模式
    SKIP_PATTERNS = [
        r"网络不可达",
        r"连接超时",
        r"Connection refused",
        r"Connection timeout",
        r"Network is unreachable",
        r"测试环境.*未启动",
        r"服务.*未启动",
        r"语法错误",
        r"SyntaxError",
        r"认证配置错误",
        r"认证失败",
        r"Unauthorized",
        r"测试数据.*失败",
        r"数据准备.*失败",
        r"非缺陷",
        r"not_a_defect",
        r"脚本错误",
        r"测试脚本",
    ]

    #: **非 SUT 侧失败**规则表：(正则, 分类说明)。
    #: 这些文本描述的是"脚本/执行器/平台前置条件"的状态，**没有 SUT 侧证据**
    #: （无请求报文、无业务期望、无数据结果），却会被 `_analyze_error` 兜底
    #: `extract_failure_headline()` 当成"缺陷现象"，产出不合规标题。故一律不自动开单。
    #:
    #: 依据（2026-10-07 平台自有数据审计，`defect_title_audit.txt` / `skip_change_impact_audit.txt`）：
    #: 已开出的 85 条单里，非 SUT 技术串占相当比例 ——
    #: `TimeoutError: locator.fill: Timeout 30000ms exceeded.` ×3、
    #: `Error: 未找到或无法点击：点击运营菜单` ×9、`[script_not_registered] …` 多条、
    #: `企业控制台登录失败（验证码 OCR 重试 10 次后仍未离开登录页）` ×11、
    #: `net::ERR_CONNECTION_REFUSED` ×2、`net::ERR_UNSAFE_PORT` ×1，
    #: 甚至有一条标题就是 `Error Context: test-results-playwright-report-…` 的现场路径。
    #: 典型个例：TR-172 / TC-321795（单号 jf-20260122392-01-2963）。
    #:
    #: **生效条件**：仅在无 SUT 侧证据（未观测到响应状态码、无断言结果）时跳过；
    #: 一旦有状态码或断言结果，说明是 SUT 的真实行为，仍走开单判定（见 `_check_skip_conditions`）。
    NON_SUT_FAILURE_RULES: list[tuple[str, str]] = [
        # 平台入库的 error_message **只有首行**（实测 TR-172 存的是
        # `TimeoutError: locator.waitFor: Timeout 15000ms exceeded.`，不含 Call log 里的
        # `waiting for locator(...)`），故直接匹配"定位器调用 + 等待超时"组合。
        (r"locator\.\w+[^\n]*Timeout \d+ms exceeded", "自动化交互层失败（定位器未出现/不可见）"),
        (r"waiting for (locator|selector|element|getBy)", "自动化交互层失败（等待元素超时）"),
        (r"strict mode violation", "自动化交互层失败（定位器命中多个元素）"),
        (r"Target (page|context|browser)[^\n]{0,40}has been closed",
         "环境/执行器类失败（浏览器或会话被关闭）"),
        (r"browser has been closed", "环境/执行器类失败（浏览器已关闭）"),
        (r"Execution context was destroyed", "环境/执行器类失败（执行上下文被销毁）"),
        # Chromium 进程**原生崩溃**（非 JS 异常）：报文首行即 `Stacktrace:`，后跟
        # `ptr1=…` / `failure_message_object=…` 等原生栈帧。实测（2026-10-08，TR-192 /
        # 用例 TC-261933 的 WT-A9C1C11F）该形态经全表未命中 → `decide()` 判 create，
        # 产出标题就是 `Stacktrace:` 的垃圾单（`idp_defect_records.b6f950c5`）。
        # 与"预期结果写成 HTTP 200"同族 —— 平台把**执行器自身故障**当成了被测系统现象。
        # 锚定**报文首行**：本函数用 `re.search` 且未开 `re.MULTILINE`，`^` 即串首
        # （允许前导空白），只认"首行即原生栈帧"这一形态；正文里提及 Stacktrace 字样
        # 的合法断言消息不受影响（见 test_defect_type.py 的边界用例）。
        (r"^\s*Stacktrace:", "环境/执行器类失败（浏览器进程原生崩溃，非 SUT 现象）"),
        (r"Test timeout of \d+ms exceeded", "环境/执行器类失败（测试整体超时）"),
        (r"测试执行超时", "环境/执行器类失败（作业级超时）"),
        (r"net::ERR_", "环境类失败（浏览器网络层错误）"),
        (r"No tests found", "脚本采集失败（未找到可执行用例）"),
        (r"未找到或无法点击", "自动化导航助手失败（脚本未找到入口元素）"),
        (r"验证码 OCR|OCR 重试", "自动化登录助手失败（验证码识别未通过）"),
        (r"脚本来源授权拒绝|未绑定子功能|占位/未配置地址|"
         r"script_not_registered|sub_function_unbound|placeholder_base_url",
         "平台前置条件未满足（脚本未登记/未绑定子功能/base_url 未配置）"),
        (r"Web 测试已跳过|缺少运行时登录凭据",
         "平台前置条件未满足（缺少运行时凭据/前置条件不成立）"),
        (r"执行被中断", "平台侧执行中断（服务器重启或进程终止）"),
        (r"编译断言执行受阻",
         "平台侧断言执行受阻（隔离守卫/环境限制），未形成 SUT 观测"),
        (r"无法打开表单|定位器可能失效|定位器未配置|未定位到",
         "自动化定位器/模板配置问题（无法打开表单或未定位到控件），非 SUT 现象"),
        # 渲染/空白页类失败（2026-10-10 新增，实测 PR-9 CS 批次 9 次命中）：
        # 现场快照显示**路由与菜单都正常**（`link [active] = #/cs/case/list`、Tab 已打开），
        # 仅 iframe 内容区为空；独立探针 150s 采样 `bodyLen` 恒 0（无 JS 异常、无请求失败）。
        # 该类失败**既无状态码也无断言结果**，无法归因到被测系统行为 ⇒ 按既定口径不开单，
        # 保留原文供人工复核。反向保护：一旦带业务断言结果或 >=400 状态码，
        # `has_sut_evidence` 成立，本规则整体不生效。
        (r"未渲染|未检测到表格|未检测到[^，。]{0,8}分页|内容区(为)?空|页面空白|空白页",
         "环境/渲染类失败（页面内容区未渲染，疑似前端资源或时序问题）"),
        (r"执行已被取消", "平台侧执行被取消（非 SUT 行为）"),
        # 登录助手失败：文案由我方 `loginToConsole` 生成，**无 SUT 侧证据**（未观测到登录接口状态码/报文）。
        # 实测（2026-10-07，单号 jf-20260122392-01-2964）：TR-181 报「登录未完成（3 次尝试后仍停留在登录页…）」，
        # 紧接着 TR-182 / TR-183 用**同一账号**连续通过 ⇒ 属偶发；且作业分类 `classify_job_failure`
        # 早就把它判为 `precondition / 执行前置失败`（即非缺陷），此处与之一致。
        # 反向保护：若失败同时带响应状态码（如登录接口 5xx），has_sut_evidence 成立 → 仍会开单。
        (r"登录未完成|仍停留在登录页|未离开登录页",
         "自动化登录助手失败（登录未完成，疑似凭据/环境类偶发）"),
        (r"Error Context:|test-results-playwright-report|error-context\.md|\.png\b",
         "采集到的只是失败现场文件（截图/error-context 路径），而非 SUT 现象"),
    ]

    NON_SUT_FAILURE_REASON_TEMPLATE = (
        "跳过创建：{label}（{headline}）——本次无 SUT 侧证据"
        "（未观测到响应状态码、无断言结果），不自动开单；"
        "原始错误在运行日志与 stdout 中保留，请人工复核后决定是否提缺陷"
    )

    #: 缺陷类型归类（关键词 → 类型；命中不了归「功能缺陷」）
    DEFECT_TYPE_RULES: list[tuple[str, str]] = [
        # 注：`敏感信息泄露` 与 `泄露敏感信息` 语序相反，只写一种会漏判 ——
        # 实测「响应中泄露敏感信息」原样匹配不到，故两种语序都收。
        (r"越权|未授权访问|注入|XSS|CSRF|"
         r"敏感信息(泄露|暴露|外泄)|泄露[^，。]{0,6}敏感信息", "安全缺陷"),
        (r"未校验|缺少校验|未执行校验|应拒绝|接受了[^，。]*(非法|异常|冲突|重复)|"
         r"校验不一致|校验缺失|缺少.*校验", "数据校验缺失"),
        (r"空指针|NullPointer|NPE|内部错误|健壮性|data:null|data 为 null|"
         r"分页.*(异常|越界|为空)|响应结构不一致", "接口健壮性"),
        (r"超时|响应时间|性能|吞吐", "性能问题"),
        (r"兼容", "兼容性问题"),
    ]
    DEFAULT_DEFECT_TYPE = "功能缺陷"

    @classmethod
    def _classify_defect_type(cls, error_type: str, error_message: Optional[str]) -> str:
        """按错误特征归类缺陷类型（IDP 模板分类口径，零业务词）。"""
        if error_type in ("none", "skip"):
            return ""
        text = error_message or ""
        for pattern, dtype in cls.DEFECT_TYPE_RULES:
            if re.search(pattern, text):
                return dtype
        return cls.DEFAULT_DEFECT_TYPE

    # 高优先级关键词
    HIGH_PRIORITY_PATTERNS = [
        r"数据.*丢失",
        r"数据.*损坏",
        r"安全.*漏洞",
        r"越权",
        r"未授权访问",
        r"SQL 注入",
        r"XSS",
        r"服务.*崩溃",
        r"内存.*溢出",
        r"死锁",
    ]

    @classmethod
    def decide(
        cls,
        test_status: str,
        error_message: Optional[str],
        response_status_code: Optional[int],
        expected_status_code: Optional[int],
        assertion_results: Optional[list[dict]] = None,
        retry_count: int = 0,
    ) -> DefectDecisionResult:
        """
        判断是否需要创建缺陷

        Args:
            test_status: 测试状态 (passed/failed/skipped/blocked)
            error_message: 错误信息
            response_status_code: 实际响应状态码
            expected_status_code: 预期状态码
            assertion_results: 断言结果列表
            retry_count: 重试次数

        Returns:
            DefectDecisionResult: 决策结果
        """
        # 0. 状态码归一化 —— 调用方可能把 Playwright 的**用例状态**字符串
        #    （"failed"/"timedOut"）当成 HTTP 状态码传进来，两者不是一回事。
        #    不归一化会让下游 `response_status_code >= 500` 抛
        #    TypeError: '>=' not supported between 'str' and 'int'（2026-10-27 实测）。
        #    非整型一律按「未观测到状态码」处理；数字字符串允许转成 int。
        response_status_code = cls._as_status_code(response_status_code)
        expected_status_code = cls._as_status_code(expected_status_code)

        # 1. 测试通过，不创建缺陷
        if test_status == "passed":
            return DefectDecisionResult(
                decision=DefectDecision.SKIP,
                priority=DefectPriority.LOW,
                priority_code="priority-2",
                priority_id=2,
                reason="测试通过",
                error_type="none",
                failure_summary="测试通过，无需创建缺陷",
            )

        # 2. 检查是否属于跳过的情况
        #    SUT 侧证据（**异常**状态码 / 断言结果）存在时，"自动化/环境类"跳过规则不生效。
        #    注意：**HTTP 2xx/3xx 不算证据**（详见 `_has_sut_evidence`），
        #    否则传进来一个 200 就会把「登录未完成」「定位器超时」重新开成缺陷单。
        skip_reason = cls._check_skip_conditions(
            error_message,
            retry_count,
            has_sut_evidence=cls._has_sut_evidence(
                response_status_code, assertion_results
            ),
        )
        if skip_reason:
            return DefectDecisionResult(
                decision=DefectDecision.SKIP,
                priority=DefectPriority.LOW,
                priority_code="priority-2",
                priority_id=2,
                reason=skip_reason,
                error_type="skip",
                failure_summary=skip_reason,
            )

        # 3. 分析错误类型和优先级
        error_type, failure_summary = cls._analyze_error(
            error_message,
            response_status_code,
            expected_status_code,
            assertion_results,
        )

        priority, priority_code, priority_id = cls._determine_priority(
            error_type,
            error_message,
            response_status_code,
        )

        # 从断言失败消息反推期望/实际值，供提单描述写"真正的预期结果"。
        # 注意：**不**用 `expected_status_code` 兜底——调用方普遍以 `or 200` 传参，
        # 那个 200 是猜的不是断言给的，写进工单等于伪造预期。
        expected_result, observed_result, assertion_expr = extract_assertion_expectation(
            error_message
        )

        return DefectDecisionResult(
            decision=DefectDecision.CREATE,
            priority=priority,
            priority_code=priority_code,
            priority_id=priority_id,
            reason=f"检测到缺陷: {failure_summary}",
            error_type=error_type,
            failure_summary=failure_summary,
            defect_type=cls._classify_defect_type(error_type, error_message),
            expected_result=expected_result,
            observed_result=observed_result,
            assertion_expr=assertion_expr,
        )

    @classmethod
    def _check_skip_conditions(
        cls,
        error_message: Optional[str],
        retry_count: int,
        has_sut_evidence: bool = False,
    ) -> Optional[str]:
        """
        检查是否应该跳过创建缺陷

        Args:
            error_message: 错误信息
            retry_count: 已重试次数
            has_sut_evidence: 是否已观测到 SUT 侧证据（**异常**状态码 / 业务断言结果），
                由 :meth:`_has_sut_evidence` 判定。**自动化与执行器类**跳过规则只在
                无此证据时生效——已观测到状态码不匹配或断言结果时，那是 SUT 的真实行为，
                仍应走开单判定（不得被"浏览器被关闭"这类字样一并吞掉）。
                ⚠️ **HTTP 2xx/3xx 不算证据**：页面能打开 ≠ 被测系统行为异常。

        Returns:
            Optional[str]: 跳过原因，不需要跳过时返回 None
        """
        if not error_message:
            return None

        error_lower = error_message.lower()

        # 非 SUT 侧失败（脚本/执行器/平台前置条件）：仅在**没有 SUT 侧证据**时跳过
        if not has_sut_evidence:
            for pattern, label in cls.NON_SUT_FAILURE_RULES:
                if re.search(pattern, error_message, re.IGNORECASE) or re.search(
                    pattern, error_lower, re.IGNORECASE
                ):
                    headline = (
                        extract_failure_headline(error_message)
                        or error_message.splitlines()[0]
                    )
                    return cls.NON_SUT_FAILURE_REASON_TEMPLATE.format(
                        label=label, headline=headline[:120]
                    )

        # 检查跳过模式（环境/凭据/脚本类，行为保持不变）
        for pattern in cls.SKIP_PATTERNS:
            if re.search(pattern, error_message, re.IGNORECASE) or re.search(
                pattern, error_lower, re.IGNORECASE
            ):
                return f"跳过创建：匹配到跳过规则 '{pattern}'"

        # 重试次数过多（可能是偶发错误）
        if retry_count >= 3:
            return f"跳过创建：已重试 {retry_count} 次，判定为非稳定性偶发错误"

        return None

    #: 构成「SUT 侧证据」的状态码下限。
    #:
    #: **2xx/3xx 不是证据**：浏览器或页面文档返回 200，只说明"页面能打开"，
    #: 不能证明"登录没走完 / 元素没出现 / 定位器超时"是被测系统的行为。
    #: 一旦把 200 当作证据，上面的 `NON_SUT_FAILURE_RULES` 就会**整体失效**。
    #:
    #: 实测（2026-10-08，用本服务自身判定，见 `defect_gate/gate_probe.py`）：
    #:
    #: ========================================  ==========  ==================
    #: error_message                             status      旧口径结果
    #: ========================================  ==========  ==================
    #: 登录未完成（3 次尝试后仍停留在登录页…）      None        SKIP ✅
    #: 登录未完成（3 次尝试后仍停留在登录页…）      200         CREATE ❌
    #: TimeoutError: locator.waitFor: …            200         CREATE ❌
    #: ========================================  ==========  ==================
    #:
    #: 也就是说：只要链路上有一个 HTTP 200 被传进来，「登录未完成」「定位器超时」
    #: 就会被重新开单 —— 与 jf-20260122392-01-2963 / 2964 的产生路径一致。
    #: 收紧为 >= 400 后，只有**异常**状态码（4xx/5xx，含网关 502/503）才算证据，
    #: 反向保护（登录接口返回 502 仍开单）不受影响。
    _SUT_EVIDENCE_MIN_STATUS = 400

    @staticmethod
    def _as_status_code(value: object) -> Optional[int]:
        """把状态码收敛成 ``int`` 或 ``None``。

        接受 int 与数字字符串；``None`` 原样返回；其余（``"failed"``、``"timedOut"``、
        bool 等）一律视为「未观测到状态码」。语义上只做类型收敛，不改变任何合法输入。
        """
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    @classmethod
    def _has_sut_evidence(
        cls,
        response_status_code: Optional[int],
        assertion_results: Optional[list[dict]],
    ) -> bool:
        """是否已观测到 **SUT 侧证据**（决定"自动化/环境类"跳过规则是否让位）。

        - 有**业务断言结果** → 是证据（断言本身就是对被测系统的期望）；
        - 状态码 **>= 400** → 是证据（服务端/网关真实报错，如登录接口 502）；
        - 状态码 **2xx/3xx** → **不是证据**（只能说明页面/接口通，见类常量注释）；
        - 完全没有状态码 → 不是证据（UI 失败通常观测不到 HTTP 响应）。
        """
        if assertion_results:
            return True
        return (
            response_status_code is not None
            and response_status_code >= cls._SUT_EVIDENCE_MIN_STATUS
        )

    @classmethod
    def _analyze_error(
        cls,
        error_message: Optional[str],
        response_status_code: Optional[int],
        expected_status_code: Optional[int],
        assertion_results: Optional[list[dict]],
    ) -> tuple[str, str]:
        """
        分析错误类型

        Returns:
            tuple[str, str]: (error_type, failure_summary)
        """
        # 状态码不匹配
        if (
            response_status_code is not None
            and expected_status_code is not None
            and response_status_code != expected_status_code
        ):
            return (
                "status_mismatch",
                f"状态码不匹配：预期 {expected_status_code}，实际 {response_status_code}",
            )

        # 断言失败
        if assertion_results:
            failed_assertions = [
                a for a in assertion_results if not a.get("passed", True)
            ]
            if failed_assertions:
                first = failed_assertions[0]
                return (
                    "assertion_failed",
                    f"断言失败: {first.get('message', '未知断言')}",
                )

        # 5xx 错误
        if response_status_code and response_status_code >= 500:
            return (
                "server_error",
                f"服务器错误: HTTP {response_status_code}",
            )

        # 默认错误类型
        error_type = "test_failed"
        # 不要把整块 Playwright 报错（含 Expected/Received 与堆栈）当摘要塞进工单
        failure_summary = (
            extract_failure_headline(error_message) or "测试失败（未知原因）"
        )
        if len(failure_summary) > 200:
            failure_summary = failure_summary[:200] + "..."

        return error_type, failure_summary

    @classmethod
    def _determine_priority(
        cls,
        error_type: str,
        error_message: Optional[str],
        response_status_code: Optional[int],
    ) -> tuple[DefectPriority, str, int]:
        """
        确定缺陷优先级

        pageNum=9999 规则：
        - 返回合法空数据或规范错误码：低优先级
        - 稳定返回 500：中优先级
        - 影响服务稳定性或数据正确性：高优先级

        Returns:
            tuple[DefectPriority, str, int]: (priority, priority_code, priority_id)
        """
        error_text = (error_message or "").lower()

        # 检查高优先级模式
        for pattern in cls.HIGH_PRIORITY_PATTERNS:
            if re.search(pattern, error_text, re.IGNORECASE):
                return (DefectPriority.HIGH, "priority-1", 1)

        # 「数据正确性」类：按本方法文档化口径「影响数据正确性 → 高优先级」，
        # 但 HIGH_PRIORITY_PATTERNS 只覆盖了"数据丢失/损坏"，**没有**覆盖
        # "校验缺失导致受理非法数据"。这类缺陷恰恰不带服务器错误特征
        # （业务拦截缺失 → HTTP 仍是 200），既不匹配上面的模式、也非 5xx/4xx，
        # 会一路兜底成「低」—— 与"已产生脏数据"的实际风险严重倒挂。
        #
        # 实测：DEFECT-001 / jf-20260122392-01-2960（modify 入口未做商品唯一性校验、
        # 受理了已被占用的商品 11281）被平台判为「低」，而人工定性是 P1。
        #
        # 复用既有的缺陷类型分类口径（单一真相），不另起一套关键词。
        _dtype = cls._classify_defect_type(error_type, error_message)
        if _dtype == "数据校验缺失":
            return (DefectPriority.HIGH, "priority-1", 1)
        # 安全类：HIGH_PRIORITY_PATTERNS 覆盖了越权/注入/XSS，但漏了
        # CSRF、敏感信息泄露（会被归到「安全缺陷」却一路兜底成「低」）。一并补齐。
        if _dtype == "安全缺陷":
            return (DefectPriority.HIGH, "priority-1", 1)

        # 5xx 服务器错误 → 中优先级
        if response_status_code and response_status_code >= 500:
            return (DefectPriority.MEDIUM, "priority-2", 2)

        # 4xx 客户端错误 → 中优先级（可能是接口问题）
        if response_status_code and response_status_code >= 400:
            return (DefectPriority.MEDIUM, "priority-2", 2)

        # 断言失败 → 中优先级
        if error_type == "assertion_failed":
            return (DefectPriority.MEDIUM, "priority-2", 2)

        # 其他情况 → 低优先级
        return (DefectPriority.LOW, "priority-3", 3)

    @classmethod
    def decide_from_api_result(
        cls,
        status: str,
        error_message: Optional[str],
        request_summary: Optional[dict],
        response_summary: Optional[dict],
    ) -> DefectDecisionResult:
        """
        从 API 测试结果中判断是否需要创建缺陷

        便捷方法，从 request_summary 和 response_summary 中提取信息
        """
        response_status_code = None
        expected_status_code = None

        if response_summary:
            response_status_code = response_summary.get("status_code")

        if request_summary:
            expected_status_code = request_summary.get("expected_status_code")

        return cls.decide(
            test_status=status,
            error_message=error_message,
            response_status_code=response_status_code,
            expected_status_code=expected_status_code,
        )
