"""需求功能用例与 Web 可执行资产的最小闭环服务。"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.minio_client import MinIOClient
from app.config.settings import settings
from app.models.attachment import Attachment, AttachmentEntityType
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.test_run import TestRun, TestRunScriptJob, TestRunTestCase
from app.models.web_function import WebFunction, WebSubFunction
from app.models.web_form_template import WebFormTemplate
from app.models.web_path_template import ProjectWebPathTemplate
from app.models.web_test import WebTest
from app.repositories.test_case_repo import TestCaseRepository
from app.repositories.test_run_repo import TestRunRepository
from app.services.web_case_compiler import (
    COMPILER_REV,
    build_form_plan,
    compiler_source_digest,
    build_form_plan_for_template,
    compile_cases,
    merge_registered_locators,
    render_coverage_snippet,
    render_form_executor,
    render_manifest_block,
    resolve_form_locators,
)
from app.services.web_test_compile_manifest import apply_compile_manifest
from app.schemas.enums import (
    AutomationStatus,
    ExecutionMode,
    JobStatus,
    ScriptType,
    TestResultStatus,
    TestRunState,
)


_TOKEN_SPLIT_RE = re.compile(r"[\s/、,，;；:：()（）\[\]【】\-]+")
logger = logging.getLogger(__name__)
#: 与 _merge_web_path_description 写入格式对应：剥离回写的验证路径段落
_WEB_VALIDATION_PATH_RE = re.compile(
    r"(?:\r?\n){0,2}Web验证路径：.*?(?=(?:\r?\n){2,}|\Z)", re.S
)


def _sub_function_tokens(*values: str | None) -> set[str]:
    """把子功能/功能名称切成可匹配词元（含中文双字组，容忍词序差异）。"""
    tokens: set[str] = set()
    for value in values:
        if not value:
            continue
        for part in _TOKEN_SPLIT_RE.split(str(value)):
            part = part.strip()
            if len(part) >= 2:
                tokens.add(part)
            # 中文长词补充双字组："B端储值充值" 与 "储值企业充值" 共享 储值/充值
            if len(part) >= 4:
                for i in range(len(part) - 1):
                    tokens.add(part[i:i + 2])
    return tokens


def _score_sub_function_match(case_text: str, *name_values: str | None) -> int:
    """用例文本与候选子功能名称的重合打分（词元长度即权重，避免短词虚高）。"""
    return sum(len(t) for t in _sub_function_tokens(*name_values) if t in case_text)


#: Web 脚本模板键 → 资产命名标签。每个键对应一个**按具体业务流编写**的模板，
#: 没有对应键时不得退回其它业务模板（详见 `_resolve_web_template_key`）。
#: `yl_masterdata`：2026-10-15 页面提测部署后，包装器 `_generate_yl_masterdata_script`
#: 已按探针校准结果实现（同日提交）；字段级定位仍由子功能 custom_config.field_locators
#: 的 selector_hint 驱动（页面为英文标签）。
_WEB_TEMPLATE_LABELS: dict[str, str] = {
    "cs_module": "CS工单管理",
    "enterprise_storage_recharge": "B端企业储值充值",
    "storage_writeoff": "B端储值免单活动",
    "purchase_limit": "B端会员限购商品管理",
    "yl_masterdata": "YL主数据管理",
    "survey_operation": "调查问卷管理",
}

#: 模板标签 → 模板键（反查）。2026-10-20 F-1：子功能 `display_name` 与标签
#: **归一化后完全相等**时，视为「显式绑定」——这是"配置了却不生效"的最小修复：
#: 平台已有 100+ 子功能（`WSF-SURVEY-01` 的 display_name 就是「调查问卷管理」），
#: 让它们无需改库即可路由，而不是逼用户改用例名去凑关键词。
_TEMPLATE_LABEL_TO_KEY: dict[str, str] = {v: k for k, v in _WEB_TEMPLATE_LABELS.items()}

#: 「调查问卷管理」（营销云-系统）业务的**正向**识别词。
#: 取问卷业务专有复合词（问卷标题/问卷题目/问卷编码/调查问卷/参与次数），
#: 不取裸「问卷」，避免误伤未来含「问卷」二字的其他业务文案。
_SURVEY_KEYWORDS = (
    "调查问卷", "问卷标题", "问卷题目", "问卷编码", "参与次数",
)

#: 「储值免单活动」业务的**正向**识别词（仅用于确认该业务，不再作为通用兜底）。
_STORAGE_WRITEOFF_KEYWORDS = ("储值免单", "免单活动")

#: 「会员限购商品管理」（交易云-运营）业务的**正向**识别词。
#: 注意取复合词（"限购商品/限购标签"），不取裸 "限购"，避免误伤未来含"限购"二字的其他业务。
_PURCHASE_LIMIT_KEYWORDS = (
    "限购商品", "限购标签", "会员限购", "限购周期", "限购门槛",
    "加购限量", "消费金额门槛", "加购商品",
)

#: 业务识别前的**分隔符归一化**（2026-10-10）。
#:
#: 背景：`test_case.module` 由上传需求文档的**文件名 stem** 派生
#: （`api/v2/ai.py:_derive_requirement_module`），而模板路由（`_resolve_web_template_key`）
#: 又拿 `module` 文本去匹配**含空格**的硬编码关键词（如 `"CS Module"`）。
#: 文件名里的 `_` / `-` / `.` 会把关键词切断 —— 实测
#: `CS_Module_方案説明2.0.pdf` 生成的 39 条用例在 `minimal-loop` 阶段 **32/33 报
#: `[unsupported_web_template]`**（平台自己生成的用例自己编译不了）；
#: 只把**上传文件名**改成含空格的 `CS Module 方案説明2.0.pdf`（PDF 内容不动）
#: 即 33/33 通过。这里在**匹配前**统一把分隔符折叠为空格，
#: 匹配语义（正向识别、绝不兜底）保持不变。
#: 文件名里的 `.` **不**算分隔符：stem 已去掉扩展名，剩下的 `.` 通常是版本号
#: （`方案説明2.0`），拆开只会污染诊断输出而不增加命中率。
_ROUTE_SEPARATOR_RE = re.compile(r"[_\-/\\|]+")


def normalize_route_text(text: str | None) -> str:
    """把分隔符折叠为空格并压缩空白，供业务关键词匹配使用。

    仅做「无语义分隔符 → 空格」的等价变换，不改动任何汉字/字母数字，
    因此不会引入新的误命中面（关键词表本身也一并归一化后比较）。
    """
    if not text:
        return ""
    return " ".join(_ROUTE_SEPARATOR_RE.sub(" ", text).split())


# ---------------------------------------------------------------------------
# Web 脚本公共骨架（P0-2 阶段 A）
# ---------------------------------------------------------------------------
# 背景（2026-09-30 实测）：4 个业务脚本生成器各自内联了一份 clickByText /
# loginToConsole / requiredClick，逐字对比后发现它们**同源但已分叉**，
# 且差异有明确语义（不是随手写的）：
#   * 仅「会员限购商品管理」（交易云）的 clickByText 带 3 个 Element 菜单定位器，
#     通用 first() 会命中面包屑导致点不中「运营」（实测踩过）；
#   * 「储值免单」的 clickByText **没有** DOM 直点兜底（全屏 loading 遮罩场景会失败）；
#   * requiredClick 的 timeoutMs 参数仅限购版本有。
#
# 因此本骨架**不做"简单替换式抽取"**，而是把差异做成**参数**：
#   - menu_selectors: "el_menu"（Element 菜单优先）| "generic"（通用文本）
#   - text_fallback:  "last"（限购/储值充值）| "first"（其它）
#   - dom_click_fallback: 是否启用 DOM 直点兜底
# 各生成器通过参数声明自己的形态，产出与现状**语义等价**的脚本。
#
# 实现说明（重要，勿踩坑）：这些常量**作为变量**插入到生成器的 f-string 中
# （形如 f"""...{skeleton}..."""），而 f-string 只解析**字面量**里的花括号，
# 变量值不再参与转义。因此常量内部必须写**单花括号**（正常 TS 代码）——
# 若按字面量习惯写成 {{ }}，产物会残留双花括号导致 TS 语法错误（实测确认）。

#: clickByText：带超时预算的重试点击（含 DOM 直点兜底，菜单定位器可配置）。
#: 占位符：@@MENU_LOCATORS@@ / @@TEXT_FALLBACK@@ / @@DOM_CLICK_BLOCK@@
_CLICK_BY_TEXT_TEMPLATE = """async function clickByText(scope: ClickScope, candidates: string[], timeoutMs = 20000) {
  // UAT 的菜单/页面偶发渲染较慢：在超时预算内反复尝试"等可见 → 点击"，
  // 避免把渲染延迟报成"未找到或无法点击：<菜单>"。
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const text of candidates) {
      const locators = [
@@MENU_LOCATORS@@        scope.getByRole('button', { name: new RegExp(text) }).first(),
        scope.getByRole('link', { name: new RegExp(text) }).first(),
        scope.getByText(text, { exact: false }).@@TEXT_FALLBACK@@(),
      ];
      for (const locator of locators) {
        if (!(await locator.count())) continue;
        try {
          await locator.waitFor({ state: 'visible', timeout: 2500 });
          await locator.click({ timeout: 5000 });
          console.log('[nav] clicked:', text);
          return true;
        } catch {
          // Try the next locator.
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
@@DOM_CLICK_BLOCK@@  return false;
}"""

#: Element 菜单定位器（交易云等 el-menu 应用）。注意：这些必须排在通用定位器之前，
#: 且文本兜底要取 last()——首个匹配常命中面包屑等非菜单节点（实测点不中「运营」）。
_EL_MENU_LOCATORS = """        scope.locator(`li[role="menuitem"]:has-text("${text}")`).first(),
        scope.locator(`.el-menu-item:has-text("${text}")`).first(),
        scope.locator(`.el-submenu__title:has-text("${text}")`).first(),
"""

#: DOM 直点兜底：UAT 偶发全屏 loading/地图遮罩挡住整个页面，
#: Playwright 的命中测试会全部失败，但 DOM 事件派发不受影响。
_DOM_CLICK_FALLBACK_BLOCK = """  // 最后一招：DOM 直点（el.click() 绕过命中测试）。
  for (const text of candidates) {
    const jsClicked = await scope.evaluate((t) => {
      const els = Array.from(document.querySelectorAll('li, div, span, a, button'));
      const hits = els.filter(e => (e.textContent || '').trim() === t || (e.childElementCount === 0 && (e.textContent || '').includes(t)));
      const target = hits.find(e => e.getBoundingClientRect().width > 0) || hits[hits.length - 1];
      if (!target) return false;
      (target as HTMLElement).click();
      return true;
    }, text).catch(() => false);
    if (jsClicked) {
      console.log('[nav] js-clicked:', text);
      return true;
    }
  }
"""

#: requiredClick：clickByText 的断言包装（带 timeoutMs，向后兼容默认 20000）。
_REQUIRED_CLICK_TEMPLATE = """async function requiredClick(scope: ClickScope, stepName: string, candidates: string[], timeoutMs = 20000) {
  const clicked = await clickByText(scope, candidates, timeoutMs);
  expect(clicked, `未找到或无法点击：${stepName}`).toBeTruthy();
}"""


def render_click_by_text(*, menu_selectors: str = "generic", text_fallback: str = "last",
                         dom_click_fallback: bool = True) -> str:
    """渲染 ``clickByText``（P0-2 阶段 A）。

    参数即"同源分叉"的差异点，默认值对齐**多数模板**的现有形态：
    ``menu_selectors="generic"`` / ``text_fallback="last"`` / ``dom_click_fallback=True``。
    """
    menu = _EL_MENU_LOCATORS if menu_selectors == "el_menu" else ""
    dom_block = _DOM_CLICK_FALLBACK_BLOCK if dom_click_fallback else ""
    return (
        _CLICK_BY_TEXT_TEMPLATE
        .replace("@@MENU_LOCATORS@@", menu)
        .replace("@@TEXT_FALLBACK@@", text_fallback)
        .replace("@@DOM_CLICK_BLOCK@@", dom_block)
    )


def render_required_click() -> str:
    """渲染 ``requiredClick``（4 个模板语义等价，统一为带 timeoutMs 的版本）。"""
    return _REQUIRED_CLICK_TEMPLATE


#: N5（2026-10-18）：取帧/菜单失败现场的诊断块。
#:
#: 历史问题（逐字来自 TR-517 日志）：只有 `[nav] clicked: CRM` + `YL_MENU_DUMP []`，
#: **没有任何 pages/frames 清单** —— 无法判断到底是"抓错了 frame"（取帧缺陷）、
#: 还是"菜单懒加载未完成 / 语言不符 / 权限未开"（环境/数据缺陷）。
#: 三种原因的修法完全不同，没有现场就只能猜。
#:
#: 这里用**纯字符串常量**（非 f-string）：插进模板时直接替换 ``{frame_diag_js}``，
#: 不需要把 JS 的花括号写成双份——模板里手写 JS 时最容易被花括号转义坑到。
_FRAME_DIAG_TEMPLATE = """// ---- 取帧诊断（N5）：失败现场必须能自证 ----------------------------------
// 输出形如：
//   FRAME_DIAG scope=menu-expand ; page#0 https://... ; frame#0 name= url=https://... bodyLen=812 menu=["Customer Service"] ; iframes=1 src=["..."]
// 判读口径：
//   · 某个 frame 的 menu 里**有**目标菜单 → 取错 frame（改 pickAppFrame 的遍历顺序）
//   · 所有 frame 的 menu 都为空 / bodyLen 很小 → 页面没加载完或权限/语言问题
//   · iframes=0 → 该页面根本不嵌 iframe（作用域应留在 page）
async function frameDiag(appPage: Page, scopeLabel: string): Promise<string> {
  const parts: string[] = [`scope=${scopeLabel}`];
  try {
    const pages = appPage.context().pages();
    for (let i = 0; i < pages.length; i += 1) {
      const p = pages[i];
      let closed = false;
      try { closed = p.isClosed(); } catch { /* ignore */ }
      parts.push(`page#${i} ${String(p.url()).slice(0, 120)}${closed ? ' [closed]' : ''}`);
    }
  } catch (err) {
    parts.push(`pages-failed:${String(err).slice(0, 60)}`);
  }
  try {
    const frames = appPage.frames();
    for (let i = 0; i < frames.length; i += 1) {
      const f = frames[i];
      let menu: string[] = [];
      try { menu = await visibleMenuTexts(f as unknown as ClickScope); } catch { /* ignore */ }
      let bodyLen = -1;
      try { bodyLen = (await f.locator('body').innerText()).length; } catch { /* ignore */ }
      parts.push(
        `frame#${i} name=${JSON.stringify(f.name() || '')} url=${String(f.url()).slice(0, 120)}`
        + ` bodyLen=${bodyLen} menu=${JSON.stringify(menu.slice(0, 12))}`,
      );
    }
  } catch (err) {
    parts.push(`frames-failed:${String(err).slice(0, 60)}`);
  }
  try {
    const iframes = appPage.locator('iframe');
    const total = await iframes.count();
    const srcs: string[] = [];
    for (let i = 0; i < total; i += 1) {
      srcs.push(String((await iframes.nth(i).getAttribute('src')) || '').slice(0, 120));
    }
    parts.push(`iframes=${total} src=${JSON.stringify(srcs)}`);
  } catch (err) {
    parts.push(`iframes-failed:${String(err).slice(0, 60)}`);
  }
  return parts.join(' ;; ');
}

async function frameCount(appPage: Page): Promise<number> {
  return await appPage.locator('iframe').count().catch(() => 0);
}

// ---- 取帧失败重取（rev116 / rev116-fix）-------------------------------------
// 为什么需要：getCrmScope 只在入口调用**一次**。若那一刻 iframe 还没插入 DOM
// （openCrm 的等待是 60s 上限 + 3s 缓冲，但网络慢时仍可能没到），
// 就会以 iframes=0 选中 page，之后所有菜单探测都在 page 上白找 ——
// 20s 探测 + 15s 列表轮询全打在错的 scope 上，日志看起来像"菜单不存在"。
//
// rev116-fix（2026-10-19 复核）：旧签名是 (appPage, prevFrames, reason)，
// 而 prevFrames 由调用方**再读一次** frameCount 得到 —— 两次读 DOM 之间 iframe 可能插入，
// 于是 scope=page 而 prevFrames=1，本函数判 changed=false 直接放弃，
// 「重取」在竞态下完全失效（实测 probe 序列 ["page","page"]：页面上有 iframe 却全程打在 page）。
//
// 现在改为**由 currentScope 反推**，不再二次读 DOM：
//   · 是否等待 = 「当前是不是 page」（page 才值得等 iframe 出现）；
//   · 是否切换 = 「当前 scope 的类型」与「刷新后应有类型」是否不一致（stale）——
//     page→有帧 要换（哪怕帧数一直是 1）；iframe→无帧（已脱离）也要换；类型一致就不白等。
//   · 日志里的 adopt 恒等于 stale，也就是调用方**实际会不会换**，
//     不会出现"日志写着 chosen=iframe#0、实际仍停在 page"的自证失真。
async function refreshCrmScope(
  appPage: Page, currentScope: ClickScope, reason: string, waitMs = 6000,
): Promise<{ scope: ClickScope; frames: number; stale: boolean }> {
  const onPage = currentScope === appPage;
  let frames = await frameCount(appPage);
  if (onPage) {
    const deadline = Date.now() + waitMs;
    while (frames === 0 && Date.now() < deadline) {
      await appPage.waitForTimeout(400);
      frames = await frameCount(appPage);
    }
  }
  const stale = onPage !== (frames === 0);
  const chosen = frames > 0 ? 'iframe#0' : 'page';
  console.log(`SCOPE_REFRESH ${JSON.stringify({
    reason,
    prevScope: onPage ? 'page' : 'iframe#0',
    iframes: frames,
    stale,
    chosen,
    adopt: stale,
  })}`);
  return { scope: frames > 0 ? appPage.frameLocator('iframe').first() : appPage, frames, stale };
}
"""


def render_frame_diag() -> str:
    """渲染取帧诊断块（N5）。逐字插入模板，无占位符。"""
    return _FRAME_DIAG_TEMPLATE


#: 列头**只读**比对块（N3 第 2 步，2026-10-18）。
#:
#: 为什么先做这个、而不是直接做"列检查执行器"：
#:   · 148 个子功能里只有 1 个配了列清单，没有真实列头就无法判断配置对不对；
#:   · 列头是**读**出来的（innerText），不点击、不提交、不造数据，对被测系统零副作用；
#:   · 配置与真实列头尚未对齐（实测 `columns_22` 声明 22 列、实际 23 项含勾选列），
#:     所以默认只报 `COLUMN_DIFF`、**不判失败**，否则会把配置漂移变成整批假失败。
#:     需要硬门禁时在列契约里写 `"assert_on_mismatch": true`。
#: 与 FRAME_DIAG 同理，用纯字符串常量 + 占位符替换，避免 f-string 的花括号转义坑。
_COLUMN_DIAG_TEMPLATE = r"""// ---- 列头只读比对（N3 第 2 步）：读 DOM，不点击、不提交、不造数据 --------------
// 输出两行（都可机读）：
//   COLUMN_HEADERS {"label":"yl-list","selector":".ant-table-thead > tr:first-child > th",
//                   "count":22,"headers":["Case No.","Status",...]}
//   COLUMN_DIFF    {"label":"yl-list","verdict":"match|mismatch|no_contract|no_header",
//                   "configured":22,"page_count":23,"matched":21,
//                   "missing_in_page":[...],"extra_in_page":[...],"order_monotonic":true}
// 判读口径：
//   · missing_in_page 非空 → 配置要求的列页面上没有（需求未实现／列被隐藏／权限裁剪）
//   · extra_in_page 非空  → 页面多出配置外的列（需求变更未同步到定位器配置）
//   · verdict=no_contract → 该子功能还没有列配置（当前 148 个子功能里 147 个如此，
//     这一行同时也是**采集真实列头**的入口，可用于反向补配置）
type ColumnContractItem = { key?: string; header_text?: string; aliases?: string[] };
type ColumnContract = {
  items?: ColumnContractItem[];
  source?: string;
  //: 编译期 `parse_columns` 产出的其余字段（原样带过来，供日志判读配置来源与漂移）
  table_scope?: string;
  column_count?: number;
  notes?: string[];
  //: 仅当配置显式要求时才因列头不一致判失败（默认关闭，见文件头注释）
  assert_on_mismatch?: boolean;
} | null;
const COLUMN_CONTRACT: ColumnContract = @@COLUMNS_JSON@@;

function normColText(value: unknown): string {
  return String(value == null ? '' : value).replace(/\s+/g, ' ').trim().toLowerCase();
}

//: 读表头：只取**第一行**（antd 可能有分组表头，第二行是子列，不能算进列数），
//: 并剔除固定列的滚动条占位格（它没有文本，会把列数恒定 +1）。
//: 每个候选先**限定在第一张表内**（root）：列表页上方常带统计小表/弹窗内有子表，
//: 不限定时会把多张表的表头拼成一条，列数直接翻倍。
async function readColumnHeaders(scope: ClickScope): Promise<{ headers: string[]; selector: string }> {
  const plans = [
    { root: '.ant-table', inner: '.ant-table-thead > tr:first-child > th' },
    { root: '', inner: 'thead > tr:first-child > th' },
    { root: '', inner: '[role="columnheader"]' },
  ];
  for (const plan of plans) {
    try {
      const base: any = plan.root ? scope.locator(plan.root).first() : scope;
      const cells = base.locator(plan.inner);
      const total = await cells.count();
      if (!total) continue;
      const headers: string[] = [];
      for (let i = 0; i < total; i += 1) {
        const cell = cells.nth(i);
        const cls = String((await cell.getAttribute('class')) || '');
        if (cls.indexOf('ant-table-cell-scrollbar') >= 0) continue;
        let text = '';
        try { text = await cell.innerText(); } catch { text = ''; }
        headers.push(String(text || '').replace(/\s+/g, ' ').trim().slice(0, 40));
      }
      if (headers.length) return { headers, selector: plan.root ? plan.root + ' ' + plan.inner : plan.inner };
    } catch {
      // 该选择器不适用于当前作用域，换下一个候选
    }
  }
  return { headers: [], selector: '' };
}

async function columnDiag(scope: ClickScope, label: string): Promise<void> {
  const picked = await readColumnHeaders(scope);
  console.log('COLUMN_HEADERS ' + JSON.stringify({
    label, selector: picked.selector, count: picked.headers.length, headers: picked.headers.slice(0, 40),
  }));
  const items = (COLUMN_CONTRACT && Array.isArray(COLUMN_CONTRACT.items)) ? COLUMN_CONTRACT.items : [];
  if (!items.length) {
    console.log('COLUMN_DIFF ' + JSON.stringify({ label, verdict: 'no_contract', page_count: picked.headers.length }));
    return;
  }
  if (!picked.headers.length) {
    // 列表渲染断言已过、却读不到表头：可能是非表格形态（卡片列表）或表头选择器不适用
    console.log('COLUMN_DIFF ' + JSON.stringify({ label, verdict: 'no_header', configured: items.length }));
    return;
  }
  const pageNorm = picked.headers.map(normColText);
  const used: boolean[] = pageNorm.map(() => false);
  const matched: string[] = [];
  const missing: string[] = [];
  const hitIndex: number[] = [];
  for (const item of items) {
    const name = String((item && (item.key || item.header_text)) || '');
    const rawCands = [item && item.header_text, item && item.key].concat(
      (item && Array.isArray(item.aliases)) ? item.aliases : [],
    );
    const cands: string[] = [];
    for (const c of rawCands) {
      const n = normColText(c);
      if (n && cands.indexOf(n) < 0) cands.push(n);
    }
    let hit = -1;
    for (const cand of cands) {
      for (let i = 0; i < pageNorm.length; i += 1) {
        if (!used[i] && pageNorm[i] === cand) { hit = i; break; }
      }
      if (hit >= 0) break;
    }
    if (hit >= 0) { used[hit] = true; matched.push(name); hitIndex.push(hit); }
    else missing.push(name);
  }
  const extra = picked.headers.filter((_h, i) => !used[i]);
  // 顺序：命中列在页面上的下标必须严格递增，否则"列都在、顺序被调过"
  let orderMonotonic = true;
  for (let i = 1; i < hitIndex.length; i += 1) {
    if (hitIndex[i] <= hitIndex[i - 1]) { orderMonotonic = false; break; }
  }
  const verdict = (missing.length || extra.length || !orderMonotonic) ? 'mismatch' : 'match';
  console.log('COLUMN_DIFF ' + JSON.stringify({
    label, verdict, source: (COLUMN_CONTRACT && COLUMN_CONTRACT.source) || '',
    configured: items.length, page_count: picked.headers.length, matched: matched.length,
    missing_in_page: missing.slice(0, 20), extra_in_page: extra.slice(0, 20),
    order_monotonic: orderMonotonic,
  }));
  if (verdict !== 'match' && COLUMN_CONTRACT && COLUMN_CONTRACT.assert_on_mismatch === true) {
    expect(verdict, `列头与配置不一致：缺失=${missing.join('/') || '无'}；多出=${extra.join('/') || '无'}`).toBe('match');
  }
}
// ---- 列头比对结束（此标记供测试切出本块做行为验证，勿删）-------------------
"""


def render_column_diag(columns_json: str = "null") -> str:
    """渲染列头只读比对块。``columns_json`` 由编译期列契约序列化而来（可为 null）。"""
    return _COLUMN_DIAG_TEMPLATE.replace("@@COLUMNS_JSON@@", columns_json or "null")


def _case_sort_key(case: TestCase) -> tuple[str, str]:
    """业务无关的确定性排序键：created_at（ISO 文本，避免 naive/aware 混比）→ identifier。"""
    created = getattr(case, "created_at", None)
    created_text = created.isoformat() if hasattr(created, "isoformat") else str(created or "")
    return (created_text, str(getattr(case, "identifier", "") or ""))


def _case_brief(case: TestCase, score: int | None = None) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "identifier": getattr(case, "identifier", None),
        "name": getattr(case, "name", None),
    }
    if score is not None:
        brief["score"] = score
    return brief


def _primary_case_match_text(case: TestCase) -> str:
    """主用例选择的匹配文本（只取用例自身描述，不掺入回写的 web_* 元数据）。"""
    return " ".join([case.name or "", case.module or "", case.description or ""])


def build_case_asset_bindings(
    *,
    web_test_id: Any,
    web_test_identifier: str,
    manifest: dict[str, Any],
    batch_cases: list[Any],
    primary_case_identifier: str | None,
    navigation_path: Any,
    path_template_id: Any,
    path_match_status: str,
    base_url_source: Any,
    login_profile: Any,
    sub_function_id: Any,
    bound_at: str,
) -> list[tuple[Any, dict[str, Any]]]:
    """计算「用例 → 执行资产」的绑定字段（逐条用例，含该用例在资产里的编译状态）。

    背景（2026-09-29 客观复核第 3 项）：此前只把 ``web_test_id`` 回写到**主用例**，
    其余批次用例的断言虽然确实被编译进同一个资产（rev91 已为它们建 run_case 链接），
    但用例侧看不到自己属于哪个资产 → PR-9 实测"用例↔资产"可追溯性只有 7/85。

    口径（与四指标一致，**不得**把"绑定了资产"读成"已被自动覆盖"）：
    - 绑定 = 血缘可追溯（每条批次用例都能反查资产与运行）；
    - 是否被自动覆盖由 ``web_coverage.auto_executable`` + ``assertion_count`` 表达：
      叙述型/悬空规则用例同样绑定，但 ``auto_executable=False``、``assertion_count=0``。
    """
    entries = {
        str(item.get("identifier") or ""): item
        for item in (manifest.get("cases") or [])
    }
    out: list[tuple[Any, dict[str, Any]]] = []
    for case in batch_cases:
        identifier = str(getattr(case, "identifier", "") or "")
        entry = entries.get(identifier) or {}
        assertions = list(entry.get("assertions") or [])
        compile_status = str(entry.get("compile_status") or "not_in_manifest")
        is_primary = bool(primary_case_identifier) and identifier == primary_case_identifier
        fields = dict(getattr(case, "custom_fields", None) or {})
        coverage = {
            "web_test_id": str(web_test_id),
            "web_test_identifier": web_test_identifier,
            "role": "primary" if is_primary else "batch",
            "rule_id": entry.get("rule_id") or "",
            "compile_status": compile_status,
            "assertion_count": len(assertions),
            "auto_executable": compile_status == "compiled" and bool(assertions),
            "sub_function_id": str(sub_function_id) if sub_function_id else "",
            "bound_at": bound_at,
        }
        existing = fields.get("web_coverage")
        same_target = str(fields.get("web_test_id") or "") == str(web_test_id)
        # 幂等比较必须**包含来源字段**（sub_function_id 等）：否则配置修正后（例如首次
        # 绑定漏带子功能、later 补上）旧值会一直留着，用例侧看到的仍是过期归属。
        same_coverage = isinstance(existing, dict) and all(
            existing.get(key) == coverage[key]
            for key in ("role", "rule_id", "compile_status", "assertion_count",
                        "auto_executable", "sub_function_id")
        )
        if same_target and same_coverage:
            continue  # 幂等：同一资产且编译状态未变 → 不重写
        out.append((case, {
            **fields,
            "web_test_id": str(web_test_id),
            "web_test_identifier": web_test_identifier,
            "web_validation_path": navigation_path,
            "web_path_template_id": path_template_id,
            "web_path_match_status": path_match_status,
            "web_path_base_url_source": base_url_source,
            "login_profile": login_profile,
            "sub_function_id": str(sub_function_id) if sub_function_id else None,
            "web_primary_case": is_primary,
            "web_coverage": coverage,
        }))
    return out


def select_primary_case(
    cases: list[TestCase],
    *,
    primary_test_case_id: UUID | None = None,
    sub_function_names: tuple[str | None, ...] = (),
    related_case_ids: set[UUID] | None = None,
) -> tuple[TestCase | None, dict[str, Any]]:
    """选出本次生成的「主用例」（WebTest↔TestCase 一对一绑定的锚点）。

    早期实现用**项目业务词**打分（`["储值免单", "新建活动", "营销活动", "活动管理", "B端"]`）
    挑主用例，PR-9 的 CS Module 批次因此被选中储值类用例并套错脚本模板。
    这里改为**与业务无关**的四级策略，并把决策全量写入 audit 供审计：

    1. ``explicit_request``：请求显式指定 ``primary_test_case_id``（不在批次内则报错）；
    2. ``sub_function_match``：用已解析/显式指定子功能的名称做通用词元重合打分（复用
       ``_score_sub_function_match``，属于平台通用机制而非业务硬编码）；
    3. ``existing_relation``：批次内已存在 WebTest 显式关联的用例（复用既有血缘锚点）；
    4. ``generic_order``：确定性排序（created_at → identifier）取第一条——不猜业务，仅保证可复现。
    """
    if not cases:
        return None, {
            "strategy": "none",
            "reason": "no_candidates",
            "selected": None,
            "candidates": [],
        }

    ordered = sorted(cases, key=_case_sort_key)
    audit: dict[str, Any] = {"selected": None, "candidates": []}

    if primary_test_case_id is not None:
        for case in ordered:
            if case.id == primary_test_case_id:
                audit.update({
                    "strategy": "explicit_request",
                    "reason": "请求显式指定主用例",
                    "selected": case.identifier,
                    "candidates": [_case_brief(case)],
                })
                return case, audit
        raise ValueError(
            f"[primary_case_not_in_batch] 显式指定的主用例不属于本次批次: {primary_test_case_id}"
        )

    names = tuple(name for name in sub_function_names if name)
    if names:
        scored = sorted(
            ((_score_sub_function_match(_primary_case_match_text(case), *names), case)
             for case in ordered),
            key=lambda pair: -pair[0],
        )
        best_score, best = scored[0]
        if best_score > 0:
            audit.update({
                "strategy": "sub_function_match",
                "reason": "按子功能名称与用例文本的通用词元重合度选择（同分取更早创建）",
                "best_score": best_score,
                "selected": best.identifier,
                "candidates": [_case_brief(case, score) for score, case in scored[:3]],
            })
            return best, audit

    related = related_case_ids or set()
    if related:
        linked = [case for case in ordered if case.id in related]
        if linked:
            chosen = linked[0]
            audit.update({
                "strategy": "existing_relation",
                "reason": "批次内已存在 WebTest 显式关联，复用既有血缘锚点",
                "selected": chosen.identifier,
                "candidates": [_case_brief(case) for case in linked[:5]],
            })
            return chosen, audit

    first = ordered[0]
    audit.update({
        "strategy": "generic_order",
        "reason": (
            "未显式指定主用例，且无子功能/既有关系可判定；"
            "按 created_at → identifier 确定性排序取第一条（不按业务词猜测）"
        ),
        "selected": first.identifier,
        "candidates": [_case_brief(case) for case in ordered[:5]],
    })
    return first, audit


class WebCaseLinkService:
    """把已生成的功能测试用例转换为一条可执行 Web 冒烟脚本。"""
    DEFAULT_PLACEHOLDER_BASE_URL = "https://example.invalid/console/login"
    #: 子功能自动匹配的最低分与最低"分差"（低于则视为不确定 → 不自动绑定，要求显式指定）。
    SUB_FUNCTION_MATCH_MIN_SCORE = 4
    SUB_FUNCTION_MATCH_MIN_MARGIN = 2
    DEFAULT_LOGIN_PROFILE = "WEB_TEST_DEFAULT"
    DEFAULT_WEB_VALIDATION_PATH = [
        "登录系统入口（由项目路径配置或 WEB_TEST_BASE_URL 指定）",
        "进入目标业务入口",
        "打开目标功能菜单",
        "执行用例描述中的验证操作",
    ]

    def __init__(self, session: AsyncSession):
        self.session = session
        self.test_case_repo = TestCaseRepository(session)
        self.test_run_repo = TestRunRepository(session)
        #: 最近一次子功能匹配的候选/得分/决策（写入 WebTest.generation_params 便于事后审计）
        self._last_sub_function_audit: dict[str, Any] = {}
        #: 最近一次**主用例**选择的策略/候选/决策（同样落库审计，替代原业务关键词打分）
        self._last_case_selection_audit: dict[str, Any] = {}
        #: 最近一次**模板键解析**的来源审计（2026-10-20 F-1）：
        #: 记为 `explicit:* / sub_function_name / keyword / none`，写入
        #: `web_tests.generation_params.template_routing`——回答"这条用例为什么用了这个模板"。
        self._last_template_key_audit: dict[str, Any] = {}

    async def create_minimal_loop(
        self,
        project_identifier: str,
        test_case_ids: list[UUID],
        folder_id: UUID | None = None,
        base_url: str | None = None,
        test_run_identifier: str | None = None,
        create_test_run: bool = False,
        execution_config: dict[str, Any] | None = None,
        sub_function_id: UUID | None = None,
        allow_upload_checks: bool = False,
        primary_test_case_id: UUID | None = None,
    ) -> dict[str, Any]:
        """生成/复用 WebTest，并可追加到 TestRun。

        rev62（执行三要素修复）：
        - 产出的 WebTest/脚本附件会绑定到 **Web 子功能**（显式指定或按用例文本自动匹配），
          并在同一事务内登记 `web_script_registry`，使其满足执行授权门
          （项目 + 子功能当前脚本附件 + 内容哈希）；
        - 需要创建执行 job 但无法绑定子功能 / base_url 仍是占位地址时，
          **不创建运行**并抛 ValueError（避免生成必然失败的测试运行）。

        rev63（去业务化）：
        - 主用例选择不再使用业务关键词（`[primary_case_not_in_batch]` 等结构化错误见
          `select_primary_case`），决策写入 `generation_params.case_selection`；
        - 脚本模板必须与用例业务**正向匹配**，无匹配时抛 `[unsupported_web_template]`，
          不再默认套用「储值免单」模板（实测 PR-9 因此产出内容错误的脚本）。
        """
        project = await self._get_project(project_identifier)
        # 显式指定的子功能先解析：它参与主用例选择（按子功能名称反查，替代业务关键词打分）
        requested_sub_function = await self._resolve_requested_sub_function(
            project, sub_function_id
        )
        test_case = await self._select_test_case(
            project.id,
            test_case_ids,
            primary_test_case_id=primary_test_case_id,
            sub_function=requested_sub_function,
        )
        if not test_case:
            raise ValueError("未找到可关联的功能测试用例")

        path_config = await self._resolve_path_config(project, test_case, base_url)
        resolved_base_url = path_config["base_url"]
        navigation_path = path_config["navigation_path"]
        sub_function = await self._resolve_sub_function(
            project, test_case, sub_function_id
        )
        path_config["sub_function_match"] = dict(
            getattr(self, "_last_sub_function_audit", None) or {}
        )
        path_config["case_selection"] = dict(
            getattr(self, "_last_case_selection_audit", None) or {}
        )
        # 编译接线：把**本批次全部用例**编译成执行清单（血缘：用例→规则→断言）
        # 编译属增强项：失败只降级为"无清单"，不得阻断资产/运行创建。
        batch_cases: list[TestCase] = []
        try:
            batch_cases = await self._load_batch_cases(project.id, test_case_ids)
            if test_case not in batch_cases:
                batch_cases.append(test_case)
            manifest = compile_cases(batch_cases)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[web_case_link] 用例编译失败，降级为单条清单: %s", exc)
            batch_cases = [test_case]
            manifest = compile_cases(batch_cases)
        # rev64 人工执行打标：不可自动执行的用例（dangling_rule / narrative）在
        # 编译期即打上「需人工执行」标（平台标签 + custom_fields.manual_execution），
        # 让"未执行"在用例列表/执行详情里一眼可辨——是**无法自动执行、需人工干预**，
        # 而不是用例错误。打标属增强项：失败仅告警，不阻断编译/资产创建。
        await self._tag_manual_execution_cases(project.id, manifest, batch_cases)
        # 定位器层：默认定位器可用子功能 custom_config["field_locators"] 覆盖
        locator_override = None
        upload_allow_hosts: list[str] = []
        upload_allow_rules: list[dict[str, Any]] = []
        if sub_function is not None:
            custom = getattr(sub_function, "custom_config", None) or {}
            if isinstance(custom, dict):
                locator_override = custom.get("field_locators")
                # N3（2026-10-18）：CS 侧的列配置（columns_22 / requirement_fields_16 /
                # sortable_columns）挂在 custom_config **顶层**，不在 field_locators 里，
                # 编译器读不到 → 列契约永远是 0 列，看起来像"根本没配"。这里把这三个键
                # 以 `columns_legacy` 带进定位器视图（**只读、不改库、不改变执行能力**）。
                if isinstance(locator_override, dict):
                    legacy_columns = {
                        k: custom[k] for k in ("columns_22", "requirement_fields_16",
                                               "sortable_columns", "locators")
                        if k in custom and k not in locator_override
                    }
                    if legacy_columns:
                        locator_override = {**locator_override, "columns_legacy": legacy_columns}
                # 上传隔离的放行清单：默认空 → 上传校验期间**所有写请求**都被阻断（GET 放行）
                raw_hosts = custom.get("upload_allow_hosts") or []
                if isinstance(raw_hosts, list):
                    upload_allow_hosts = [str(h) for h in raw_hosts if h]
                raw_rules = custom.get("upload_allow_rules") or []
                if isinstance(raw_rules, list):
                    upload_allow_rules = [r for r in raw_rules if isinstance(r, dict)]
        # 表单定位器：**用哪张表必须是显式决定**（详见 web_case_compiler.resolve_form_locators）。
        # 历史实现是"全局默认表"（储值那张）：任何未配置定位器的新业务模板都会套用它，
        # 去操作目标页面根本没有的控件。实测（2026-09-24 CS、2026-09-29 限购）同一根因。
        # 这里只用「子功能配置的表」或「本模板自带的表」；都没有就不生成表单检查
        # （断言如实留在 pending_locator，不冒充通过、也不判 blocked 硬失败）。
        # rev130（F-1）：**显式绑定优先**——子功能配了 template_key（或 display_name
        # 与模板标签完全一致）就直接用，不再让关键词把"配了却不生效"埋成误报。
        web_template_key = self._resolve_web_template_key(test_case, sub_function)
        path_config["template_routing"] = dict(
            getattr(self, "_last_template_key_audit", None) or {}
        )
        registered_locators = await self._load_registered_form_locators(project.id, web_template_key)
        form_plan, locator_source = build_form_plan_for_template(
            web_template_key, manifest, locator_override, registered_locators,
            allow_upload=allow_upload_checks,
        )
        if form_plan is None:
            logger.warning(
                "[web_case_link] 未生成表单检查计划（locator_source=%s，模板 %s）："
                "断言全部留在 pending_locator。常见原因＝子功能未配置 custom_config.field_locators、"
                "项目内也未注册该模板；若用例含上传类断言且未传 allow_upload_checks=true 也会走到这里。",
                locator_source,
                web_template_key,
            )
        elif not form_plan.get("checks"):
            # 定位器命中、但零可执行检查（如上传类断言默认被丢弃）——旧文案只说"无可用定位器"，
            # 会把调用方引向错误方向。这里如实说明后果（执行器不注入 ⇒ 脚本退化为骨架）。
            logger.warning(
                "[web_case_link] 模板 %s 定位器已命中（来源 %s）但**零可执行检查**：%d 条断言被跳过"
                "（原因见 generation_params.compiled_cases.skipped_assertions）"
                "⇒ 执行器不注入、脚本退化为骨架、**资产状态仍会显示 completed**。",
                web_template_key,
                locator_source,
                len(form_plan.get("skipped") or []),
            )

        # ---- fail-fast：不创建注定失败 / 内容错误的产物 ---------------------
        # 消息前缀 [code] 供前端与报告按码处理（此前只有自由文本，UI 只能弹 4s toast）。
        # 顺序即优先级：先报更具体的原因，再报"没配路径模板"。
        wants_run = bool(test_run_identifier) or create_test_run
        if wants_run and sub_function is None:
            raise ValueError(
                "[sub_function_unbound] 未找到可绑定的 Web 子功能，已跳过创建测试运行（不产生必然失败的运行）。"
                "执行授权要求三要素：项目 + 子功能当前脚本附件 + 内容哈希；"
                "请在项目「路径配置」确认该业务路径对应的子功能，"
                "或在请求中显式指定 sub_function_id 后重试。"
            )
        if wants_run and not (resolved_base_url or "").strip():
            raise ValueError(
                "[base_url_empty] base_url 为空，已跳过创建测试运行（请先配置真实入口 URL）。"
            )
        if wants_run and resolved_base_url == self.DEFAULT_PLACEHOLDER_BASE_URL:
            raise ValueError(
                "[placeholder_base_url] base_url 仍为占位地址（example.invalid），已跳过创建测试运行；"
                "请为该业务路径配置真实 UAT 入口 URL。"
            )
        if wants_run and not path_config.get("matched"):
            # 平台现有的 Web 冒烟模板都是**按具体业务流**编写的（储值充值 / 储值免单）。
            # 没匹配到路径模板就执行，会跑出"归属正确、内容错误"的脚本
            # （实测 PR-9 的 WT-3751DDA0：绑定 PR-9 用例，内容却是小杨生煎的储值免单流程，
            #   运行 45.6s 后失败在"找不到【创建】"）。
            # 这里只拒绝**建运行**；资产仍会创建并标记 pending_confirmation（保持既有契约）。
            raise ValueError(
                "[no_path_template] 该项目未匹配到任何「路径配置」模板，已拒绝创建测试运行："
                "现有冒烟脚本模板按具体业务流编写，套用会产生内容错误的脚本。"
                "请先在项目「路径配置」补充该业务路径（base_url + login_profile + navigation_path）"
                "及匹配关键词后重试。"
            )

        # 模板能力判定（fail-fast）：现有脚本模板都是**按具体业务流**编写的，
        # 用例业务无法正向匹配时必须报错，**不得**退回其它业务模板
        # （早期实现默认套「储值免单」，实测 PR-9 的 CS Module 批次因此产出内容错误的脚本）。
        # 注：web_template_key 已在上方解析（表单定位器来源需要它），此处不再重复解析。
        path_config["form_locator_source"] = locator_source

        web_test = await self._get_existing_web_test(test_case)
        if not web_test:
            web_test = await self._create_web_test_asset(
                project_identifier=project_identifier,
                project=project,
                test_case=test_case,
                folder_id=folder_id,
                base_url=resolved_base_url,
                navigation_path=navigation_path,
                path_config=path_config,
                web_template_key=web_template_key,
                sub_function=sub_function,
                manifest=manifest,
                form_plan=form_plan,
                upload_allow_hosts=upload_allow_hosts,
                upload_allow_rules=upload_allow_rules,
                allow_upload_checks=allow_upload_checks,
            )
        else:
            await self._refresh_web_test_asset(
                project_identifier=project_identifier,
                web_test=web_test,
                test_case=test_case,
                base_url=resolved_base_url,
                navigation_path=navigation_path,
                path_config=path_config,
                web_template_key=web_template_key,
                sub_function=sub_function,
                manifest=manifest,
                form_plan=form_plan,
                upload_allow_hosts=upload_allow_hosts,
                upload_allow_rules=upload_allow_rules,
            )

        test_case.automation_status = AutomationStatus.AUTOMATED
        existing_custom_fields = test_case.custom_fields or {}
        test_case.custom_fields = {
            **existing_custom_fields,
            "channel": "web",
            "executable_type": "web_test",
            "generated_from": existing_custom_fields.get("generated_from") or "document_ai",
            "web_test_id": str(web_test.id),
            "web_validation_path": navigation_path,
            "web_path_template_id": path_config.get("template_id"),
            "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
            "web_path_match_score": path_config.get("score", 0),
            "web_path_base_url_source": path_config.get("base_url_source"),
            "login_profile": path_config.get("login_profile"),
            "sub_function_id": str(sub_function.id) if sub_function else None,
        }
        test_case.description = self._merge_web_path_description(
            test_case.description,
            navigation_path,
        )
        await self._ensure_test_case_tags(project.id, test_case)
        # rev95：批次内**每条**用例都回写「用例→资产」绑定（含编译状态与断言数），
        # 让"这条用例是否被自动化覆盖"在用例列表即可回答；只绑主用例时无法回答。
        await self._bind_batch_cases_to_asset(
            web_test=web_test,
            manifest=manifest,
            batch_cases=batch_cases,
            primary_case=test_case,
            navigation_path=navigation_path,
            path_config=path_config,
            sub_function=sub_function,
        )

        test_run_info = None
        if test_run_identifier:
            test_run_info = await self._append_to_test_run(
                project=project,
                test_run_identifier=test_run_identifier,
                test_case=test_case,
                web_test=web_test,
                execution_config=execution_config or {},
                batch_cases=batch_cases,
            )
        elif create_test_run:
            test_run_info = await self._create_test_run(
                project=project,
                test_case=test_case,
                web_test=web_test,
                execution_config=execution_config or {},
                batch_cases=batch_cases,
            )

        await self.session.commit()
        return {
            "project_identifier": project_identifier,
            "test_cases": [{
                "id": str(test_case.id),
                "identifier": test_case.identifier,
                "name": test_case.name,
            }],
            "web_tests": [{
                "id": str(web_test.id),
                "identifier": web_test.identifier,
                "name": web_test.name,
                "script_path": web_test.script_path,
                "test_case_id": str(test_case.id),
                "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
                "web_path_template_id": path_config.get("template_id"),
            }],
            "test_run": test_run_info,
            "compile_receipt": self._compile_receipt(
                form_plan, manifest, locator_source, allow_upload_checks),
            "message": "已创建 1 条 Web 冒烟可执行资产",
        }

    async def _get_project(self, project_identifier: str) -> Project:
        result = await self.session.execute(
            select(Project).where(Project.identifier == project_identifier)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise ValueError(f"项目不存在: {project_identifier}")
        return project

    async def _select_test_case(
        self,
        project_id: UUID,
        test_case_ids: list[UUID],
        *,
        primary_test_case_id: UUID | None = None,
        sub_function: WebSubFunction | None = None,
    ) -> TestCase | None:
        """选出本次生成的主用例（决策见 `select_primary_case`，全部与业务无关）。"""
        result = await self.session.execute(
            select(TestCase)
            .where(TestCase.project_id == project_id)
            .where(TestCase.id.in_(test_case_ids))
        )
        cases = list(result.scalars().all())
        if not cases:
            self._last_case_selection_audit = {
                "strategy": "none",
                "reason": "no_candidates",
                "selected": None,
                "candidates": [],
            }
            return None
        # deprecated=true 的废弃用例不参与主用例选择（2026-09-29 复核）
        from app.services.deprecation import filter_active, is_deprecated
        dropped = [c.identifier for c in cases if is_deprecated(c)]
        if dropped:
            logger.warning(
                "[web_case_link] 主用例选择跳过已废弃用例 %s", dropped,
            )
        cases = filter_active(cases)
        if not cases:
            self._last_case_selection_audit = {
                "strategy": "none",
                "reason": "all_candidates_deprecated",
                "selected": None,
                "candidates": [],
            }
            return None

        related_case_ids = await self._find_related_case_ids(project_id, [c.id for c in cases])
        test_case, audit = select_primary_case(
            cases,
            primary_test_case_id=primary_test_case_id,
            sub_function_names=(
                (
                    sub_function.display_name,
                    sub_function.name,
                    getattr(sub_function, "test_scenario", None),
                )
                if sub_function is not None
                else ()
            ),
            related_case_ids=related_case_ids,
        )
        self._last_case_selection_audit = audit
        logger.info(
            "[web_case_link] 主用例选择: 策略=%s 选中=%s 候选=%s",
            audit.get("strategy"), audit.get("selected"), audit.get("candidates"),
        )
        return test_case

    async def _find_related_case_ids(
        self, project_id: UUID, case_ids: list[UUID]
    ) -> set[UUID]:
        """批次内已被 WebTest **显式关联**的用例 ID（复用既有血缘锚点，不猜业务）。"""
        if not case_ids:
            return set()
        result = await self.session.execute(
            select(WebTest.test_case_id)
            .where(WebTest.project_id == project_id)
            .where(WebTest.test_case_id.in_(case_ids))
        )
        return {row for row in result.scalars().all() if row}

    async def _load_batch_cases(
        self, project_id: UUID, test_case_ids: list[UUID]
    ) -> list[TestCase]:
        """加载本次请求涉及的**全部**用例（编译清单需要整批血缘，而非只挑一条）。

        deprecated=true 的废弃用例排除在执行/绑定之外（2026-09-29 复核：
        "已废弃但仍被执行"是不允许的）；请求显式点名废弃用例时记 WARNING 留痕。
        """
        if not test_case_ids:
            return []
        result = await self.session.execute(
            select(TestCase)
            .where(TestCase.project_id == project_id)
            .where(TestCase.id.in_(test_case_ids))
        )
        cases = list(result.scalars().all())
        from app.services.deprecation import filter_active, is_deprecated
        dropped = [c.identifier for c in cases if is_deprecated(c)]
        if dropped:
            logger.warning(
                "[web_case_link] 跳过已废弃用例 %s（deprecated=true，不参与执行/绑定）",
                dropped,
            )
        return filter_active(cases)

    @staticmethod
    def _manifest_summary(manifest: dict[str, Any] | None,
                          form_plan: dict[str, Any] | None = None) -> dict[str, Any]:
        """编译血缘摘要（写入 generation_params，便于事后核查"脚本来自哪些用例/规则"）。"""
        if not manifest or not manifest.get("case_count"):
            return {}
        summary = {
            "case_count": manifest.get("case_count", 0),
            "rule_case_count": manifest.get("rule_case_count", 0),
            "assertion_count": manifest.get("assertion_count", 0),
            "executed_assertion_count": manifest.get("soft_assertion_count", 0),
            # rev147 改名：pending_locator_count -> locator_depended_assertion_count
            # （实义是「依赖定位器的断言数」，与 skipped_assertion_count 并排；
            # 兼容回退读旧 key，旧产物 manifest 仍可解析）
            "locator_depended_assertion_count": manifest.get(
                "locator_depended_assertion_count", manifest.get("pending_locator_count", 0)
            ),
            # 四指标口径：生成用例数(case_count) / 规则用例数(rule_case_count) /
            # 编译断言数(assertion_count) / 实际执行资产数（运行侧聚合），
            # 另加"不可编译用例"逐条状态，避免被读成"已自动验证"。
            "compiled_case_count": manifest.get("compiled_case_count", 0),
            "uncompiled_case_count": manifest.get("uncompiled_case_count", 0),
            "case_status_counts": dict(manifest.get("case_status_counts") or {}),
            "uncompiled_cases": list(manifest.get("uncompiled_cases") or [])[:20],
            "uncompiled_cases_truncated": len(manifest.get("uncompiled_cases") or []) > 20,
            "rule_ids": list(manifest.get("rule_ids") or [])[:50],
            "case_identifiers": [
                c.get("identifier") for c in (manifest.get("cases") or []) if c.get("identifier")
            ][:50],
        }
        if form_plan:
            summary["form_executable_checks"] = form_plan.get("check_count", 0)
            # rev134：别名生效流水。跳过项里的 suggestions 只是"猜"，这里才是"实际改了名"。
            summary["field_alias_applied"] = list(
                form_plan.get("field_alias_applied") or [])
            summary["field_alias_dangling"] = list(
                form_plan.get("field_alias_dangling") or [])
            summary["form_skipped_no_locator"] = form_plan.get("skipped_count", 0)
            # N3（2026-10-18）：跳过项分账（list=列表域缺生成器 / form=表单缺定位器）。
            # 合成一个数会误导修法：列表域配 fields 一条都救不回来。
            summary["form_skipped_by_scope"] = dict(form_plan.get("skipped_by_scope") or {})
            summary["list_columns_declared"] = int(
                (form_plan.get("columns") or {}).get("column_count") or 0
            )
            # 未执行的断言必须可追溯（例如 .xls 无有效样本）：报告要单独列出，不能算通过
            skipped = list(form_plan.get("skipped") or [])
            summary["skipped_assertions"] = [
                {"field": s.get("field"), "kind": ",".join(s.get("kinds") or []) or (s.get("kind") or ""),
                 "format": s.get("format") or "", "reason": s.get("reason"),
                 "scope": s.get("scope") or "form",
                 "case_id": s.get("case_id") or "", "rule_id": s.get("rule_id") or "",
                 "suggestions": list(s.get("suggestions") or [])}
                for s in skipped[:20]
            ]
            summary["skipped_assertion_count"] = len(skipped)
            # 规则级负路径场景（P1-3 阶段 B）：与 skipped_assertions 同一口径——
            # "配了但从未执行 / 被配置校验拒掉"必须在**资产审计**里可见，
            # 否则只能靠人去运行日志里翻 [negative-skip] 行。
            scenarios = list(form_plan.get("negative_scenarios") or [])
            summary["negative_scenario_count"] = len(scenarios)
            summary["negative_scenarios"] = [
                {
                    "scenario_id": s.get("scenario_id"),
                    "rule_ref": s.get("rule_ref") or "",
                    "target_entry": s.get("target_entry") or "",
                    "setup_steps": [t.get("action") for t in (s.get("setup") or [])],
                    "submit_label": list(s.get("submit_label") or []),
                    "api_path_hint": (s.get("expect_reject") or {}).get("api_path_hint") or "",
                    "result_code_not": (s.get("expect_reject") or {}).get("result_code_not") or "",
                    "message_contains": list(
                        (s.get("expect_reject") or {}).get("message_contains") or []),
                    "case_id": s.get("case_id") or "",
                    "rule_id": s.get("rule_id") or "",
                }
                for s in scenarios[:20]
            ]
            rejects = list(form_plan.get("negative_scenario_rejects") or [])
            summary["negative_scenario_reject_count"] = len(rejects)
            summary["negative_scenario_rejects"] = rejects[:20]
        return summary

    @staticmethod
    def _compile_receipt(
        form_plan: dict[str, Any] | None,
        manifest: dict[str, Any] | None,
        locator_source: str,
        allow_upload_checks: bool,
    ) -> dict[str, Any]:
        """编译回执：把「本次生成了几条可执行检查、丢了什么、为什么」放进**接口响应**。

        动机（2026-10-13 实测，CS 附件类资产）：``checks`` 为空时执行器**不注入**
        （见 ``_generate_web_script`` 的 ``if form_plan and form_plan.get("checks")``），
        产物退化成无 ``FORM_PLAN`` 的骨架、运行期 ``summary.total=0``，但**资产状态仍是
        completed** ⇒ 只看状态会把"零覆盖"读成"通过"（本轮实测到两条 139KB→18KB 的假绿）。
        这里给调用方一个不必查 DB / 翻日志的回执。
        """
        plan = form_plan or {}
        checks = plan.get("checks") or []
        skipped = list(plan.get("skipped") or [])
        # 用例声明的断言类型：用于区分"配置开关关掉了上传检查"与"字段名对不上定位器"
        declared: set[str] = set()
        for case in (manifest or {}).get("cases") or []:
            for assertion in case.get("assertions") or []:
                if assertion.get("kind"):
                    declared.add(str(assertion["kind"]))
        upload_kinds = declared & {"file_size", "file_format", "upload_file"}

        warnings: list[str] = []
        # 场景（规则级负路径）与字段级检查同权：有场景时执行器照常注入、脚本可执行，
        # 不能按「零可执行检查」告警——那会把"配了场景、等着跑"误报成"零覆盖"。
        scenarios = plan.get("negative_scenarios") or []
        if form_plan is None:
            warnings.append(
                f"[no_form_plan] 未生成表单检查计划（locator_source={locator_source}）："
                "断言全部留在 pending_locator，本次脚本不含任何表单断言。"
                "常见原因＝子功能未配置 custom_config.field_locators 且项目内未注册该模板。"
            )
        elif not checks and not scenarios:
            kinds = sorted({str(s.get("kind") or "") for s in skipped if s.get("kind")})
            warnings.append(
                f"[zero_executable_checks] 表单计划**零可执行检查**（{len(skipped)} 条被跳过"
                + (f"，类型 {','.join(kinds)}" if kinds else "")
                + "）：执行器不注入、脚本退化为骨架、运行期 summary.total=0，"
                "**资产状态仍会显示 completed** —— 勿据此判定「已覆盖」。"
            )
        if not allow_upload_checks and upload_kinds:
            warnings.append(
                f"[upload_checks_disabled] 用例声明了上传类断言（{','.join(sorted(upload_kinds))}）"
                "但 allow_upload_checks=false（默认）：这些断言被整条丢弃（也可能因此退化为零检查）。"
                "如确需验证，请显式传 allow_upload_checks=true。"
            )
        # rev134：悬空别名必须显式告警。旧实现里"别名配错"与"别名没配"在回执上完全一样
        # （都是无定位器跳过），配置方会去改定位器，而真正要改的是别名目标键。
        dangling_aliases = list(plan.get("field_alias_dangling") or [])
        if dangling_aliases:
            warnings.append(
                "[field_alias_dangling] 别名目标不在定位器表里（配了也不会生效）："
                + "、".join("%s→%s" % (d.get("from"), d.get("to"))
                           for d in dangling_aliases[:6])
            )
        return {
            "form_locator_source": locator_source,
            "executable": bool(checks) or bool(scenarios),
            "form_executable_checks": len(checks),
            "negative_scenario_count": len(scenarios),
            "assertion_check_count": plan.get("assertion_check_count", 0),
            "skipped_assertion_count": len(skipped),
            # rev134：别名真实命中（配了但没命中会在这里显示为空列表，便于当场发现拼错）
            "field_alias_applied": list(plan.get("field_alias_applied") or []),
            "field_alias_dangling": dangling_aliases,
            # N3（2026-10-18）：跳过项按作用域分账。`list` 的修法是"补列表检查生成器"，
            # `form` 的修法是"配 field_locators.fields"——合成一个数会让人按错方向修。
            "skipped_by_scope": dict(plan.get("skipped_by_scope") or {}),
            "list_columns_declared": int((plan.get("columns") or {}).get("column_count") or 0),
            "skipped_assertions": [
                {"field": s.get("field"), "kind": s.get("kind") or "",
                 "scope": s.get("scope") or "form", "reason": s.get("reason")}
                for s in skipped[:20]
            ],
            "locator_depended_assertion_count": (manifest or {}).get(
                "locator_depended_assertion_count", (manifest or {}).get("pending_locator_count", 0)
            ),
            "allow_upload_checks": bool(allow_upload_checks),
            "warnings": warnings,
        }

    async def _get_existing_web_test(self, test_case: TestCase) -> WebTest | None:
        result = await self.session.execute(
            select(WebTest)
            .where(WebTest.project_id == test_case.project_id)
            .where(WebTest.test_case_id == test_case.id)
            .where(WebTest.generated_by_agent == "web_case_link_service")
            .order_by(WebTest.created_at.asc())
        )
        return result.scalars().first()

    async def precheck_minimal_loop(
        self,
        project_identifier: str,
        *,
        module: str | None = None,
        module_in: list[str] | None = None,
        module_source: str | None = None,
        original_module: str | None = None,
        has_requirement_rule: bool | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        test_case_ids: list[UUID] | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """只读预检（dry-run）：cohort 筛选 + 唯一键判定，**零写入**。

        阶段 1 第 2 步（2026-10-17 评审约定）：
        - cohort 筛选只用**稳定字段**：``test_cases.module``（实列）、
          ``custom_fields.module_source``、``custom_fields.original_module``、
          ``custom_fields.requirement_rule_id``（有无）、``created_at`` 窗口；
          不使用显示名/业务关键词，避免两套 module 标签家族因显示名混计；
        - 唯一键判定与 ``_get_existing_web_test`` 同口径：仅 ``web_case_link_service``
          族按 ``(test_case_id, sub_function_id)`` 语义取最早行（本方法按
          created_at 取首行预演，不写库）；
        - 全程只执行 SELECT，不 add / 不 flush / 不 commit；
        - 顺带暴露 ``web_agent`` 族行数（探针资产堆积的只读观察口径），
          供阶段 2 业务候选确认时参考。
        """
        project = await self._get_project(project_identifier)

        # JSONB 表达式只构造一次并复用：asyncpg 下 WHERE / GROUP BY 若各自
        # 构造同形表达式，会生成不同绑定名 → Postgres 报 GroupingError。
        module_source_expr = TestCase.custom_fields["module_source"].astext
        original_module_expr = TestCase.custom_fields["original_module"].astext
        rule_len_expr = func.length(
            func.coalesce(TestCase.custom_fields["requirement_rule_id"].astext, "")
        )

        conditions = [TestCase.project_id == project.id]
        if test_case_ids:
            conditions.append(TestCase.id.in_(test_case_ids))
        if module:
            conditions.append(TestCase.module == module)
        if module_in:
            conditions.append(TestCase.module.in_(module_in))
        if module_source:
            conditions.append(module_source_expr == module_source)
        if original_module:
            conditions.append(original_module_expr == original_module)
        if has_requirement_rule is True:
            conditions.append(rule_len_expr > 0)
        elif has_requirement_rule is False:
            conditions.append(rule_len_expr == 0)
        if created_from:
            conditions.append(TestCase.created_at >= created_from)
        if created_to:
            conditions.append(TestCase.created_at <= created_to)
        where = *conditions,

        # 1) cohort 总量（不受 limit 截断）
        total_result = await self.session.execute(
            select(func.count()).select_from(TestCase).where(*where)
        )
        cohort_total = int(total_result.scalar() or 0)

        # 2) 标签家族分布：按 (module 实列, module_source) 分组，覆盖全部 cohort
        family_result = await self.session.execute(
            select(
                TestCase.module,
                module_source_expr,
                func.count(),
            )
            .where(*where)
            .group_by(
                TestCase.module,
                module_source_expr,
            )
            .order_by(func.count().desc())
        )
        label_families = [
            {"module": row[0], "module_source": row[1], "case_count": int(row[2])}
            for row in family_result.all()
        ]

        # 3) cohort 明细（受 limit 截断；created_at → identifier 确定性排序）
        rows_result = await self.session.execute(
            select(TestCase)
            .where(*where)
            .order_by(TestCase.created_at.asc(), TestCase.identifier.asc())
            .limit(limit)
        )
        cases = list(rows_result.scalars().all())
        case_ids = [case.id for case in cases]

        # 4) 唯一键判定：service 族既有资产（与 _get_existing_web_test 同口径）
        service_family: dict[UUID, WebTest] = {}
        rows_by_agent: dict[UUID, dict[str, int]] = {}
        if case_ids:
            asset_result = await self.session.execute(
                select(
                    WebTest.test_case_id,
                    WebTest.generated_by_agent,
                    func.count(),
                )
                .where(WebTest.project_id == project.id)
                .where(WebTest.test_case_id.in_(case_ids))
                .group_by(WebTest.test_case_id, WebTest.generated_by_agent)
            )
            for row in asset_result.all():
                rows_by_agent.setdefault(row[0], {})[str(row[1] or "")] = int(row[2])

            svc_result = await self.session.execute(
                select(WebTest)
                .where(WebTest.project_id == project.id)
                .where(WebTest.test_case_id.in_(case_ids))
                .where(WebTest.generated_by_agent == "web_case_link_service")
                .order_by(WebTest.created_at.asc())
            )
            for asset in svc_result.scalars().all():
                service_family.setdefault(asset.test_case_id, asset)

        case_items: list[dict[str, Any]] = []
        reuse_count = 0
        create_count = 0
        for case in cases:
            existing = service_family.get(case.id)
            fields = case.custom_fields if isinstance(case.custom_fields, dict) else {}
            action = "reuse" if existing is not None else "create"
            if action == "reuse":
                reuse_count += 1
            else:
                create_count += 1
            existing_brief = None
            if existing is not None:
                existing_brief = {
                    "identifier": existing.identifier,
                    "name": existing.name,
                    "sub_function_id": str(existing.sub_function_id) if existing.sub_function_id else None,
                    "created_at": existing.created_at.isoformat() if existing.created_at else None,
                }
            case_items.append({
                "id": str(case.id),
                "identifier": case.identifier,
                "name": case.name,
                "module": case.module,
                "module_source": fields.get("module_source"),
                "original_module": fields.get("original_module"),
                "requirement_rule_id": fields.get("requirement_rule_id"),
                "created_at": case.created_at.isoformat() if case.created_at else None,
                "predicted_action": action,
                "existing_service_web_test": existing_brief,
                "web_test_rows_by_agent": rows_by_agent.get(case.id, {}),
            })

        # 5) 资产族统计（cohort 内）：service 族行数守恒口径 + 其他族（探针）暴露
        service_rows_total = sum(
            (rows_by_agent.get(cid, {}).get("web_case_link_service", 0) for cid in case_ids),
            start=0,
        )
        other_rows_total = sum(
            (sum({k: v for k, v in rows_by_agent.get(cid, {}).items() if k != "web_case_link_service"}.values())
             for cid in case_ids),
            start=0,
        )

        return {
            "read_only": True,
            "project": {"id": str(project.id), "identifier": project.identifier},
            "filters_applied": {
                "module": module,
                "module_in": module_in,
                "module_source": module_source,
                "original_module": original_module,
                "has_requirement_rule": has_requirement_rule,
                "created_from": created_from.isoformat() if created_from else None,
                "created_to": created_to.isoformat() if created_to else None,
                "test_case_ids_count": len(test_case_ids) if test_case_ids else 0,
                "limit": limit,
            },
            "cohort_total": cohort_total,
            "returned": len(case_items),
            "label_families": label_families,
            "summary": {
                "predicted_reuse": reuse_count,
                "predicted_create": create_count,
                "service_family_rows_in_cohort": service_rows_total,
                "other_agent_rows_in_cohort": other_rows_total,
            },
            "cases": case_items,
        }


    async def _resolve_path_config(
        self,
        project: Project,
        test_case: TestCase,
        requested_base_url: str | None,
    ) -> dict[str, Any]:
        template, score = await self._match_path_template(project.id, test_case)
        matched = template is not None and score > 0
        navigation_path = (
            list(template.navigation_path or [])
            if matched and template and template.navigation_path
            else list(self.DEFAULT_WEB_VALIDATION_PATH)
        )
        if requested_base_url:
            base_url = requested_base_url
            base_url_source = "request"
        elif matched and template and template.base_url:
            base_url = template.base_url
            base_url_source = "template"
        else:
            base_url = self.DEFAULT_PLACEHOLDER_BASE_URL
            base_url_source = "placeholder"

        return {
            "matched": matched,
            "score": score,
            "template_id": str(template.id) if matched and template else None,
            "template_name": template.name if matched and template else None,
            "base_url": base_url,
            "base_url_source": base_url_source,
            "login_profile": (
                template.login_profile
                if matched and template and template.login_profile
                else self.DEFAULT_LOGIN_PROFILE
            ),
            "navigation_path": navigation_path,
        }

    async def _match_path_template(
        self,
        project_id: UUID,
        test_case: TestCase,
    ) -> tuple[ProjectWebPathTemplate | None, int]:
        result = await self.session.execute(
            select(ProjectWebPathTemplate)
            .where(ProjectWebPathTemplate.project_id == project_id)
            .where(ProjectWebPathTemplate.status == "active")
        )
        candidates = list(result.scalars().all())
        if not candidates:
            return None, 0

        text = self._build_case_match_text(test_case)
        scored = sorted(
            [(self._score_path_template(template, test_case, text), template) for template in candidates],
            key=lambda pair: pair[0],
            reverse=True,
        )
        score, template = scored[0]
        if score <= 0:
            return None, 0
        return template, score

    async def _load_registered_form_locators(
        self,
        project_id: UUID,
        template_key: Any,
    ) -> dict[str, Any] | None:
        """读取本项目为该模板键注册的定位器表（P6 数据化注册）。

        - 只有 ``status='active'`` 的注册参与解析；
        - 读不到（老项目未注册）返回 ``None`` → 解析继续走内置表兜底；
        - 读取异常不阻断编译：告警后返回 ``None``（等效于"未注册"）。
        """
        key = str(template_key or "").strip()
        if not key:
            return None
        try:
            row = (
                await self.session.execute(
                    select(WebFormTemplate)
                    .where(WebFormTemplate.project_id == project_id)
                    .where(WebFormTemplate.template_key == key)
                    .where(WebFormTemplate.status == "active")
                )
            ).scalars().first()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[web_case_link] 读取模板注册失败（按未注册处理）: %s", exc)
            return None
        if row is None or not isinstance(row.fields, dict) or not row.fields:
            return None
        return self._merge_registered_locators(row)

    @staticmethod
    def _merge_registered_locators(row: Any) -> dict[str, Any]:
        """把注册行还原成**完整**定位器表。

        委托给 ``web_case_compiler.merge_registered_locators``（单一实现），
        使执行链路与 ``/resolve-preview`` 预览接口用同一段逻辑——
        两处各写一份时曾出现"预览说没问题、执行却报
        ``FORM_PLAN.locators.form_button is not iterable`` 硬失败"的分叉（TR-163）。
        """
        return merge_registered_locators(row)

    async def _resolve_requested_sub_function(
        self,
        project: Project,
        requested_sub_function_id: UUID | None,
    ) -> WebSubFunction | None:
        """解析**显式指定**的子功能（校验属于本项目）；未指定时返回 None。

        抽成独立方法是为了让"主用例选择"能先用子功能做反查（不再依赖业务关键词）。
        """
        if not requested_sub_function_id:
            return None
        sub_function = await self.session.get(WebSubFunction, requested_sub_function_id)
        if sub_function is None or sub_function.project_id != project.id:
            raise ValueError(
                f"指定的 Web 子功能不存在或不属于本项目: {requested_sub_function_id}"
            )
        return sub_function

    async def _resolve_sub_function(
        self,
        project: Project,
        test_case: TestCase,
        requested_sub_function_id: UUID | None = None,
    ) -> WebSubFunction | None:
        """解析该用例对应的 Web 子功能（执行三要素的绑定目标）。

        - 显式指定优先（并校验属于本项目）；
        - 否则按"用例文本 ↔ 子功能/功能名称"重合打分自动匹配，低于阈值视为无法绑定。
        """
        if requested_sub_function_id:
            sub_function = await self._resolve_requested_sub_function(
                project, requested_sub_function_id
            )
            self._last_sub_function_audit = {
                "mode": "explicit",
                "decision": "matched",
                "selected": sub_function.identifier,
            }
            return sub_function

        rows = (
            await self.session.execute(
                select(WebSubFunction, WebFunction)
                .join(WebFunction, WebSubFunction.function_id == WebFunction.id)
                .where(WebSubFunction.project_id == project.id)
                # 确定性顺序：同分时取更早创建的子功能（避免绑定结果随机）
                .order_by(WebSubFunction.created_at.asc())
            )
        ).all()
        if not rows:
            self._last_sub_function_audit = {
                "mode": "auto",
                "decision": "no_candidate",
                "candidates": [],
            }
            return None

        text = self._build_case_match_text(test_case, include_web_metadata=False)
        scored: list[tuple[int, WebSubFunction]] = []
        for sub_function, function in rows:
            score = _score_sub_function_match(
                text,
                sub_function.display_name,
                sub_function.name,
                sub_function.test_scenario,
                function.display_name,
                function.name,
                function.business_module,
            )
            scored.append((score, sub_function))
        scored.sort(key=lambda pair: -pair[0])

        candidates = [
            {"sub_function": sf.identifier, "name": sf.display_name, "score": score}
            for score, sf in scored[:3]
        ]
        best_score, best = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0
        margin = best_score - second_score

        decision = "matched"
        if best_score < self.SUB_FUNCTION_MATCH_MIN_SCORE:
            decision = "below_threshold"
        elif margin < self.SUB_FUNCTION_MATCH_MIN_MARGIN:
            decision = "ambiguous_margin"
        self._last_sub_function_audit = {
            "mode": "auto",
            "decision": decision,
            "threshold": self.SUB_FUNCTION_MATCH_MIN_SCORE,
            "min_margin": self.SUB_FUNCTION_MATCH_MIN_MARGIN,
            "best_score": best_score,
            "second_score": second_score,
            "margin": margin,
            "candidates": candidates,
            "selected": best.identifier if decision == "matched" else None,
        }
        logger.info(
            "[web_case_link] 子功能自动匹配: 决策=%s 最高=%s(%s) 次高=%s 分差=%s 候选=%s",
            decision, best_score, best.identifier, second_score, margin, candidates,
        )
        if decision != "matched":
            # 不确定时不绑定：交由上层 fail-fast（要求显式指定 sub_function_id），
            # 避免"授权通过但绑到了错误业务子功能"的隐蔽问题。
            return None
        return best

    _WEB_METADATA_KEYS = {
        "channel",
        "executable_type",
        "generated_from",
        "web_test_id",
        "web_validation_path",
        "web_path_template_id",
        "web_path_match_status",
        "web_path_match_score",
        "web_path_base_url_source",
        "login_profile",
        "sub_function_id",
    }

    def _build_case_match_text(
        self, test_case: TestCase, *, include_web_metadata: bool = True
    ) -> str:
        custom_fields = test_case.custom_fields or {}
        description = test_case.description or ""
        if not include_web_metadata:
            # 该用例此前生成时回写的 web_* 元数据与"Web验证路径：…"来自"上一次匹配结果"，
            # 用它参与匹配会自我循环（本轮实测：储值充值用例被匹配到储值免单子功能）。
            custom_fields = {
                k: v
                for k, v in custom_fields.items()
                if k not in self._WEB_METADATA_KEYS and not str(k).startswith("web_")
            }
            description = _WEB_VALIDATION_PATH_RE.sub("", description).strip()
        custom_text = json.dumps(custom_fields, ensure_ascii=False)
        return " ".join([
            test_case.identifier or "",
            test_case.name or "",
            test_case.module or "",
            test_case.case_kind or "",
            description,
            test_case.preconditions or "",
            custom_text,
        ])

    def _score_path_template(
        self,
        template: ProjectWebPathTemplate,
        test_case: TestCase,
        text: str,
    ) -> int:
        score = 0
        field_values = {
            "side": "",
            "module": test_case.module or "",
            "business_type": "",
            "action": "",
        }
        custom_fields = test_case.custom_fields or {}
        for key in ["side", "business_type", "action"]:
            value = custom_fields.get(key) or custom_fields.get(f"web_{key}") or ""
            field_values[key] = str(value)

        for attr, weight in [
            ("side", 3),
            ("module", 4),
            ("business_type", 5),
            ("action", 3),
        ]:
            expected = getattr(template, attr) or ""
            actual = field_values.get(attr) or ""
            if expected and actual and expected in actual:
                score += weight
            elif expected and expected in text:
                score += max(1, weight - 1)

        for keyword in template.match_keywords or []:
            if keyword and keyword in text:
                score += 2
        return score

    def _merge_web_path_description(
        self,
        description: str | None,
        navigation_path: list[str],
    ) -> str:
        base_description = re.sub(
            r"(\r?\n){0,2}Web验证路径：.*?(?=(\r?\n){2,}|\Z)",
            "",
            description or "",
            flags=re.S,
        ).strip()
        path_description = "Web验证路径：" + " -> ".join(navigation_path)
        return f"{base_description}\n\n{path_description}" if base_description else path_description

    async def _create_web_test_asset(
        self,
        project_identifier: str,
        project: Project,
        test_case: TestCase,
        folder_id: UUID | None,
        base_url: str,
        navigation_path: list[str],
        path_config: dict[str, Any],
        sub_function: WebSubFunction | None = None,
        manifest: dict[str, Any] | None = None,
        form_plan: dict[str, Any] | None = None,
        upload_allow_hosts: list[str] | None = None,
        upload_allow_rules: list[dict[str, Any]] | None = None,
        allow_upload_checks: bool = False,
        web_template_key: str | None = None,
    ) -> WebTest:
        web_label = self._web_test_label(test_case, web_template_key)
        object_name = (
            f"web-tests/{project_identifier}/minimal-loop/{test_case.id}/"
            f"{uuid4().hex}/test-script.spec.ts"
        )
        script_content = self._stamp_compiler_rev(self._generate_playwright_script(
            test_case, base_url, navigation_path, manifest=manifest, form_plan=form_plan,
            upload_allow_hosts=upload_allow_hosts,
                upload_allow_rules=upload_allow_rules,
            project_identifier=project_identifier,
            web_template_key=web_template_key,
        ))
        storage_info = self._store_script(object_name, script_content)

        web_test = WebTest(
            project_id=project.id,
            folder_id=folder_id or test_case.folder_id,
            test_case_id=test_case.id,
            # rev62：执行三要素要求子功能绑定（与 save_web_test_script 产物同构）
            function_id=sub_function.function_id if sub_function else None,
            sub_function_id=sub_function.id if sub_function else None,
            identifier=f"WT-{uuid4().hex[:8].upper()}",
            name=f"{test_case.identifier} - {web_label}冒烟",
            description=f"由文档生成用例自动创建的 {web_label} 最小闭环冒烟脚本",
            base_url=base_url,
            script_path=object_name,
            script_format="playwright",
            script_language="typescript",
            test_config={
                "base_url": base_url,
                "storage": storage_info,
                "credential_env": ["WEB_TEST_USERNAME", "WEB_TEST_PASSWORD"],
                "web_path_template_id": path_config.get("template_id"),
                "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
                "web_path_base_url_source": path_config.get("base_url_source"),
                "login_profile": path_config.get("login_profile"),
                # 上传类断言默认关闭（可能触发向 UAT 的上传请求）；开启后执行器仍会拦截上传
                "allow_upload_checks": bool(allow_upload_checks),
            },
            target_pages=[{"name": f"{web_label}入口", "url": base_url}],
            test_flows=[{
                "name": f"登录并进入{web_label}入口",
                "steps": navigation_path,
            }],
            generated_by_agent="web_case_link_service",
            generation_params={
                "mode": "minimal_loop",
                "web_path_template_id": path_config.get("template_id"),
                "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
                "sub_function_id": str(sub_function.id) if sub_function else None,
                "sub_function_match": path_config.get("sub_function_match"),
                # 去业务化审计：主用例如何选出、用了哪套业务模板（历史实现默认套储值免单，无法回溯）
                "case_selection": path_config.get("case_selection"),
                # 表单定位器来源审计（P1）：sub_function_override / template_default / none
                "form_locator_source": path_config.get("form_locator_source"),
                "web_template_key": web_template_key,
                # rev130（F-1）：模板键**来源**审计——explicit:custom_config.template_key /
                # sub_function.display_name / keyword。回答"为什么这条用例用了这个模板"，
                # 也让"配了 template_key 却仍按关键词走"这类回归一眼可见。
                "template_routing": path_config.get("template_routing"),
                # P0-2：记录产出该脚本的编译器版本（与 spec 头部指纹同源），便于按版本筛旧产物
                "compiler_rev": COMPILER_REV,
                "compiler_digest": compiler_source_digest(),
                "allow_upload_checks": bool(allow_upload_checks),
                "compiled_cases": self._manifest_summary(manifest, form_plan),
            },
            total_pages=1,
            total_flows=1,
        )
        # N7：把编译清单抽成可查询列（口径见 web_test_compile_manifest）。
        # 必须在 flush 前赋值，让 INSERT 一次带全；事后再 UPDATE 会留一个"列全空"的中间态。
        apply_compile_manifest(web_test)
        self.session.add(web_test)
        await self.session.flush()

        attachment = Attachment(
            entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
            # 与 save_web_test_script 一致：脚本附件归属子功能（无绑定时退回 WebTest）
            entity_id=sub_function.id if sub_function else web_test.id,
            project_id=project.id,
            file_name="test-script.spec.ts",
            file_size=len(script_content.encode("utf-8")),
            content_type="text/plain",
            object_name=object_name,
            description=f"{web_label}最小闭环 Web 冒烟脚本",
            created_by="web_case_link_service",
        )
        self.session.add(attachment)
        await self.session.flush()
        await self._register_script_provenance(
            project_identifier,
            web_test,
            script_content,
            object_name=object_name,
            sub_function_id=sub_function.id if sub_function else None,
        )
        await self.session.refresh(web_test)
        return web_test

    async def _refresh_web_test_asset(
        self,
        project_identifier: str,
        web_test: WebTest,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        path_config: dict[str, Any],
        sub_function: WebSubFunction | None = None,
        manifest: dict[str, Any] | None = None,
        form_plan: dict[str, Any] | None = None,
        upload_allow_hosts: list[str] | None = None,
        upload_allow_rules: list[dict[str, Any]] | None = None,
        web_template_key: str | None = None,
    ) -> None:
        web_label = self._web_test_label(test_case, web_template_key)
        # 历史产物可能没有 script_path（空）→ 重新生成标准对象名，避免上传到空名
        if not (web_test.script_path or "").strip():
            web_test.script_path = (
                f"web-tests/{project_identifier}/minimal-loop/{test_case.id}/"
                f"{uuid4().hex}/test-script.spec.ts"
            )
        script_content = self._stamp_compiler_rev(self._generate_playwright_script(
            test_case, base_url, navigation_path, manifest=manifest, form_plan=form_plan,
            upload_allow_hosts=upload_allow_hosts,
                upload_allow_rules=upload_allow_rules,
            project_identifier=project_identifier,
            web_template_key=web_template_key,
        ))
        storage_info = self._store_script(web_test.script_path, script_content)
        existing_config = web_test.test_config or {}
        web_test.base_url = base_url
        # rev62：补齐/修正子功能绑定（历史产物为 None → 本次一并修好）
        if sub_function is not None:
            web_test.sub_function_id = sub_function.id
            web_test.function_id = sub_function.function_id
        web_test.test_config = {
            **existing_config,
            "base_url": base_url,
            "storage": storage_info,
            "credential_env": ["WEB_TEST_USERNAME", "WEB_TEST_PASSWORD"],
            "web_path_template_id": path_config.get("template_id"),
            "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
            "web_path_base_url_source": path_config.get("base_url_source"),
            "login_profile": path_config.get("login_profile"),
            "case_selection": path_config.get("case_selection"),
            "web_template_key": web_template_key,
        }
        web_test.name = f"{test_case.identifier} - {web_label}冒烟"
        web_test.description = f"由文档生成用例自动创建的 {web_label} 最小闭环冒烟脚本"
        web_test.target_pages = [{"name": f"{web_label}入口", "url": base_url}]
        web_test.test_flows = [{
            "name": f"登录并进入{web_label}入口",
            "steps": navigation_path,
        }]
        existing_generation_params = web_test.generation_params or {}
        web_test.generation_params = {
            **existing_generation_params,
            "mode": "minimal_loop",
            "web_path_template_id": path_config.get("template_id"),
            "web_path_match_status": "matched" if path_config["matched"] else "pending_confirmation",
            "sub_function_id": str(sub_function.id) if sub_function else None,
            "sub_function_match": path_config.get("sub_function_match"),
            "case_selection": path_config.get("case_selection"),
            "form_locator_source": path_config.get("form_locator_source"),
            "web_template_key": web_template_key,
            # P0-2：重新生成时刷新编译器版本（旧值不能保留，否则历史记录会一直显示旧 rev）
            "compiler_rev": COMPILER_REV,
            "compiler_digest": compiler_source_digest(),
            "compiled_cases": self._manifest_summary(manifest, form_plan),
        }
        # N7：重编译会改写 compiled_cases，列必须同步刷新；否则列表页展示的是上一次编译的检查数
        apply_compile_manifest(web_test, generation_params=web_test.generation_params)
        await self.session.flush()
        # rev62：脚本内容可能已变 → 必须同步更新登记哈希，否则授权以"hash 不符"拒绝
        await self._register_script_provenance(
            project_identifier,
            web_test,
            script_content,
            object_name=web_test.script_path,
            sub_function_id=sub_function.id if sub_function else None,
        )

    async def _register_script_provenance(
        self,
        project_identifier: str,
        web_test: WebTest,
        script_content: str,
        *,
        object_name: str,
        sub_function_id: UUID | None = None,
    ) -> None:
        """登记脚本来源（三要素：真实项目 + 子功能脚本附件 + 内容哈希）。

        与执行侧 `write_bytes(MinIO 字节)` 保持同一哈希基准（utf-8 编码字节）。
        不在此处 commit——由 create_minimal_loop 统一提交，避免半成品。
        """
        from app.agents.tools.web.script_provenance import (
            register_script_provenance,
            sha256_hex,
        )

        script_bytes = script_content.encode("utf-8")
        attachment = (
            await self.session.execute(
                select(Attachment)
                .where(Attachment.object_name == object_name)
                .limit(1)
            )
        ).scalars().first()
        if attachment is None:
            # 历史产物缺少附件记录时补建，归属子功能（无绑定时退回 WebTest）
            attachment = Attachment(
                entity_type=AttachmentEntityType.WEB_TEST_SCRIPT,
                entity_id=sub_function_id or web_test.id,
                project_id=web_test.project_id,
                file_name="test-script.spec.ts",
                file_size=len(script_bytes),
                content_type="text/plain",
                object_name=object_name,
                description="最小闭环 Web 冒烟脚本（补登记）",
                created_by="web_case_link_service",
            )
            self.session.add(attachment)
            await self.session.flush()
        elif sub_function_id is not None and attachment.entity_id != sub_function_id:
            # 历史产物附件挂在 WebTest 上 → 本次修正为子功能归属
            attachment.entity_id = sub_function_id
            attachment.file_size = len(script_bytes)
        await register_script_provenance(
            self.session,
            project_identifier,
            attachment.id,
            sha256_hex(script_bytes),
            "typescript",
            "playwright",
            created_by="web_case_link_service",
            version_status="effective",
        )

    def _store_script(self, object_name: str, content: str) -> dict[str, Any]:
        data = content.encode("utf-8")
        backup_path = self._write_local_backup(object_name, data)
        try:
            MinIOClient.upload_bytes(
                object_name=object_name,
                data=data,
                content_type="text/plain",
            )
            return {"minio": True, "local_backup": str(backup_path)}
        except Exception as exc:
            return {
                "minio": False,
                "local_backup": str(backup_path),
                "warning": f"MinIO 上传失败，已使用本地备份兜底: {exc}",
            }

    def _write_local_backup(self, object_name: str, data: bytes) -> Path:
        workspace_root = Path(settings.web_cli_workspace_root).resolve()
        backup_path = workspace_root / "artifacts_backup" / object_name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(data)
        return backup_path

    async def _bind_batch_cases_to_asset(
        self,
        *,
        web_test: WebTest,
        manifest: dict[str, Any],
        batch_cases: list[TestCase],
        primary_case: TestCase,
        navigation_path: Any,
        path_config: dict[str, Any],
        sub_function: WebSubFunction | None,
    ) -> int:
        """rev95：把「用例 → 资产」绑定回写到**全部批次用例**（幂等；增强项，失败不阻断）。

        与 rev64「需人工执行」打标同一套路：批次级回写、逐条幂等、异常只告警。
        只有主用例带 `web_test_id` 时，"这条用例有没有被自动化覆盖"在用例列表里
        无法回答（PR-9 实测 85 条只有 7 条可追溯）。
        """
        try:
            bindings = build_case_asset_bindings(
                web_test_id=web_test.id,
                web_test_identifier=web_test.identifier,
                manifest=manifest,
                batch_cases=batch_cases,
                primary_case_identifier=getattr(primary_case, "identifier", None),
                navigation_path=navigation_path,
                path_template_id=path_config.get("template_id"),
                path_match_status=(
                    "matched" if path_config.get("matched") else "pending_confirmation"
                ),
                base_url_source=path_config.get("base_url_source"),
                login_profile=path_config.get("login_profile"),
                sub_function_id=getattr(sub_function, "id", None),
                bound_at=datetime.now().isoformat(timespec="seconds"),
            )
            for case, fields in bindings:
                case.custom_fields = fields
            if bindings:
                logger.info(
                    "[web_case_link] 用例→资产绑定回写: %d/%d 条（资产 %s）",
                    len(bindings), len(batch_cases), web_test.identifier,
                )
            return len(bindings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[web_case_link] 用例→资产绑定回写失败（忽略）: %s", exc)
            return 0

    async def _ensure_test_case_tags(self, project_id: UUID, test_case: TestCase) -> None:
        for tag_name in ["AI生成", "最小闭环", "Web冒烟"]:
            tag = await self.test_case_repo.get_or_create_tag(project_id, tag_name)
            await self.test_case_repo.add_tag_to_test_case(test_case.id, tag)

    async def _tag_manual_execution_cases(
        self,
        project_id: UUID,
        manifest: dict[str, Any],
        batch_cases: list[TestCase],
    ) -> None:
        """rev64：给不可自动执行的用例打「需人工执行」标（幂等）。

        判据来自编译清单的 ``uncompiled_cases``（``web_case_compiler.compile_cases``）：
        - ``dangling_rule``：有规则编号但无可提取的规则体/断言；
        - ``narrative``：无可提取规则编号与断言。
        这两类用例**都是可执行的测试用例**（人工一定可执行），只是没有可自动
        判定的断言、执行时必然落 NOT_EXECUTED——提前打标让"无法自动执行 ≠
        用例错误，需人工干预"在用例列表即可识别，而不是等执行完看灰色状态再猜。

        落点（双通道，均无 DB 迁移）：
        - 平台标签「需人工执行」（用例列表标签列直接可见）；
        - ``custom_fields.manual_execution``（结构化：status/reason/tagged_at，
          供 API 与详情页消费）。
        """
        uncompiled = manifest.get("uncompiled_cases") or []
        if not uncompiled:
            return
        try:
            tag = await self.test_case_repo.get_or_create_tag(project_id, "需人工执行")
            tagged = 0
            by_identifier = {
                str(getattr(c, "identifier", "") or ""): c for c in batch_cases
            }
            for item in uncompiled:
                case = by_identifier.get(str(item.get("identifier") or ""))
                if case is None:
                    continue
                status = str(item.get("status") or "")
                reason = str(item.get("reason") or "")
                fields = dict(getattr(case, "custom_fields", None) or {})
                existing = fields.get("manual_execution")
                if (
                    isinstance(existing, dict)
                    and existing.get("status") == status
                    and existing.get("reason") == reason
                ):
                    continue  # 幂等：同状态同原因已打过标
                fields["manual_execution"] = {
                    "required": True,
                    "status": status,
                    "reason": reason or "无可执行断言，需人工执行",
                    "tagged_at": datetime.now().isoformat(timespec="seconds"),
                }
                case.custom_fields = fields
                await self.test_case_repo.add_tag_to_test_case(case.id, tag)
                tagged += 1
            if tagged:
                logger.info(
                    "[web_case_link] 人工执行打标完成: %d/%d 条不可自动执行用例",
                    tagged, len(uncompiled),
                )
        except Exception as exc:  # noqa: BLE001
            # 打标属增强项：失败只告警，不阻断编译/资产/运行创建
            logger.warning("[web_case_link] 人工执行打标失败（忽略）: %s", exc)

    async def _append_to_test_run(
        self,
        project: Project,
        test_run_identifier: str,
        test_case: TestCase,
        web_test: WebTest,
        execution_config: dict[str, Any],
        batch_cases: list | None = None,
    ) -> dict[str, Any]:
        result = await self.session.execute(
            select(TestRun)
            .where(TestRun.project_id == project.id)
            .where(TestRun.identifier == test_run_identifier)
        )
        test_run = result.scalar_one_or_none()
        if not test_run:
            raise ValueError(f"测试运行不存在: {test_run_identifier}")

        # rev91：payload 全量用例都建 run_case 链接（此前只建锚点——断言被编译
        # 跳过的用例（如无定位器字段）永远进不了运行用例列表，总用例数少报，
        # 实测 TR-123 payload 14 条只显示 13）。
        for c in (batch_cases or [test_case]):
            await self._ensure_run_case(test_run, c.id)
        job = await self._ensure_web_job(
            test_run=test_run,
            web_test=web_test,
            execution_order=await self._next_execution_order(test_run.id),
            execution_config=execution_config,
        )
        await self.session.flush()
        return self._test_run_info(test_run, [job])

    async def _create_test_run(
        self,
        project: Project,
        test_case: TestCase,
        web_test: WebTest,
        execution_config: dict[str, Any],
        batch_cases: list | None = None,
    ) -> dict[str, Any]:
        test_run = TestRun(
            project_id=project.id,
            identifier=await self.test_run_repo.generate_identifier(project.id),
            name=f"Web 最小闭环验证 {datetime.now().strftime('%Y%m%d-%H%M%S')}",
            description="由 Web 最小闭环接口自动创建",
            run_state=TestRunState.NEW_RUN,
            test_cases_count=0,
            execution_mode=ExecutionMode.SEQUENTIAL,
            max_concurrency=1,
        )
        self.session.add(test_run)
        await self.session.flush()
        # rev91：payload 全量用例都建 run_case 链接（同 _append_to_test_run 的理由）
        for c in (batch_cases or [test_case]):
            await self._ensure_run_case(test_run, c.id)
        job = await self._ensure_web_job(
            test_run=test_run,
            web_test=web_test,
            execution_order=1,
            execution_config=execution_config,
        )
        await self.session.flush()
        return self._test_run_info(test_run, [job])

    async def _ensure_run_case(self, test_run: TestRun, test_case_id: UUID) -> None:
        result = await self.session.execute(
            select(TestRunTestCase)
            .where(TestRunTestCase.test_run_id == test_run.id)
            .where(TestRunTestCase.test_case_id == test_case_id)
        )
        if result.scalar_one_or_none():
            return
        self.session.add(TestRunTestCase(
            test_run_id=test_run.id,
            test_case_id=test_case_id,
            latest_status=TestResultStatus.NOT_EXECUTED,
        ))
        test_run.test_cases_count = (test_run.test_cases_count or 0) + 1

    async def _ensure_web_job(
        self,
        test_run: TestRun,
        web_test: WebTest,
        execution_order: int,
        execution_config: dict[str, Any],
    ) -> TestRunScriptJob:
        result = await self.session.execute(
            select(TestRunScriptJob)
            .where(TestRunScriptJob.test_run_id == test_run.id)
            .where(TestRunScriptJob.script_type == ScriptType.WEB_TEST)
            .where(TestRunScriptJob.script_id == web_test.id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.execution_config = {
                **(existing.execution_config or {}),
                **(execution_config or {}),
            }
            return existing

        job = TestRunScriptJob(
            test_run_id=test_run.id,
            script_type=ScriptType.WEB_TEST,
            script_id=web_test.id,
            script_identifier=web_test.identifier,
            script_name=web_test.name,
            execution_order=execution_order,
            execution_mode=ExecutionMode.SEQUENTIAL,
            status=JobStatus.PENDING,
            max_retries=0,
            execution_config=execution_config,
        )
        self.session.add(job)
        return job

    async def _next_execution_order(self, test_run_id: UUID) -> int:
        result = await self.session.execute(
            select(TestRunScriptJob.execution_order)
            .where(TestRunScriptJob.test_run_id == test_run_id)
            .order_by(TestRunScriptJob.execution_order.desc())
        )
        current = result.scalars().first()
        return (current or 0) + 1

    def _test_run_info(
        self,
        test_run: TestRun,
        jobs: list[TestRunScriptJob],
    ) -> dict[str, Any]:
        return {
            "id": str(test_run.id),
            "identifier": test_run.identifier,
            "script_jobs": [
                {
                    "id": str(job.id),
                    "script_type": job.script_type.value,
                    "script_id": str(job.script_id),
                    "script_identifier": job.script_identifier,
                }
                for job in jobs
            ],
        }

    @staticmethod
    def _stamp_compiler_rev(script_content: str) -> str:
        """在 spec 头部写入编译器版本指纹（P0-2，2026-10-19）。

        为什么必须在**文件里**留痕：``web_tests.generation_params`` 只说明「这条记录是哪版生成的」，
        而执行器读的是磁盘/对象存储里的**脚本本体**——两者可能分叉（手工替换、历史产物回填、
        重新生成失败但记录已更新）。以脚本本体为唯一事实来源，执行前才判得准。

        幂等：已有指纹（含被手工复制粘贴的）不重复写。
        """
        head = script_content[:4096]
        if "COMPILER_REV:" in head:
            return script_content
        rev_literal = json.dumps(COMPILER_REV, ensure_ascii=False)
        digest = compiler_source_digest()
        digest_literal = json.dumps(digest, ensure_ascii=False)
        header = (
            "// ===== 产物指纹（P0-2 / rev118）=====\n"
            f"// COMPILER_REV: {COMPILER_REV}\n"
            f"// COMPILER_DIGEST: {digest}\n"
            "// 由平台编译器写入；执行前与当前编译器比对，不一致按「旧产物」处理。\n"
            "// DIGEST 覆盖执行器模板与编译逻辑源码 —— 改了逻辑却忘记 bump rev 时，靠它兜底。\n"
            f"const ARTIFACT_COMPILER_REV = {rev_literal};\n"
            f"const ARTIFACT_COMPILER_DIGEST = {digest_literal};\n"
            "console.log(`COMPILER_REV ${JSON.stringify({ rev: ARTIFACT_COMPILER_REV, digest: ARTIFACT_COMPILER_DIGEST })}`);\n"
            "\n"
        )
        return header + script_content

    def _generate_playwright_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        *,
        manifest: dict[str, Any] | None = None,
        form_plan: dict[str, Any] | None = None,
        upload_allow_hosts: list[str] | None = None,
        upload_allow_rules: list[dict[str, Any]] | None = None,
        project_identifier: str | None = None,
        web_template_key: str | None = None,
    ) -> str:
        manifest_block = (
            render_manifest_block(manifest)
            if manifest and manifest.get("case_count")
            else ""
        )
        coverage_snippet = (
            render_coverage_snippet(
                form_executable_checks=(form_plan or {}).get("check_count", 0),
                assertion_checks=(form_plan or {}).get("assertion_check_count", 0),
            )
            if manifest_block else ""
        )
        form_executor = (
            render_form_executor(form_plan, upload_allow_hosts=upload_allow_hosts,
                                 upload_allow_rules=upload_allow_rules)
            # executable=False（有跳过、无检查、无场景）时不注入执行器：为 0 条检查打开表单只会
            # 把环境问题变成硬失败；跳过原因由资产审计字段与运行期 [plan-skip] 承载。
            # rev98b：「只有规则级负路径场景、没有字段级检查」同样注入——场景是真实可执行
            # 内容（build_form_plan 对这种形态 executable=True），不注入场景就永远跑不到
            # （实测 CS email 弹窗：checks=0 + 1 场景，旧闸会产出无执行器的骨架）。
            if form_plan and (form_plan.get("checks") or form_plan.get("negative_scenarios")) else ""
        )
        # 列头只读比对（N3 第 2 步）：列契约来自编译期 `parse_columns`，此处只序列化注入，
        # **不**据此产生任何检查/断言（是否判失败由契约里的 assert_on_mismatch 决定，默认关）。
        columns_json = json.dumps((form_plan or {}).get("columns"), ensure_ascii=False)
        # 模板必须与用例业务**正向匹配**：无匹配时 `_resolve_web_template_key` 抛
        # [unsupported_web_template]，绝不退回其它业务模板
        # （早期实现默认套「储值免单」，实测 PR-9 的 CS Module 批次因此产出内容错误的脚本）。
        template_key = web_template_key or self._resolve_web_template_key(test_case)
        if template_key == "cs_module":
            return self._generate_cs_module_script(
                test_case,
                base_url,
                navigation_path,
                manifest_block=manifest_block,
                coverage_snippet=coverage_snippet,
                project_identifier=project_identifier,
                form_executor=form_executor,
                columns_json=columns_json,
            )
        if template_key == "enterprise_storage_recharge":
            return self._generate_enterprise_storage_recharge_script(
                test_case,
                base_url,
                navigation_path,
                manifest_block=manifest_block,
                coverage_snippet=coverage_snippet,
                form_executor=form_executor,
            )
        if template_key == "purchase_limit":
            return self._generate_purchase_limit_script(
                test_case,
                base_url,
                navigation_path,
                manifest_block=manifest_block,
                coverage_snippet=coverage_snippet,
                form_executor=form_executor,
            )
        if template_key == "survey_operation":
            return self._generate_survey_operation_script(
                test_case,
                base_url,
                navigation_path,
                manifest_block=manifest_block,
                coverage_snippet=coverage_snippet,
                form_executor=form_executor,
            )
        if template_key == "storage_writeoff":
            return self._generate_storage_writeoff_script(
                test_case,
                base_url,
                navigation_path,
                manifest_block=manifest_block,
                coverage_snippet=coverage_snippet,
                form_executor=form_executor,
            )
        if template_key == "yl_masterdata":
            # 2026-10-15 提测部署后落地：探针（probe_yl_store_v3/v4）已校准真实菜单
            # （英文 Master Data Management → Shop Management）、列表路由与整页表单
            # 结构，包装器按 cs_module 同构实现（登录/OCR/iframe 链路复用）。
            return self._generate_yl_masterdata_script(
                test_case,
                base_url,
                navigation_path,
                manifest_block=manifest_block,
                coverage_snippet=coverage_snippet,
                form_executor=form_executor,
                columns_json=columns_json,
            )
        raise ValueError(
            f"[unsupported_web_template] 未知的 Web 脚本模板键: {template_key}"
        )

    def _generate_survey_operation_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        *,
        manifest_block: str = "",
        coverage_snippet: str = "",
        form_executor: str = "",
    ) -> str:
        """Generate a test for 营销云-系统-调查问卷管理.

        与 purchase_limit 模板同构，差异仅在：
        - 门户卡片是「数盈·营销云」（点击后新开标签页，无 iframe，域名
          imarketing-uat.xysjg.com）；
        - 列表断言取问卷列表 8 个展示列 +「新建」入口（2026-10-20 探针实测表头）；
        - 已知页面行为：左侧子菜单面板展开时会遮挡内容区（pointer-events 被
          sider 拦截）⇒ 进入列表后先把鼠标移到内容区空白处等待菜单收起，
          再执行任何按钮定位点击；
        - 新建表单为整页路由（#/system/survey/surveyOperation/add），表单字段
          检查/真实落库由 build_form_plan 编译清单 + negative_scenarios 驱动
          （保存接口 POST /api/icdp/v1/evaluate，resultCode=0 = 受理）。
        """
        path_steps = json.dumps(navigation_path, ensure_ascii=False, indent=2)

        def _normalize_nav_step(step: Any) -> str:
            text = str(step).strip()
            text = re.sub(r"^(点击|进入|打开|选择)\s*", "", text)
            text = re.sub(r"(列表页|页面|菜单|按钮|入口)$", "", text).strip()
            return text

        nav_steps = [
            _normalize_nav_step(step) for step in (navigation_path or [])
            if not re.search(r"门户|工作台|登录|卡片|进入|交易云|营销云|微商城", str(step))
        ]
        nav_steps = [s for s in nav_steps if s]
        nav_steps_js = json.dumps(nav_steps, ensure_ascii=False, indent=2)
        click_by_text_js = render_click_by_text(
            menu_selectors="el_menu", text_fallback="last", dom_click_fallback=True
        )
        required_click_js = render_required_click()
        return f"""import {{ test, expect, type BrowserContext, type FrameLocator, type Page }} from '@playwright/test';

const LOGIN_URL = process.env.WEB_TEST_BASE_URL || {json.dumps(base_url, ensure_ascii=False)};
const USERNAME = process.env.WEB_TEST_USERNAME;
const PASSWORD = process.env.WEB_TEST_PASSWORD;
const WEB_VALIDATION_PATH = {path_steps};
const NAV_STEPS = {nav_steps_js};
{manifest_block}
type ClickScope = Page | FrameLocator;

{click_by_text_js}

{required_click_js}

async function waitForPortal(page: Page) {{
  await page.waitForFunction(
    () => /门户中心|数盈[·\\s]*营销云|Marketing cloud/.test(document.body.innerText),
    null,
    {{ timeout: 45000 }},
  ).catch(() => undefined);
}}

//: 营销云：门户卡片 → 新开标签页（域名 imarketing-uat.xysjg.com，无 iframe）。
async function openMarketingCloud(page: Page, context: BrowserContext) {{
  const newPagePromise = context.waitForEvent('page', {{ timeout: 15000 }}).catch(() => null);
  let clicked = false;
  for (let attempt = 0; attempt < 3 && !clicked; attempt += 1) {{
    clicked = await clickByText(page, ['数盈·营销云', '数盈营销云', '营销云', 'Marketing cloud']);
    if (!clicked) {{
      await page.waitForTimeout(2000);
    }}
  }}
  expect(clicked, '未找到或无法点击：数盈营销云卡片（已重试 3 次）').toBeTruthy();
  let appPage = await newPagePromise;
  if (!appPage) {{
    // 定位器点击未带出弹窗时：降级为真实鼠标坐标点击卡片中心（与人工操作一致）。
    const box = await page.evaluate(() => {{
      const cols = Array.from(document.querySelectorAll('.ant-col, [class*=flexCenter], .ant-card'));
      const t = cols.find(e => /营销云/.test((e as HTMLElement).innerText || ''));
      if (!t) return null;
      const r = (t as HTMLElement).getBoundingClientRect();
      return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
    }});
    if (box) {{
      const pop2 = context.waitForEvent('page', {{ timeout: 12000 }}).catch(() => null);
      await page.mouse.click(box.x, box.y);
      appPage = (await pop2) || null;
    }}
  }}
  appPage = appPage || page;
  await appPage.waitForLoadState('domcontentloaded', {{ timeout: 30000 }}).catch(() => undefined);
  await appPage.waitForTimeout(2500);
  console.log('[nav] appPage.url =', appPage.url());
  // 外壳判定（营销云首页：顶部菜单 用户/忠诚度/营销/活动/数据/系统）。
  const shellReady = await appPage.waitForFunction(
    () => /忠诚度|广告投放|装修管理|调查问卷/.test(document.body.innerText),
    null,
    {{ timeout: 30000 }},
  ).then(() => true).catch(() => false);
  console.log('[nav] shellReady =', shellReady);
  expect(shellReady, '营销云应用外壳未渲染（卡片点击后未进入营销云，弹窗可能被拦截）').toBeTruthy();
  return appPage;
}}

async function getAppScope(page: Page): Promise<ClickScope> {{
  const iframe = page.locator('iframe#systemIframe').first();
  if (await iframe.count()) {{
    await iframe.waitFor({{ state: 'attached', timeout: 30000 }}).catch(() => undefined);
    return page.frameLocator('#systemIframe').first();
  }}
  return page;
}}

async function loginToConsole(page: Page) {{
  // 与限购/储值模板同一登录实现：显式判定登录完成 + 有界重试。
  for (let attempt = 0; attempt < 3; attempt += 1) {{
    const user = page.locator('input[placeholder*="用户名"], input#login_username').first();
    const pass = page.locator('input[type="password"], input#login_password').first();
    await user.waitFor({{ state: 'visible', timeout: 20000 }});
    await user.fill(USERNAME);
    await pass.fill(PASSWORD);
    if ((await pass.inputValue()) !== PASSWORD) {{
      await page.waitForTimeout(500);
      continue;
    }}
    await page.locator('button:has-text("登 录"), button[type="submit"]').first().click();
    try {{
      await page.waitForFunction(
        () => !document.querySelector('input[type="password"], input#login_password'),
        null,
        {{ timeout: 20000 }},
      );
      return true;
    }} catch {{
      await page.waitForTimeout(1000);
    }}
  }}
  return false;
}}

//: 进入「调查问卷管理」列表页并返回应用作用域。
async function enterSurveyList(pg: Page, ctx: BrowserContext) {{
  const loginResponse = await pg.goto(LOGIN_URL, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  const loginStatus = loginResponse ? loginResponse.status() : 0;
  if (loginStatus === 403 || loginStatus === 503 || loginStatus >= 500) {{
    throw new Error(
      `ENVIRONMENT_BLOCKED ${{JSON.stringify({{ reason: 'login_http_status', status: loginStatus }})}} `
      + `登录页返回 HTTP ${{loginStatus}}（疑似 WAF/网关拦截，非产品缺陷）`,
    );
  }}
  expect(await loginToConsole(pg), '登录未完成（3 次尝试后仍停留在登录页：账号密码未通过或被页面清空）').toBeTruthy();
  await waitForPortal(pg);
  const appPage = await openMarketingCloud(pg, ctx);
  const scope = await getAppScope(appPage);
  // 菜单导航完全由路径模板驱动（如：系统 → 调查问卷管理）。
  await appPage.waitForTimeout(2000);
  for (const step of NAV_STEPS) {{
    try {{
      await requiredClick(scope, step, [step], 45000);
    }} catch (e) {{
      const dbg = await appPage.evaluate(() => {{
        const iframes = Array.from(document.querySelectorAll('iframe')).map(f => f.src || 'about:blank');
        return {{
          body: document.body.innerText.slice(0, 300),
          iframes: iframes.slice(0, 5),
          readyState: document.readyState,
        }};
      }}).catch((err) => String(err));
      console.log('[nav] FAILED step=' + step + ' appPage.url=' + appPage.url() + ' dbg=' + JSON.stringify(dbg));
      throw e;
    }}
    await appPage.waitForTimeout(1500);
  }}
  // 已知页面行为：左侧子菜单面板展开时会遮挡内容区（实测「新建」按钮的
  // pointer-events 被 aside 拦截）。把鼠标移到内容区空白处等菜单收起。
  await appPage.mouse.move(900, 500);
  await appPage.waitForTimeout(800);
  return scope;
}}

test('调查问卷管理入口与列表冒烟 - {test_case.identifier}', async ({{ page, context }}) => {{
  test.setTimeout(Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS || 0) || 240000));
  console.log(`TEST_BUDGET ${{JSON.stringify({{ ms: Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS || 0) || 240000), injected: !!process.env.WEB_TEST_BUDGET_MS, cap: 240000 }})}}`);
  test.skip(!USERNAME || !PASSWORD, '缺少 WEB_TEST_USERNAME/WEB_TEST_PASSWORD，跳过外部 B端 Web 冒烟执行');

  const appScope = await enterSurveyList(page, context);

  // 问卷列表 8 个展示列 + 新建入口（2026-10-20 探针实测表头）
  await expect(appScope.getByText('问卷编码', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('问卷标题', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('业务类型', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('适用门店', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('适用商品', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('优先级', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('状态', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('参与次数', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByRole('button', {{ name: /新\\s*建/ }}).first()).toBeVisible({{ timeout: 15000 }});

  console.log('WEB_VALIDATION_PATH =', JSON.stringify(WEB_VALIDATION_PATH));
{form_executor}{coverage_snippet}}});
"""

    def _generate_storage_writeoff_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        *,
        manifest_block: str = "",
        coverage_snippet: str = "",
        form_executor: str = "",
    ) -> str:
        """「储值免单活动」业务模板（仅该业务可用，不再作为通用兜底）。"""
        path_steps = json.dumps(navigation_path, ensure_ascii=False, indent=2)
        # P0-2 阶段 A：三件套改由共享骨架渲染。
        # 说明（有意的能力统一）：原实现对 clickByText 是**弱版**——无 timeoutMs、无重试、
        # 无 waitFor 可见性等待、无 DOM 直点兜底，UAT 渲染慢时会直接报"未找到或无法点击"。
        # 这里升级为与其它模板一致的标准版（重试 + 可见性等待 + DOM 兜底），
        # text_fallback 仍保持该模板原有的 first()（不改定位优先级）。
        click_by_text_js = render_click_by_text(
            menu_selectors="generic", text_fallback="first", dom_click_fallback=True
        )
        required_click_js = render_required_click()
        return f"""import {{ test, expect, type BrowserContext, type FrameLocator, type Page }} from '@playwright/test';

const LOGIN_URL = process.env.WEB_TEST_BASE_URL || {json.dumps(base_url, ensure_ascii=False)};
const USERNAME = process.env.WEB_TEST_USERNAME;
const PASSWORD = process.env.WEB_TEST_PASSWORD;
const WEB_VALIDATION_PATH = {path_steps};
{manifest_block}
type ClickScope = Page | FrameLocator;

{click_by_text_js}

{required_click_js}

async function clickClickableAncestorByText(page: Page, candidates: string[]) {{
  return await page.evaluate((texts) => {{
    const normalize = (value: string) => value.replace(/\\s+/g, '').trim();
    const isVisible = (element: Element) => {{
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }};
    const elements = Array.from(document.querySelectorAll('body *'))
      .filter(isVisible)
      .filter((element) => texts.some((text) => normalize(element.textContent || '').includes(normalize(text))))
      .sort((a, b) => (a.textContent || '').length - (b.textContent || '').length);

    for (const element of elements) {{
      let current: HTMLElement | null = element as HTMLElement;
      for (let depth = 0; current && depth < 6; depth += 1, current = current.parentElement) {{
        const style = window.getComputedStyle(current);
        if (style.cursor === 'pointer' || current.tagName === 'BUTTON' || current.tagName === 'A') {{
          current.click();
          return true;
        }}
      }}
      (element as HTMLElement).click();
      return true;
    }}
    return false;
  }}, candidates);
}}

async function openMarketingCloud(page: Page, context: BrowserContext) {{
  const marketingPagePromise = context.waitForEvent('page', {{ timeout: 15000 }}).catch(() => null);
  let clicked = false;
  for (let attempt = 0; attempt < 3 && !clicked; attempt += 1) {{
    clicked = await clickByText(page, ['数盈·营销云', '数盈营销云', '营销云', 'Marketing cloud']);
    if (!clicked) {{
      clicked = await clickClickableAncestorByText(page, ['数盈·营销云', '数盈营销云', '营销云', 'Marketing cloud']);
    }}
    if (!clicked) {{
      await page.waitForTimeout(2000);
    }}
  }}
  expect(clicked, '未找到或无法点击：数盈·营销云卡片（已重试 3 次）').toBeTruthy();

  const newPage = await marketingPagePromise;
  const appPage = newPage || page;
  await appPage.waitForLoadState('domcontentloaded', {{ timeout: 30000 }}).catch(() => undefined);
  await appPage.waitForLoadState('networkidle', {{ timeout: 30000 }}).catch(() => undefined);
  await appPage.waitForTimeout(3000);
  return appPage;
}}

async function getMarketingScope(page: Page): Promise<ClickScope> {{
  const iframe = page.locator('iframe#systemIframe').first();
  if (await iframe.count()) {{
    await iframe.waitFor({{ state: 'attached', timeout: 30000 }}).catch(() => undefined);
    return page.frameLocator('#systemIframe').first();
  }}
  return page;
}}

async function chooseActivityType(scope: ClickScope) {{
  if (await clickByText(scope, ['储值免单活动', '储值免单'])) {{
    return true;
  }}

  const triggerCandidates = [
    scope.getByLabel(/活动类型/).first(),
    scope.getByPlaceholder(/请选择.*活动类型|活动类型/).first(),
    scope.locator('.ant-select:has-text("活动类型"), .ant-select-selector').first(),
    scope.locator('xpath=//*[contains(normalize-space(.), "活动类型")]/following::*[contains(@class, "ant-select-selector")][1]').first(),
    scope.getByText('活动类型', {{ exact: false }}).locator('..').first(),
  ];
  for (const trigger of triggerCandidates) {{
    if (!(await trigger.count())) continue;
    try {{
      await trigger.click({{ timeout: 5000 }});
      if (await clickByText(scope, ['储值免单活动', '储值免单'])) {{
        return true;
      }}
    }} catch (error) {{
      // Try next trigger.
    }}
  }}
  return false;
}}

async function loginToConsole(page: Page) {{
  // 登录失败的典型表现是"页面停在登录页、密码框被清空"，随后在门户卡片处报
  // "未找到或无法点击"——真实原因与表象不符。这里显式判定登录是否完成，
  // 并给一次有界重试（重填 + 重新提交），失败时给出准确原因。
  // 环境阻断前置判定：UAT 被 WAF/网关拦截时返回 403/503，页面根本不渲染，
  // 若只看定位器超时会把它误报成产品缺陷（实测量测 2026-09-24）。
  const loginResponse = await page.goto(LOGIN_URL, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  const loginStatus = loginResponse ? loginResponse.status() : 0;
  if (loginStatus === 403 || loginStatus === 503 || loginStatus >= 500) {{
    throw new Error(
      `ENVIRONMENT_BLOCKED ${{JSON.stringify({{ reason: 'login_http_status', status: loginStatus }})}} `
      + `登录页返回 HTTP ${{loginStatus}}（疑似 WAF/网关拦截，非产品缺陷）`,
    );
  }}
  for (let attempt = 0; attempt < 3; attempt += 1) {{
    const user = page.locator('input[placeholder*="用户名"], input#login_username').first();
    const pass = page.locator('input[type="password"], input#login_password').first();
    await user.waitFor({{ state: 'visible', timeout: 20000 }});
    await user.fill(USERNAME);
    await pass.fill(PASSWORD);
    // 提交前回读：受控输入可能在渲染后被重置，填了空密码就白点一次
    if ((await pass.inputValue()) !== PASSWORD) {{
      await page.waitForTimeout(500);
      continue;
    }}
    await page.locator('button:has-text("登 录"), button[type="submit"]').first().click();
    try {{
      // 登录完成的判据：登录表单消失（不再有"请输入密码"输入框）
      await page.waitForFunction(
        () => !document.querySelector('input[type="password"], input#login_password'),
        null,
        {{ timeout: 20000 }},
      );
      return true;
    }} catch {{
      await page.waitForTimeout(1000);
    }}
  }}
  return false;
}}

test('B端储值免单活动新建入口冒烟 - {test_case.identifier}', async ({{ page, context }}) => {{
  // 必须放在最前面：默认 30s 的用例预算会在登录/跳转阶段就把测试掐断
  // （实测 page.goto 报 "Test timeout of 30000ms exceeded"）。
  // rev116：不得超执行器下发的进程预算（未注入时用本模板原定的 4 分钟），
  // 否则进程先被杀、Playwright 来不及产出报告。
  test.setTimeout(Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS || 0) || 240000));
  console.log(`TEST_BUDGET ${{JSON.stringify({{ ms: Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS || 0) || 240000), injected: !!process.env.WEB_TEST_BUDGET_MS, cap: 240000 }})}}`);
  test.skip(!USERNAME || !PASSWORD, '缺少 WEB_TEST_USERNAME/WEB_TEST_PASSWORD，跳过外部 B端 Web 冒烟执行');

  expect(await loginToConsole(page), '登录未完成（3 次尝试后仍停留在登录页：账号密码未通过或被页面清空）').toBeTruthy();
  await page.waitForLoadState('networkidle', {{ timeout: 30000 }}).catch(() => undefined);
  await page.waitForFunction(() => /数盈[·\\s]*营销云|Marketing cloud/.test(document.body.innerText), null, {{ timeout: 45000 }});

  const appPage = await openMarketingCloud(page, context);
  const appScope = await getMarketingScope(appPage);

  await requiredClick(appScope, '活动菜单', ['活动']);
  await requiredClick(appScope, '营销活动管理', ['营销活动管理', '营销活动']);
  await requiredClick(appScope, '创建', ['创建', '新建活动', '新增活动']);
  const selected = await chooseActivityType(appScope);
  expect(selected).toBeTruthy();

  await expect(appScope.getByText(/储值免单/).first()).toBeVisible({{ timeout: 15000 }});
{coverage_snippet}}});
"""

    @staticmethod
    def _case_route_text(test_case: TestCase) -> str:
        """用例的**归一化**业务识别文本（name + module + description）。

        见模块级 `normalize_route_text`：`module` 来自上传文件名 stem，
        文件名里的下划线/连字符会把含空格的关键词（如 `"CS Module"`）切断，
        故匹配前统一归一化。
        """
        return normalize_route_text(" ".join([
            test_case.name or "",
            test_case.module or "",
            test_case.description or "",
        ]))

    @staticmethod
    def _route_keyword_hit(text: str, keywords) -> bool:
        """关键词命中判定：**两侧都归一化 + 折叠大小写**后做包含匹配。

        折叠大小写是为了让英文菜单名关键词（Item Manage / Payment Method /
        Shop Management 等）不受用例与菜单的大小写差异影响；中文无大小写，
        该变换对其完全无影响。
        """
        return any(normalize_route_text(k).lower() in text.lower() for k in keywords)

    def _is_cs_module_case(self, test_case: TestCase) -> bool:
        """CS Module（客户服务管理系统，eatizen CRM Console）用例判定。

        关键词取自需求文档（CS Case/Task/Goodwill/RetroClaim/审批中心/消息中心），
        刻意不用裸 "CS"（导出模板样例标题 cs_cases_xxx 会误命中）。
        """
        keywords = [
            "工单", "CS Module", "CS模块", "Customer Service",
            "Goodwill", "GoodWill", "RetroClaim", "Retro Claim",
            "审批中心", "消息中心",
        ]
        return self._route_keyword_hit(self._case_route_text(test_case), keywords)

    def _is_yl_masterdata_case(self, test_case: TestCase) -> bool:
        """YL 主數據管理（店鋪/商品/支付方式主數據）用例判定。

        關鍵詞取自鎖定需求文檔標題與模塊名；刻意用「主數據管理/主数据管理」
        組合詞，避免裸「店鋪/商品」誤傷其它業務。
        """
        return self._route_keyword_hit(
            self._case_route_text(test_case),
            ["主數據管理", "主数据管理", "YL-主數據", "YL-主数据",
             "店鋪主數據", "店铺主数据", "商品主數據", "支付方式主數據"],
        )

    def _yl_masterdata_child_menu(self, test_case: TestCase) -> tuple[str, list[str]]:
        """按用例文本推断 YL 主数据的**子菜单**（店鋪/商品/支付方式）。

        返回 ``(业务标签, 子菜单候选集)``。菜单为**英文**，2026-10-15 用户口径确认：
        - 店鋪 = ``Shop Management``（UAT 探针实测）
        - 商品 = ``Item Manage``（用户确认；``Item Management`` 为 i18n 语言包旧译名）
        - 支付 = ``Payment Method``（用户确认；``Payment Methods`` 为 i18n 旧译名）

        候选集**首项为首选**，其后依次是英文旧译名与中文名——点击走 clickByText
        逐候选兜底，UAT 语言切换或版本改名时不至于整批断链。
        """
        text = self._case_route_text(test_case)
        if self._route_keyword_hit(
            text,
            ["商品主數據", "商品主数据", "Item Manage", "Item Management", "Goods Management"],
        ):
            return "商品主數據", [
                "Item Manage", "Item Management", "Goods Management",
                "商品主數據", "商品管理", "Goods",
            ]
        if self._route_keyword_hit(
            text,
            ["支付方式主數據", "支付方式主数据", "Payment Method", "Payment Methods", "Payment"],
        ):
            return "支付方式主數據", [
                "Payment Method", "Payment Methods", "支付方式主數據", "支付方式管理", "Payment",
            ]
        return "店鋪主數據", ["Shop Management", "店鋪主數據", "店鋪管理", "Shop"]

    def _generate_yl_masterdata_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        *,
        manifest_block: str = "",
        coverage_snippet: str = "",
        form_executor: str = "",
        columns_json: str = "null",
    ) -> str:
        """YL 主數據管理（店鋪/商品/支付方式主數據）业务模板。

        校准依据（2026-10-15 部署日探针 probe_yl_store_v3/v4，UAT 已提测）：
        - 登录/验证码 OCR/CRM iframe 链路与 CS 模板完全同构（同一企业控制台），
          故以 cs_module 模板为底，只改菜单导航与业务文案；
        - 菜单：CRM 左侧 **Master Data Management**（英文）→ 子菜单按用例业务推断
          （店鋪=Shop Management）；
        - 列表页路由 ``#/mainData/shopManage``；「Add Store」为**同页签**跳转到
          ``#/mainData/shopManage/addStores?mode=add`` 的整页表单（非弹窗），
          表单容器选择器由子功能 custom_config.field_locators.modal 承载
          （实测 ``.ant-form``）；
        - 表单标签为**英文**（Store Name (CN)…），字段定位以 custom_config 里
          探针实测的 selector_hint 为准；中文 has-text 兜底不再适用（页面无中文标签）。
        """
        path_steps = json.dumps(navigation_path, ensure_ascii=False, indent=2)
        ocr_url = os.environ.get("WEB_TEST_CAPTCHA_OCR_URL") or (
            f"http://localhost:8000/api/v2/projects/PR-9/web-tests/captcha-ocr"
        )
        biz_label, child_labels = self._yl_masterdata_child_menu(test_case)
        child_labels_json = json.dumps(child_labels, ensure_ascii=False)
        click_by_text_js = render_click_by_text(
            menu_selectors="generic", text_fallback="first", dom_click_fallback=True
        )
        required_click_js = render_required_click()
        frame_diag_js = render_frame_diag()
        column_diag_js = render_column_diag(columns_json)
        return f"""import {{ test, expect, type BrowserContext, type FrameLocator, type Locator, type Page }} from '@playwright/test';

const LOGIN_URL = process.env.WEB_TEST_BASE_URL || {json.dumps(base_url, ensure_ascii=False)};
const USERNAME = process.env.WEB_TEST_USERNAME;
const PASSWORD = process.env.WEB_TEST_PASSWORD;
// eatizen UAT 控制台为验证码登录：回调平台验证码 OCR 端点识别
const CAPTCHA_OCR_URL = process.env.WEB_TEST_CAPTCHA_OCR_URL || {json.dumps(ocr_url)};
const WEB_VALIDATION_PATH = {path_steps};
{manifest_block}
type ClickScope = Page | FrameLocator;

{click_by_text_js}

{required_click_js}

{frame_diag_js}

{column_diag_js}

// ---- 菜单定位（与 cs_module 模板同一套，rev64 语义）------------------------
// antd SubMenu 是开关语义：先读 aria-expanded，已展开绝不重复点击。
async function visibleMenuTexts(scope: ClickScope): Promise<string[]> {{
  const items = scope.locator('.ant-menu-item, .ant-menu-submenu-title, [role="menuitem"]');
  const count = Math.min(await items.count().catch(() => 0), 80);
  const texts: string[] = [];
  for (let i = 0; i < count; i += 1) {{
    const text = (await items.nth(i).innerText().catch(() => '')).replace(/\\s+/g, ' ').trim();
    if (text) texts.push(text);
  }}
  return Array.from(new Set(texts));
}}

async function submenuTitle(scope: ClickScope, label: string): Promise<Locator | null> {{
  const candidates = [
    scope.locator('.ant-menu-submenu-title', {{ hasText: new RegExp(label) }}).first(),
    scope.getByRole('menuitem', {{ name: new RegExp(label) }}).first(),
  ];
  for (const locator of candidates) {{
    if (await locator.count().catch(() => 0)) return locator;
  }}
  return null;
}}

async function findMenuItem(scope: ClickScope, labels: string[]): Promise<Locator | null> {{
  const targets: ClickScope[] = [];
  if (await scope.locator('.ant-menu-submenu-popup').first().count().catch(() => 0)) {{
    targets.push(scope.locator('.ant-menu-submenu-popup').first() as unknown as ClickScope);
  }}
  targets.push(scope);
  for (const target of targets) {{
    for (const label of labels) {{
      const candidates = [
        target.locator('.ant-menu-item', {{ hasText: new RegExp(label) }}).first(),
        target.getByRole('menuitem', {{ name: new RegExp(label) }}).first(),
        target.getByText(new RegExp(label), {{ exact: false }}).first(),
      ];
      for (const locator of candidates) {{
        if (!(await locator.count().catch(() => 0))) continue;
        try {{
          await locator.waitFor({{ state: 'visible', timeout: 1500 }});
          return locator;
        }} catch {{
          // 换下一个候选定位器
        }}
      }}
    }}
  }}
  return null;
}}

async function expandSubmenu(
  scope: ClickScope,
  labels: string[],
  childLabels: string[],
  timeoutMs = 20000,
): Promise<boolean> {{
  const deadline = Date.now() + timeoutMs;
  let clickedOnce = false;
  while (Date.now() < deadline) {{
    for (const label of labels) {{
      const title = await submenuTitle(scope, label);
      if (!title) continue;
      try {{
        await title.waitFor({{ state: 'visible', timeout: 2500 }});
      }} catch {{
        continue;
      }}
      if ((await title.getAttribute('aria-expanded').catch(() => null)) === 'true') return true;
      if (await findMenuItem(scope, childLabels)) return true;
      if (clickedOnce) {{
        await new Promise((resolve) => setTimeout(resolve, 500));
        continue;
      }}
      clickedOnce = true;
      try {{
        await title.click({{ timeout: 5000 }});
      }} catch {{
        await title.hover({{ timeout: 5000 }}).catch(() => undefined);
      }}
      await new Promise((resolve) => setTimeout(resolve, 1200));
      if ((await title.getAttribute('aria-expanded').catch(() => null)) === 'true') return true;
      if (await scope.locator('.ant-menu-submenu-popup').first().isVisible().catch(() => false)) return true;
      if (await findMenuItem(scope, childLabels)) return true;
    }}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }}
  return false;
}}

async function clickMenuItem(scope: ClickScope, labels: string[], timeoutMs = 15000): Promise<boolean> {{
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {{
    const locator = await findMenuItem(scope, labels);
    if (locator) {{
      try {{
        await locator.click({{ timeout: 5000 }});
        return true;
      }} catch {{
        try {{
          await locator.hover({{ timeout: 3000 }});
          await locator.click({{ timeout: 5000, force: true }});
          return true;
        }} catch {{
          // 继续等下一轮
        }}
      }}
    }}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }}
  return false;
}}

async function ocrCaptcha(page: Page): Promise<string | null> {{
  const img = page.locator('img[src^="data:image"]').first();
  if (!(await img.count())) return null;
  try {{
    const shot = await img.screenshot({{ timeout: 5000 }});
    const resp = await page.request.post(CAPTCHA_OCR_URL, {{
      data: {{ image_base64: shot.toString('base64') }},
      timeout: 20000,
    }});
    if (!resp.ok()) return null;
    const body = await resp.json();
    const text = ((body && body.data && body.data.text) || '').trim();
    // 2026-10-16 实测（UAT eatizen 控制台）：图形验证码长度在 2~4 之间浮动
    // （"WX"=2 / "YBI"=3 / "LWTE"=4）。原 `>= 4` 门槛会让 2~3 字符码被**永久拒绝**：
    // 10 轮重试全部在 `if (!code) continue` 退出（3 秒内跑完），最终报"验证码 OCR 重试 10 次
    // 后仍未离开登录页"，而真实原因与文案不符。放宽到 >= 2 —— OCR 误读的代价只是失败后
    // 重试一轮（配合 loginToConsole 的整页 reload 换新图，不会退化成同图重复提交）。
    return text.length >= 2 ? text : null;
  }} catch {{
    return null;
  }}
}}

async function loginToConsole(page: Page) {{
  // eatizen UAT：用户名/密码/图形验证码。OCR 可能误读 → 整页重试 10 次，
  // 每次重新进入登录页刷新验证码；登录成功判据=离开 /login。
  for (let attempt = 0; attempt < 10; attempt += 1) {{
    // 环境阻断前置判定：WAF/网关拦截时登录页返回 403/503，页面根本不渲染。
    const loginResponse = await page.goto(LOGIN_URL, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
    const loginStatus = loginResponse ? loginResponse.status() : 0;
    if (loginStatus === 403 || loginStatus === 503 || loginStatus >= 500) {{
      throw new Error(
        `ENVIRONMENT_BLOCKED ${{JSON.stringify({{ reason: 'login_http_status', status: loginStatus }})}} `
        + `登录页返回 HTTP ${{loginStatus}}（疑似 WAF/网关拦截，非产品缺陷）`,
      );
    }}
    if (attempt > 0) {{
      // 2026-10-16 实测教训（TR-378 实锤）：`page.goto` 到同 hash 路由（…/#/login）是
      // **同文档导航**、不触发整页重载；而失败提交后 SPA 也不刷新验证码
      // ⇒ 不强制 reload 就会 10 轮都对着**同一张缓存图**重复同一个 OCR 误读，
      // 死循环到底（表现为"重试 10 次仍未离开登录页"）。
      await page.reload({{ waitUntil: 'domcontentloaded', timeout: 60000 }}).catch(() => undefined);
      await page.waitForTimeout(800);
    }}
    const user = page.locator('#login_username');
    const pass = page.locator('#login_password');
    await user.waitFor({{ state: 'visible', timeout: 30000 }});
    await user.fill(USERNAME);
    await pass.fill(PASSWORD);
    const code = await ocrCaptcha(page);
    if (!code) continue;
    await page.locator('#login_validateCode').fill(code);
    await page.locator('button:has-text("Login Immediately"), button.ant-btn-primary').first().click();
    await page.waitForTimeout(6000);
    if (!page.url().includes('/login')) return true;
  }}
  return false;
}}

async function openCrm(page: Page, context: BrowserContext) {{
  // 登录后控制台顶栏有「CRM」入口；可能当前页内嵌，也可能新开页签。
  const crmPagePromise = context.waitForEvent('page', {{ timeout: 20000 }}).catch(() => null);
  let clicked = false;
  for (let attempt = 0; attempt < 3 && !clicked; attempt += 1) {{
    clicked = await clickByText(page, ['CRM']);
    if (!clicked) await page.waitForTimeout(2000);
  }}
  expect(clicked, '未找到或无法点击：控制台顶部 CRM 入口（已重试 3 次）').toBeTruthy();
  const newPage = await crmPagePromise;
  const appPage = newPage || page;
  await appPage.waitForLoadState('domcontentloaded', {{ timeout: 60000 }}).catch(() => undefined);
  // 嵌入式 CRM 首屏加载较慢（实测约 15s）：等待左侧菜单或 iframe 出现
  await appPage.waitForFunction(
    () => /Master Data Management|Customer Service|Member Center/.test(document.body.innerText) ||
      document.querySelectorAll('iframe').length > 0,
    null,
    {{ timeout: 60000 }},
  ).catch(() => undefined);
  await appPage.waitForTimeout(3000);
  return appPage;
}}

async function getCrmScope(appPage: Page): Promise<ClickScope> {{
  // N5（2026-10-18）：取帧**决策必须留痕**。历史实现只取第一个 iframe，
  // 失败时日志里连"取了哪个 frame、页面上有几个 iframe"都没有，无法判断是取错帧。
  // 这里只加诊断，**不改变选择逻辑**（是否改成多帧遍历，等诊断数据出来再定）。
  const iframes = appPage.locator('iframe');
  const total = await iframes.count().catch(() => 0);
  if (total > 0) {{
    await iframes.first().waitFor({{ state: 'attached', timeout: 30000 }}).catch(() => undefined);
    const src = String((await iframes.first().getAttribute('src').catch(() => '')) || '');
    console.log(`SCOPE_PICK ${{JSON.stringify({{ chosen: 'iframe#0', iframes: total, src: src.slice(0, 120), pageUrl: String(appPage.url()).slice(0, 120) }})}}`);
    return appPage.frameLocator('iframe').first();
  }}
  console.log(`SCOPE_PICK ${{JSON.stringify({{ chosen: 'page', iframes: 0, pageUrl: String(appPage.url()).slice(0, 120) }})}}`);
  return appPage;
}}

test('YL{biz_label}主數據管理入口冒烟 - {test_case.identifier}', async ({{ page, context }}) => {{
  // 默认 30s 用例预算会在登录/跳转阶段被掐断，显式放宽（同 cs_module 口径）。
  // rev116：预算改由执行器下发（WEB_TEST_BUDGET_MS = runner 进程超时 - 30s），
  // 保证「Playwright 自己先超时、能产出报告」，而不是被执行器先杀进程。
  // 未注入时回退 30 分钟（本地直跑脚本的场景）。
  test.setTimeout(Number(process.env.WEB_TEST_BUDGET_MS || 0) || 1800000);
  // 运行期自证：本次预算到底是多少、是执行器下发的还是回退默认值。
  console.log(`TEST_BUDGET ${{JSON.stringify({{ ms: Number(process.env.WEB_TEST_BUDGET_MS || 0) || 1800000, injected: !!process.env.WEB_TEST_BUDGET_MS }})}}`);
  test.skip(!USERNAME || !PASSWORD, '缺少 WEB_TEST_USERNAME/WEB_TEST_PASSWORD，跳过外部 Web 冒烟执行');

  expect(await loginToConsole(page), '企业控制台登录失败（验证码 OCR 重试 10 次后仍未离开登录页）').toBeTruthy();

  const appPage = await openCrm(page, context);
  let scope = await getCrmScope(appPage);
  // YL 主数据菜单（UAT 实测为英文：Master Data Management）
  const parentLabels = ['Master Data Management', '主數據管理', '主数据管理'];
  const childLabels = {child_labels_json};
  let expanded = await expandSubmenu(scope, parentLabels, childLabels, 20000);
  if (!expanded) {{
    // rev116：入口取帧时 iframe 可能尚未插入（iframes=0 → 选了 page），失败后重取一次。
    // rev116-fix：**不要把帧数另读一遍再传进来** —— 两次读 DOM 之间的竞态会让
    // 「scope=page 但帧数=1」，重取就被判成"没变化"而直接放弃（实测失效）。
    // 传 scope 本身，由 helper 反推是否该等、是否该换。
    const refreshed = await refreshCrmScope(appPage, scope, 'yl-menu-expand');
    if (refreshed.stale) {{
      scope = refreshed.scope;
      expanded = await expandSubmenu(scope, parentLabels, childLabels, 20000);
    }}
  }}
  if (!expanded) {{
    scope = appPage;
    expanded = await expandSubmenu(scope, parentLabels, childLabels, 20000);
  }}
  if (!expanded) {{
    const dump = JSON.stringify(await visibleMenuTexts(scope));
    console.log(`YL_MENU_DUMP ${{dump}}`);
    console.log(`FRAME_DIAG ${{await frameDiag(appPage, 'yl-menu-expand')}}`);
    expect(expanded, `未找到或未能展开 Master Data Management 菜单；当前可见菜单=${{dump}}`).toBeTruthy();
  }}
  await appPage.waitForTimeout(1500);
  let childClicked = await clickMenuItem(scope, childLabels, 15000);
  if (!childClicked) childClicked = await clickMenuItem(appPage, childLabels, 10000);
  if (!childClicked) {{
    const dump = JSON.stringify(await visibleMenuTexts(scope));
    console.log(`YL_SUBMENU_DUMP ${{dump}}`);
    console.log(`FRAME_DIAG ${{await frameDiag(appPage, 'yl-submenu-click')}}`);
    expect(childClicked, `未找到 {biz_label} 子菜单（候选=${{childLabels.join('/')}}）；当前可见菜单=${{dump}}`).toBeTruthy();
  }}
  // 断言：列表已渲染（antd 表格或 Total 分页信息）——15s 轮询（口径同 CS rev2026-10-10）
  const listProbe = async () =>
    (await scope.locator('.ant-table, table').first().isVisible().catch(() => false)) ||
    (await scope.getByText(/Total\\s*\\d+/).first().isVisible().catch(() => false));
  const listT0 = Date.now();
  let listRendered = await listProbe();
  const listDeadline = listT0 + 15000;
  let listAttempts = 1;
  while (!listRendered && Date.now() < listDeadline) {{
    await appPage.waitForTimeout(500);
    listRendered = await listProbe();
    listAttempts += 1;
  }}
  console.log(`YL_LIST_READY ${{JSON.stringify({{ rendered: listRendered, attempts: listAttempts, elapsedMs: Date.now() - listT0 }})}}`);
  if (!listRendered) {{
    const bodyText = (await scope.locator('body').innerText().catch(() => '')).replace(/\\s+/g, ' ').slice(0, 400);
    console.log(`YL_LIST_DUMP ${{JSON.stringify({{ url: appPage.url(), bodyLen: bodyText.length, body: bodyText }})}}`);
    console.log(`FRAME_DIAG ${{await frameDiag(appPage, 'yl-list-not-rendered')}}`);
    await appPage.screenshot({{ path: `yl-list-not-rendered-${{Date.now()}}.png`, fullPage: true }}).catch(() => {{}});
  }}
  expect(listRendered, 'YL 主数据列表未渲染（15s 轮询后仍未检测到表格或 Total 分页）').toBeTruthy();

  // 列头只读比对（N3 第 2 步）：渲染成功后**只读**一次表头，不点击、不提交、不造数据。
  // 未配列时也会输出 COLUMN_HEADERS —— 这是采集真实列头、反补配置的入口。
  await columnDiag(scope, 'yl-list');

  // rev71：注入的 form_executor / coverage_snippet 引用变量名统一为 appScope
  const appScope = scope as any;

  // 编译断言执行：由定位器表的 flow 打开表单（executor 自己点 Add Store，避免重复点击）
{form_executor}{coverage_snippet}}});
"""

    def _is_enterprise_storage_recharge_case(self, test_case: TestCase) -> bool:
        return self._route_keyword_hit(
            self._case_route_text(test_case), ["企业储值", "储值充值", "储值账户列表"]
        )

    def _is_purchase_limit_case(self, test_case: TestCase) -> bool:
        """「会员限购商品管理」（交易云-运营）业务判定（正向识别）。"""
        return self._route_keyword_hit(
            self._case_route_text(test_case), _PURCHASE_LIMIT_KEYWORDS
        )

    def _is_storage_writeoff_case(self, test_case: TestCase) -> bool:
        """「储值免单活动」业务判定（正向识别，仅用于确认该业务）。"""
        return self._route_keyword_hit(
            self._case_route_text(test_case), _STORAGE_WRITEOFF_KEYWORDS
        )

    def _is_survey_operation_case(self, test_case: TestCase) -> bool:
        """「调查问卷管理」（营销云-系统）业务判定（正向识别）。"""
        return self._route_keyword_hit(
            self._case_route_text(test_case), _SURVEY_KEYWORDS
        )

    def _explicit_template_key(self, sub_function: Any | None) -> tuple[str | None, str]:
        """从**子功能**解析显式模板键（2026-10-20 F-1）。

        三档，越靠前越"显式"：
        1. `custom_config.template_key`
        2. `custom_config.field_locators.template_key`
        3. 子功能 `display_name` / `name` 与模板标签**归一化后完全相等**

        返回 `(key, source)`；解析不到返回 `(None, "")`，由调用方回退关键词匹配。
        值不在 `_WEB_TEMPLATE_LABELS` 内时**告警并忽略**（不静默改成别的模板：
        拼错的 key 若被当作"没配"，会退回关键词；若被当作"配了"，会跑错流程）。
        """
        if sub_function is None:
            return None, ""
        custom = getattr(sub_function, "custom_config", None) or {}
        if isinstance(custom, dict):
            locators = custom.get("field_locators")
            candidates = [
                ("custom_config.template_key", custom.get("template_key")),
                (
                    "custom_config.field_locators.template_key",
                    locators.get("template_key") if isinstance(locators, dict) else None,
                ),
            ]
            for source, raw in candidates:
                if not isinstance(raw, str) or not raw.strip():
                    continue
                key = raw.strip()
                if key in _WEB_TEMPLATE_LABELS:
                    return key, source
                logger.warning(
                    "[web_case_link] 子功能 %s 的 %s=%r 不是已知模板键（可选：%s），已忽略",
                    getattr(sub_function, "identifier", None),
                    source,
                    key,
                    "/".join(sorted(_WEB_TEMPLATE_LABELS)),
                )
        for attr in ("display_name", "name"):
            text = normalize_route_text(str(getattr(sub_function, attr, "") or "")).lower()
            if not text:
                continue
            for label, key in _TEMPLATE_LABEL_TO_KEY.items():
                if normalize_route_text(label).lower() == text:
                    return key, f"sub_function.{attr}"
        return None, ""

    def _resolve_web_template_key(
        self, test_case: TestCase, sub_function: Any | None = None
    ) -> str:
        """解析该用例应使用的**业务脚本模板**键。

        rev130（2026-10-20 F-1）：**显式绑定优先**。此前无条件走关键词匹配，实测
        236 条问卷用例里 61 条已绑子功能、内容完全可自动化，却因用例名为通用文案
        （「新建頁-保存按鈕-所有必填項填寫正確」）被判 `[unsupported_web_template]`——
        "配了却不生效"比"没配"更难排查。现在的顺序：

        1. 子功能显式模板键（`_explicit_template_key`）
        2. 用例文本关键词正向匹配
        3. 都不中 → 报错（仍**不兜底**：历史默认套「储值免单」模板，把 PR-9 的
           CS 用例跑成了小杨生煎储值免单流程）
        """
        self._last_template_key_audit = {}
        explicit_key, source = self._explicit_template_key(sub_function)
        if explicit_key:
            self._last_template_key_audit = {
                "key": explicit_key,
                "source": source,
                "sub_function": getattr(sub_function, "identifier", None),
            }
            logger.info(
                "[web_case_link] 模板键按显式绑定解析：%s ← %s（子功能 %s）",
                explicit_key,
                source,
                getattr(sub_function, "identifier", None),
            )
            return explicit_key
        if self._is_cs_module_case(test_case):
            return self._remember_template_key("cs_module", "keyword")
        if self._is_yl_masterdata_case(test_case):
            return self._remember_template_key("yl_masterdata", "keyword")
        if self._is_enterprise_storage_recharge_case(test_case):
            return self._remember_template_key("enterprise_storage_recharge", "keyword")
        if self._is_purchase_limit_case(test_case):
            return self._remember_template_key("purchase_limit", "keyword")
        if self._is_survey_operation_case(test_case):
            return self._remember_template_key("survey_operation", "keyword")
        if self._is_storage_writeoff_case(test_case):
            return self._remember_template_key("storage_writeoff", "keyword")
        raise ValueError(
            "[unsupported_web_template] 未找到与该用例业务匹配的 Web 脚本模板，已拒绝生成"
            "（现有模板均按具体业务流编写，套用会产生内容错误的脚本）："
            f"用例={getattr(test_case, 'identifier', None)} "
            f"名称={getattr(test_case, 'name', None)!r} "
            f"模块={getattr(test_case, 'module', None)!r} "
            f"归一化识别文本={self._case_route_text(test_case)!r}。"
            "两条修法任选：① 给子功能的 custom_config 配 `template_key`"
            "（cs_module / enterprise_storage_recharge / storage_writeoff / "
            "purchase_limit / yl_masterdata / survey_operation），显式绑定优先于关键词；"
            "② 让用例名称/模块可被正向识别。"
            "⚠️ `module` 由**上传需求文档的文件名 stem** 派生"
            "（api/v2/ai.py:_derive_requirement_module）；"
            "若文件名与业务关键词完全对不上，请改名重传而不是改关键词表。"
        )

    def _remember_template_key(self, key: str, source: str) -> str:
        """记录模板键来源后返回（供 `generation_params.template_routing` 审计）。"""
        self._last_template_key_audit = {"key": key, "source": source}
        return key

    def _web_test_label(self, test_case: TestCase, web_template_key: str | None = None) -> str:
        """资产命名标签。无模板键时按用例业务解析；解析不到即报错（不再静默兜底）。"""
        key = web_template_key or self._resolve_web_template_key(test_case)
        label = _WEB_TEMPLATE_LABELS.get(key)
        if not label:
            raise ValueError(
                f"[unsupported_web_template] 未知的 Web 脚本模板键: {key}"
            )
        return label

    def _generate_cs_module_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        *,
        manifest_block: str = "",
        coverage_snippet: str = "",
        project_identifier: str | None = None,
        form_executor: str = "",
        columns_json: str = "null",
    ) -> str:
        """Generate an entry smoke test for CS Module (eatizen CRM Console).

        与储值充值/免单模板的差异：
        - eatizen UAT 控制台为**验证码登录**：脚本回调平台验证码 OCR 端点
          （复用 agents/tools/web/captcha_ocr，ddddocr 优先），OCR 误读时整页重试。
        - 登录后经控制台顶栏「CRM」进入嵌入式 CRM（iframe），展开
          Customer Service 菜单并进入 Cases 工单列表做入口断言。
        """
        path_steps = json.dumps(navigation_path, ensure_ascii=False, indent=2)
        ocr_url = os.environ.get("WEB_TEST_CAPTCHA_OCR_URL") or (
            f"http://localhost:8000/api/v2/projects/{project_identifier or 'PR-9'}/web-tests/captcha-ocr"
        )
        # P0-2 阶段 A：三件套改由共享骨架渲染（CRM 是 antd 子菜单，非 el-menu，故 generic）。
        click_by_text_js = render_click_by_text(
            menu_selectors="generic", text_fallback="first", dom_click_fallback=True
        )
        required_click_js = render_required_click()
        frame_diag_js = render_frame_diag()
        column_diag_js = render_column_diag(columns_json)
        return f"""import {{ test, expect, type BrowserContext, type FrameLocator, type Locator, type Page }} from '@playwright/test';

const LOGIN_URL = process.env.WEB_TEST_BASE_URL || {json.dumps(base_url, ensure_ascii=False)};
const USERNAME = process.env.WEB_TEST_USERNAME;
const PASSWORD = process.env.WEB_TEST_PASSWORD;
// eatizen UAT 控制台为验证码登录：回调平台验证码 OCR 端点识别
const CAPTCHA_OCR_URL = process.env.WEB_TEST_CAPTCHA_OCR_URL || {json.dumps(ocr_url)};
const WEB_VALIDATION_PATH = {path_steps};
{manifest_block}
type ClickScope = Page | FrameLocator;

{click_by_text_js}

{required_click_js}

{frame_diag_js}

{column_diag_js}

// ---- 菜单定位（rev64）------------------------------------------------------
// 旧实现用"再点一次父菜单"来重试子菜单，而 antd 的 SubMenu 是**开关语义**：
// 第一次点击已展开时，第二次点击会把菜单**折叠**，于是必然找不到子项
// （实测 TR-84：页面快照显示 Customer Service 未展开、全页无 Cases 文本）。
// 这里改为：先读 aria-expanded 判断是否已展开 → 只在未展开时点击 → hover 兼容 popup 模式
// → 子项标签给候选集 → 失败时把可见菜单文本写进断言消息与日志。
async function visibleMenuTexts(scope: ClickScope): Promise<string[]> {{
  const items = scope.locator('.ant-menu-item, .ant-menu-submenu-title, [role="menuitem"]');
  const count = Math.min(await items.count().catch(() => 0), 80);
  const texts: string[] = [];
  for (let i = 0; i < count; i += 1) {{
    const text = (await items.nth(i).innerText().catch(() => '')).replace(/\\s+/g, ' ').trim();
    if (text) texts.push(text);
  }}
  return Array.from(new Set(texts));
}}

async function submenuTitle(scope: ClickScope, label: string): Promise<Locator | null> {{
  const candidates = [
    scope.locator('.ant-menu-submenu-title', {{ hasText: new RegExp(label) }}).first(),
    scope.getByRole('menuitem', {{ name: new RegExp(label) }}).first(),
  ];
  for (const locator of candidates) {{
    if (await locator.count().catch(() => 0)) return locator;
  }}
  return null;
}}

async function findMenuItem(scope: ClickScope, labels: string[]): Promise<Locator | null> {{
  // popup 模式的子菜单渲染在 .ant-menu-submenu-popup 里，需单独查
  const targets: ClickScope[] = [];
  if (await scope.locator('.ant-menu-submenu-popup').first().count().catch(() => 0)) {{
    targets.push(scope.locator('.ant-menu-submenu-popup').first() as unknown as ClickScope);
  }}
  targets.push(scope);
  for (const target of targets) {{
    for (const label of labels) {{
      const candidates = [
        target.locator('.ant-menu-item', {{ hasText: new RegExp(label) }}).first(),
        target.getByRole('menuitem', {{ name: new RegExp(label) }}).first(),
        target.getByText(new RegExp(label), {{ exact: false }}).first(),
      ];
      for (const locator of candidates) {{
        if (!(await locator.count().catch(() => 0))) continue;
        try {{
          await locator.waitFor({{ state: 'visible', timeout: 1500 }});
          return locator;
        }} catch {{
          // 换下一个候选定位器
        }}
      }}
    }}
  }}
  return null;
}}

async function expandSubmenu(
  scope: ClickScope,
  labels: string[],
  childLabels: string[],
  timeoutMs = 20000,
): Promise<boolean> {{
  const deadline = Date.now() + timeoutMs;
  let clickedOnce = false;
  while (Date.now() < deadline) {{
    for (const label of labels) {{
      const title = await submenuTitle(scope, label);
      if (!title) continue;
      try {{
        await title.waitFor({{ state: 'visible', timeout: 2500 }});
      }} catch {{
        continue;
      }}
      // 已展开（或子项已可见）→ 直接成功，绝不重复点击（重复点击=折叠）
      if ((await title.getAttribute('aria-expanded').catch(() => null)) === 'true') return true;
      if (await findMenuItem(scope, childLabels)) return true;
      if (clickedOnce) {{
        await new Promise((resolve) => setTimeout(resolve, 500));
        continue;
      }}
      clickedOnce = true;
      try {{
        await title.click({{ timeout: 5000 }});
      }} catch {{
        // 点击被遮挡 → hover（popup 模式子菜单靠 hover 展开）
        await title.hover({{ timeout: 5000 }}).catch(() => undefined);
      }}
      await new Promise((resolve) => setTimeout(resolve, 1200));
      if ((await title.getAttribute('aria-expanded').catch(() => null)) === 'true') return true;
      if (await scope.locator('.ant-menu-submenu-popup').first().isVisible().catch(() => false)) return true;
      if (await findMenuItem(scope, childLabels)) return true;
    }}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }}
  return false;
}}

async function clickMenuItem(scope: ClickScope, labels: string[], timeoutMs = 15000): Promise<boolean> {{
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {{
    const locator = await findMenuItem(scope, labels);
    if (locator) {{
      try {{
        await locator.click({{ timeout: 5000 }});
        return true;
      }} catch {{
        try {{
          await locator.hover({{ timeout: 3000 }});
          await locator.click({{ timeout: 5000, force: true }});
          return true;
        }} catch {{
          // 继续等下一轮
        }}
      }}
    }}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }}
  return false;
}}

async function ocrCaptcha(page: Page): Promise<string | null> {{
  // 验证码图片是 data:URL 内嵌 base64（logo 是独立 URL，不会误选）
  const img = page.locator('img[src^="data:image"]').first();
  if (!(await img.count())) return null;
  try {{
    const shot = await img.screenshot({{ timeout: 5000 }});
    const resp = await page.request.post(CAPTCHA_OCR_URL, {{
      data: {{ image_base64: shot.toString('base64') }},
      timeout: 20000,
    }});
    if (!resp.ok()) return null;
    const body = await resp.json();
    const text = ((body && body.data && body.data.text) || '').trim();
    // 2026-10-16 实测（UAT eatizen 控制台）：图形验证码长度在 2~4 之间浮动
    // （"WX"=2 / "YBI"=3 / "LWTE"=4）。原 `>= 4` 门槛会让 2~3 字符码被**永久拒绝**：
    // 10 轮重试全部在 `if (!code) continue` 退出（3 秒内跑完），最终报"验证码 OCR 重试 10 次
    // 后仍未离开登录页"，而真实原因与文案不符。放宽到 >= 2 —— OCR 误读的代价只是失败后
    // 重试一轮（配合 loginToConsole 的整页 reload 换新图，不会退化成同图重复提交）。
    return text.length >= 2 ? text : null;
  }} catch {{
    return null;
  }}
}}

async function loginToConsole(page: Page) {{
  // eatizen UAT：用户名/密码/图形验证码。OCR 可能误读 → 整页重试 10 次，
  // 每次重新进入登录页刷新验证码；登录成功判据=离开 /login。
  for (let attempt = 0; attempt < 10; attempt += 1) {{
    // 环境阻断前置判定：WAF/网关拦截时登录页返回 403/503，页面根本不渲染。
    // 实测（2026-09-24）：UAT 被 WAF 拦截时只表现为 `#login_username` 等不到，
    // 会被误判成产品缺陷并登记缺陷单 —— 这里显式区分。
    const loginResponse = await page.goto(LOGIN_URL, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
    const loginStatus = loginResponse ? loginResponse.status() : 0;
    if (loginStatus === 403 || loginStatus === 503 || loginStatus >= 500) {{
      throw new Error(
        `ENVIRONMENT_BLOCKED ${{JSON.stringify({{ reason: 'login_http_status', status: loginStatus }})}} `
        + `登录页返回 HTTP ${{loginStatus}}（疑似 WAF/网关拦截，非产品缺陷）`,
      );
    }}
    if (attempt > 0) {{
      // 2026-10-16 实测教训（TR-378 实锤）：`page.goto` 到同 hash 路由（…/#/login）是
      // **同文档导航**、不触发整页重载；而失败提交后 SPA 也不刷新验证码
      // ⇒ 不强制 reload 就会 10 轮都对着**同一张缓存图**重复同一个 OCR 误读，
      // 死循环到底（表现为"重试 10 次仍未离开登录页"）。
      await page.reload({{ waitUntil: 'domcontentloaded', timeout: 60000 }}).catch(() => undefined);
      await page.waitForTimeout(800);
    }}
    const user = page.locator('#login_username');
    const pass = page.locator('#login_password');
    await user.waitFor({{ state: 'visible', timeout: 30000 }});
    await user.fill(USERNAME);
    await pass.fill(PASSWORD);
    const code = await ocrCaptcha(page);
    if (!code) continue;
    await page.locator('#login_validateCode').fill(code);
    await page.locator('button:has-text("Login Immediately"), button.ant-btn-primary').first().click();
    await page.waitForTimeout(6000);
    if (!page.url().includes('/login')) return true;
  }}
  return false;
}}

async function openCrm(page: Page, context: BrowserContext) {{
  // 登录后控制台顶栏有「CRM」入口；可能当前页内嵌，也可能新开页签。
  const crmPagePromise = context.waitForEvent('page', {{ timeout: 20000 }}).catch(() => null);
  let clicked = false;
  for (let attempt = 0; attempt < 3 && !clicked; attempt += 1) {{
    clicked = await clickByText(page, ['CRM']);
    if (!clicked) await page.waitForTimeout(2000);
  }}
  expect(clicked, '未找到或无法点击：控制台顶部 CRM 入口（已重试 3 次）').toBeTruthy();
  const newPage = await crmPagePromise;
  const appPage = newPage || page;
  await appPage.waitForLoadState('domcontentloaded', {{ timeout: 60000 }}).catch(() => undefined);
  // 嵌入式 CRM 首屏加载较慢（实测约 15s）：等待左侧菜单或 iframe 出现
  await appPage.waitForFunction(
    () => /Customer Service|Member Center/.test(document.body.innerText) ||
      document.querySelectorAll('iframe').length > 0,
    null,
    {{ timeout: 60000 }},
  ).catch(() => undefined);
  await appPage.waitForTimeout(3000);
  return appPage;
}}

async function getCrmScope(appPage: Page): Promise<ClickScope> {{
  // N5（2026-10-18）：取帧**决策必须留痕**。历史实现只取第一个 iframe，
  // 失败时日志里连"取了哪个 frame、页面上有几个 iframe"都没有，无法判断是取错帧。
  // 这里只加诊断，**不改变选择逻辑**（是否改成多帧遍历，等诊断数据出来再定）。
  const iframes = appPage.locator('iframe');
  const total = await iframes.count().catch(() => 0);
  if (total > 0) {{
    await iframes.first().waitFor({{ state: 'attached', timeout: 30000 }}).catch(() => undefined);
    const src = String((await iframes.first().getAttribute('src').catch(() => '')) || '');
    console.log(`SCOPE_PICK ${{JSON.stringify({{ chosen: 'iframe#0', iframes: total, src: src.slice(0, 120), pageUrl: String(appPage.url()).slice(0, 120) }})}}`);
    return appPage.frameLocator('iframe').first();
  }}
  console.log(`SCOPE_PICK ${{JSON.stringify({{ chosen: 'page', iframes: 0, pageUrl: String(appPage.url()).slice(0, 120) }})}}`);
  return appPage;
}}

async function enterStorageAccountList(pg: Page, ctx: BrowserContext): Promise<ClickScope> {{
  // 从已登录页进入 CS 工单列表，返回可点击的作用域（iframe 或 page）。
  // rev120：原先这段逻辑内联在 test() 里，导致 form_executor 的上传隔离分支
  // 调用了一个不存在的函数（ReferenceError → 永远 main_fallback）。
  // 现在只保留这一份：主流程与隔离上下文都走它。
  // rev123：隔离上下文是**新开的 page**（about:blank）。`context.storageState()` 只带
  // cookie 与 localStorage，**不带 sessionStorage**，而本 SUT 的会话态落在 sessionStorage ——
  // 实测（TR-731）goto 登录页后仍停在登录页，点不到顶栏 CRM 入口。
  // 因此这里直接重放登录（与储值模板的 enterStorageAccountList 完全同语义）。
  // 主流程传进来的 page 已在控制台，不触发该分支，行为不变。
  if (!/^https?:/i.test(String(pg.url() || ''))) {{
    expect(await loginToConsole(pg), '隔离上下文登录未完成（3 次尝试后仍停留在登录页）').toBeTruthy();
    await pg.waitForLoadState('networkidle', {{ timeout: 30000 }}).catch(() => undefined);
  }}
  const appPage = await openCrm(pg, ctx);
  let scope = await getCrmScope(appPage);
  // 父菜单与子菜单都按候选集匹配；子菜单标签随 UAT 版本可能变化（Cases / Case List…）
  const parentLabels = ['Customer Service'];
  const childLabels = ['Cases', 'Case List', 'Case Management', '工单列表', '工单'];
  // 展开 Customer Service（幂等：已展开则不再点击，避免把菜单点回去）；iframe 作用域失败时退回主页面
  let expanded = await expandSubmenu(scope, parentLabels, childLabels, 20000);
  if (!expanded) {{
    // rev116：入口取帧时 iframe 可能尚未插入（iframes=0 → 选了 page），失败后重取一次。
    // rev116-fix：传 scope 而不是二次读到的帧数（竞态下"page + 帧数 1"会让重取被误判成没变化）。
    const refreshed = await refreshCrmScope(appPage, scope, 'cs-menu-expand');
    if (refreshed.stale) {{
      scope = refreshed.scope;
      expanded = await expandSubmenu(scope, parentLabels, childLabels, 20000);
    }}
  }}
  if (!expanded) {{
    scope = appPage;
    expanded = await expandSubmenu(scope, parentLabels, childLabels, 20000);
  }}
  if (!expanded) {{
    const dump = JSON.stringify(await visibleMenuTexts(scope));
    console.log(`CS_MENU_DUMP ${{dump}}`);
    console.log(`FRAME_DIAG ${{await frameDiag(appPage, 'cs-menu-expand')}}`);
    expect(expanded, `未找到或未能展开 Customer Service 菜单；当前可见菜单=${{dump}}`).toBeTruthy();
  }}
  await appPage.waitForTimeout(1500);
  // 点击子菜单（先在当前作用域找，再退回主页面/popup）
  let casesClicked = await clickMenuItem(scope, childLabels, 15000);
  if (!casesClicked) casesClicked = await clickMenuItem(appPage, childLabels, 10000);
  if (!casesClicked) {{
    const dump = JSON.stringify(await visibleMenuTexts(scope));
    console.log(`CS_SUBMENU_DUMP ${{dump}}`);
    console.log(`FRAME_DIAG ${{await frameDiag(appPage, 'cs-submenu-click')}}`);
    expect(casesClicked, `未找到工单列表子菜单（候选=${{childLabels.join('/')}}）；当前可见菜单=${{dump}}`).toBeTruthy();
  }}
  // 断言：工单列表已渲染（antd 表格或 Total 分页信息）
  // rev(2026-10-10)：原实现是「固定 5s + 单次 isVisible」，超时不留任何取证，
  // 实测在 CS 内容区**间歇性渲染为空**时无法区分「渲染慢」与「真不渲染」（台账 12）。
  // 改为 **60s 轮询**；最终仍失败时 dump 正文并落截图，供人工判定。
  const listProbe = async () =>
    (await scope.locator('.ant-table, table').first().isVisible().catch(() => false)) ||
    (await scope.getByText(/Total\\s*\\d+/).first().isVisible().catch(() => false));
  const listT0 = Date.now();
  let listRendered = await listProbe();
  // 超时取 15s：实测（2026-10-10，25 个样本）列表就绪仅需 115–834ms，
  // 15s 已是实测最大值的近 18 倍；过长的轮询只会让**真失败**的用例白等（每例 +55s）。
  const listDeadline = listT0 + 15000;
  let listAttempts = 1;
  while (!listRendered && Date.now() < listDeadline) {{
    await appPage.waitForTimeout(500);
    listRendered = await listProbe();
    listAttempts += 1;
  }}
  // 始终打印就绪耗时：用于区分「渲染慢」（老实现固定 5s 会误判失败）与「真不渲染」。
  console.log(`CS_LIST_READY ${{JSON.stringify({{ rendered: listRendered, attempts: listAttempts, elapsedMs: Date.now() - listT0 }})}}`);
  if (!listRendered) {{
    const bodyText = (await scope.locator('body').innerText().catch(() => '')).replace(/\\s+/g, ' ').slice(0, 400);
    console.log(`CS_LIST_DUMP ${{JSON.stringify({{ url: appPage.url(), bodyLen: bodyText.length, body: bodyText }})}}`);
    console.log(`FRAME_DIAG ${{await frameDiag(appPage, 'cs-list-not-rendered')}}`);
    await appPage.screenshot({{ path: `cs-list-not-rendered-${{Date.now()}}.png`, fullPage: true }}).catch(() => {{}});
  }}
  expect(listRendered, 'CS 工单列表未渲染（15s 轮询后仍未检测到表格或 Total 分页）').toBeTruthy();
  return scope as ClickScope;
}}

test('CS工单管理入口冒烟 - {test_case.identifier}', async ({{ page, context }}) => {{
  // 默认 30s 用例预算会在登录/跳转阶段被掐断，显式放宽；
  // rev71：全量批次（96 条）的编译断言全部落在这一个 test 里，300s 远不够
  //（实测 TR-104 跑满 600s 仍被 runner 掐死）。
  // rev116：预算改由执行器下发（WEB_TEST_BUDGET_MS = runner 进程超时 - 30s），
  // 保证 Playwright 自己先超时并产出报告；未注入时回退 30 分钟。
  test.setTimeout(Number(process.env.WEB_TEST_BUDGET_MS || 0) || 1800000);
  // 运行期自证：本次预算到底是多少、是执行器下发的还是回退默认值。
  console.log(`TEST_BUDGET ${{JSON.stringify({{ ms: Number(process.env.WEB_TEST_BUDGET_MS || 0) || 1800000, injected: !!process.env.WEB_TEST_BUDGET_MS }})}}`);
  test.skip(!USERNAME || !PASSWORD, '缺少 WEB_TEST_USERNAME/WEB_TEST_PASSWORD，跳过外部 B端 Web 冒烟执行');

  expect(await loginToConsole(page), '企业控制台登录失败（验证码 OCR 重试 10 次后仍未离开登录页）').toBeTruthy();

  // rev120：入口抽成函数（上传阶段的独立上下文要重放同一段入口，只能有一份实现）
  const scope = await enterStorageAccountList(page, context);

  // 列头只读比对（N3 第 2 步）：渲染成功后**只读**一次表头，不点击、不提交、不造数据。
  // 未配列时也会输出 COLUMN_HEADERS —— 这是采集真实列头、反补配置的入口。
  await columnDiag(scope, 'cs-list');

  // rev71 修复：注入的 form_executor / coverage_snippet 硬编码引用变量 `appScope`
  //（储值模板入口函数的返回值就叫 appScope）；本模板作用域变量叫 scope，这里补齐别名，
  // 否则运行时 ReferenceError: appScope is not defined（实测 TR-103）。
  const appScope = scope as any;

  // 编译断言执行：由定位器表的 flow 打开「Create」整页表单（executor 自己点，避免重复点击）
{form_executor}{coverage_snippet}}});
"""

    def _generate_enterprise_storage_recharge_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        *,
        manifest_block: str = "",
        coverage_snippet: str = "",
        form_executor: str = "",
    ) -> str:
        """Generate a read-only entry smoke test for the enterprise recharge form."""
        path_steps = json.dumps(navigation_path, ensure_ascii=False, indent=2)
        # P0-2 阶段 A：三件套改由共享骨架渲染（营销云为 antd 菜单，非 el-menu，故 generic）。
        click_by_text_js = render_click_by_text(
            menu_selectors="generic", text_fallback="first", dom_click_fallback=True
        )
        required_click_js = render_required_click()
        return f"""import {{ test, expect, type BrowserContext, type FrameLocator, type Page }} from '@playwright/test';

const LOGIN_URL = process.env.WEB_TEST_BASE_URL || {json.dumps(base_url, ensure_ascii=False)};
const USERNAME = process.env.WEB_TEST_USERNAME;
const PASSWORD = process.env.WEB_TEST_PASSWORD;
const WEB_VALIDATION_PATH = {path_steps};
{manifest_block}
type ClickScope = Page | FrameLocator;

{click_by_text_js}

{required_click_js}

async function waitForPortal(page: Page) {{
  // 门户卡片在 load 事件之后才渲染：轮询等待条件成立，而不是死等固定毫秒。
  await page.waitForFunction(
    () => /数盈[·\\s]*营销云|Marketing cloud/.test(document.body.innerText),
    null,
    {{ timeout: 45000 }},
  ).catch(() => undefined);
}}

async function openMarketingCloud(page: Page, context: BrowserContext) {{
  const newPagePromise = context.waitForEvent('page', {{ timeout: 15000 }}).catch(() => null);
  let clicked = false;
  for (let attempt = 0; attempt < 3 && !clicked; attempt += 1) {{
    clicked = await clickByText(page, ['数盈·营销云', '数盈营销云', '营销云', 'Marketing cloud']);
    if (!clicked) {{
      await page.waitForTimeout(2000);
    }}
  }}
  expect(clicked, '未找到或无法点击：数盈营销云卡片（已重试 3 次）').toBeTruthy();
  const appPage = (await newPagePromise) || page;
  await appPage.waitForLoadState('domcontentloaded', {{ timeout: 30000 }}).catch(() => undefined);
  await appPage.waitForTimeout(2500);
  return appPage;
}}

async function getMarketingScope(page: Page): Promise<ClickScope> {{
  const iframe = page.locator('iframe#systemIframe').first();
  if (await iframe.count()) {{
    await iframe.waitFor({{ state: 'attached', timeout: 30000 }}).catch(() => undefined);
    return page.frameLocator('#systemIframe').first();
  }}
  return page;
}}

async function loginToConsole(page: Page) {{
  // 登录失败的典型表现是"页面停在登录页、密码框被清空"，随后在门户卡片处报
  // "未找到或无法点击"——真实原因与表象不符。这里显式判定登录是否完成，
  // 并给一次有界重试（重填 + 重新提交），失败时给出准确原因。
  for (let attempt = 0; attempt < 3; attempt += 1) {{
    const user = page.locator('input[placeholder*="用户名"], input#login_username').first();
    const pass = page.locator('input[type="password"], input#login_password').first();
    await user.waitFor({{ state: 'visible', timeout: 20000 }});
    await user.fill(USERNAME);
    await pass.fill(PASSWORD);
    // 提交前回读：受控输入可能在渲染后被重置，填了空密码就白点一次
    if ((await pass.inputValue()) !== PASSWORD) {{
      await page.waitForTimeout(500);
      continue;
    }}
    await page.locator('button:has-text("登 录"), button[type="submit"]').first().click();
    try {{
      // 登录完成的判据：登录表单消失（不再有密码输入框）
      await page.waitForFunction(
        () => !document.querySelector('input[type="password"], input#login_password'),
        null,
        {{ timeout: 20000 }},
      );
      return true;
    }} catch {{
      await page.waitForTimeout(1000);
    }}
  }}
  return false;
}}

//: 进入「储值账户列表」并返回应用作用域（Page 或 FrameLocator）。
//: 抽成函数是为了让**独立上传测试上下文**也能复用同一条进入路径，避免两套导航逻辑漂移。
async function enterStorageAccountList(pg: Page, ctx: BrowserContext) {{
  // 环境阻断前置判定：WAF/网关拦截时登录页返回 403/503，页面不渲染；
  // 只看定位器超时会被误判成产品缺陷并建单（实测 2026-09-24）。
  const loginResponse = await pg.goto(LOGIN_URL, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  const loginStatus = loginResponse ? loginResponse.status() : 0;
  if (loginStatus === 403 || loginStatus === 503 || loginStatus >= 500) {{
    throw new Error(
      `ENVIRONMENT_BLOCKED ${{JSON.stringify({{ reason: 'login_http_status', status: loginStatus }})}} `
      + `登录页返回 HTTP ${{loginStatus}}（疑似 WAF/网关拦截，非产品缺陷）`,
    );
  }}
  expect(await loginToConsole(pg), '登录未完成（3 次尝试后仍停留在登录页：账号密码未通过或被页面清空）').toBeTruthy();
  // 登录后门户卡片可能晚于 load 事件渲染 → 等条件成立（最多 45s），再点击。
  await waitForPortal(pg);
  const appPage = await openMarketingCloud(pg, ctx);
  const scope = await getMarketingScope(appPage);
  await requiredClick(scope, '忠诚度菜单', ['忠诚度']);
  await requiredClick(scope, '储值管理菜单', ['储值管理']);
  await requiredClick(scope, '储值账户列表', ['储值账户列表']);
  return scope;
}}

test('企业储值充值入口与表单冒烟 - {test_case.identifier}', async ({{ page, context }}) => {{
  // 必须放在最前面：默认 30s 的用例预算会在登录/跳转阶段就把测试掐断
  // （实测 page.goto 报 "Test timeout of 30000ms exceeded"）。
  // rev116：不得超执行器下发的进程预算（未注入时用本模板原定的 4 分钟），
  // 否则进程先被杀、Playwright 来不及产出报告。
  test.setTimeout(Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS || 0) || 240000));
  console.log(`TEST_BUDGET ${{JSON.stringify({{ ms: Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS || 0) || 240000), injected: !!process.env.WEB_TEST_BUDGET_MS, cap: 240000 }})}}`);
  test.skip(!USERNAME || !PASSWORD, '缺少 WEB_TEST_USERNAME/WEB_TEST_PASSWORD，跳过外部 B端 Web 冒烟执行');

  const appScope = await enterStorageAccountList(page, context);

  await expect(appScope.getByText('手机号码', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('会员卡号', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByRole('button', {{ name: /企业充值[/]退款|创建储值充值/ }}).first()).toBeVisible({{ timeout: 15000 }});

  // 说明：UAT 目前把创建入口暴露为「企业充值/退款」。
  // 这里刻意**不再**断言 WEB_VALIDATION_PATH 包含某个菜单名——导航路径来自项目路径模板，
  // 与脚本功能断言无关；此前的元断言会在模板命中"储值免单"等相邻路径时造成必然失败。
  console.log('WEB_VALIDATION_PATH =', JSON.stringify(WEB_VALIDATION_PATH));
{form_executor}{coverage_snippet}}});
"""

    def _generate_purchase_limit_script(
        self,
        test_case: TestCase,
        base_url: str,
        navigation_path: list[str],
        *,
        manifest_block: str = "",
        coverage_snippet: str = "",
        form_executor: str = "",
    ) -> str:
        """Generate a read-only entry smoke test for 交易云-运营-会员限购商品管理.

        与储值充值模板同构，差异仅在：
        - 门户卡片是「数盈交易云」（点击后新开标签页，无 iframe）；
        - 菜单导航完全由路径模板的 navigation_path 驱动（不硬编码业务菜单）；
        - 列表页断言取需求字段表的 5 个展示列（R33）+「新建」入口；
        - 表单检查由 build_form_plan 编译清单生成，定位器可被
          子功能 custom_config["field_locators"] 覆盖；
        - 本版不提交：只读探针，不触发「选择限购商品/提交」等写操作。
        """
        path_steps = json.dumps(navigation_path, ensure_ascii=False, indent=2)
        # 门户首页已登录后直达；「登录/门户中心/工作台」是纯定位段，
        # 「数盈交易云/营销云/微商城/点击卡片进入」由门户卡片阶段统一处理
        # （openTradeCloud 点击卡片新开标签页），不属于应用内菜单，全部过滤。
        # 路径模板常把步骤写成动作描述（"点击运营菜单"），而页面元素文本是
        # 菜单名本身（"运营"）：归一化为元素文本，否则所有定位方式都命中不了
        # （实测：45s 内 clickByText/JS 直点全部落空，dom 取证后才定位到根因）。
        def _normalize_nav_step(step: Any) -> str:
            text = str(step).strip()
            text = re.sub(r"^(点击|进入|打开|选择)\s*", "", text)
            text = re.sub(r"(列表页|页面|菜单|按钮|入口)$", "", text).strip()
            return text

        nav_steps = [
            _normalize_nav_step(step) for step in (navigation_path or [])
            if not re.search(r"门户|工作台|登录|卡片|进入|交易云|营销云|微商城", str(step))
        ]
        nav_steps = [s for s in nav_steps if s]
        nav_steps_js = json.dumps(nav_steps, ensure_ascii=False, indent=2)
        # P0-2 阶段 A：三件套改由共享骨架渲染（交易云是 Element 菜单 + DOM 直点兜底）。
        click_by_text_js = render_click_by_text(
            menu_selectors="el_menu", text_fallback="last", dom_click_fallback=True
        )
        required_click_js = render_required_click()
        return f"""import {{ test, expect, type BrowserContext, type FrameLocator, type Page }} from '@playwright/test';

const LOGIN_URL = process.env.WEB_TEST_BASE_URL || {json.dumps(base_url, ensure_ascii=False)};
const USERNAME = process.env.WEB_TEST_USERNAME;
const PASSWORD = process.env.WEB_TEST_PASSWORD;
const WEB_VALIDATION_PATH = {path_steps};
const NAV_STEPS = {nav_steps_js};
{manifest_block}
type ClickScope = Page | FrameLocator;

{click_by_text_js}

{required_click_js}

async function waitForPortal(page: Page) {{
  await page.waitForFunction(
    () => /数盈[·\\s]*交易云|Trading cloud/.test(document.body.innerText),
    null,
    {{ timeout: 45000 }},
  ).catch(() => undefined);
}}

//: 交易云与营销云一样是"门户卡片 → 新开标签页"的应用。
async function openTradeCloud(page: Page, context: BrowserContext) {{
  const newPagePromise = context.waitForEvent('page', {{ timeout: 15000 }}).catch(() => null);
  let clicked = false;
  for (let attempt = 0; attempt < 3 && !clicked; attempt += 1) {{
    clicked = await clickByText(page, ['数盈·交易云', '数盈交易云', '交易云', 'Trading cloud']);
    if (!clicked) {{
      await page.waitForTimeout(2000);
    }}
  }}
  expect(clicked, '未找到或无法点击：数盈交易云卡片（已重试 3 次）').toBeTruthy();
  let appPage = await newPagePromise;
  if (!appPage) {{
    // 定位器点击未带出弹窗（执行器环境实测）：降级为真实鼠标坐标点击卡片中心。
    // 该路径与人工操作一致（2026-09-29 实测可稳定打开交易云新标签页）。
    const box = await page.evaluate(() => {{
      const cols = Array.from(document.querySelectorAll('.ant-col, [class*=flexCenter], .ant-card'));
      const t = cols.find(e => /交易云/.test((e as HTMLElement).innerText || ''));
      if (!t) return null;
      const r = (t as HTMLElement).getBoundingClientRect();
      return {{ x: r.x + r.width / 2, y: r.y + r.height / 2 }};
    }});
    if (box) {{
      const pop2 = context.waitForEvent('page', {{ timeout: 12000 }}).catch(() => null);
      await page.mouse.click(box.x, box.y);
      appPage = (await pop2) || null;
    }}
  }}
  appPage = appPage || page;
  await appPage.waitForLoadState('domcontentloaded', {{ timeout: 30000 }}).catch(() => undefined);
  await appPage.waitForTimeout(2500);
  console.log('[nav] appPage.url =', appPage.url());
  // 外壳判定：卡片点击可能未新开标签页（弹窗拦截），此时 appPage 仍是门户首页，
  // 后续菜单点击会白等 20s 才报"未找到或无法点击"。
  // 注意不能用"设置"——门户用户下拉菜单也含"设置"，会误判通过（实测踩过）。
  const shellReady = await appPage.waitForFunction(
    () => /门店|产品|订单|运营/.test(document.body.innerText),
    null,
    {{ timeout: 30000 }},
  ).then(() => true).catch(() => false);
  console.log('[nav] shellReady =', shellReady);
  expect(shellReady, '交易云应用外壳未渲染（卡片点击后未进入交易云，弹窗可能被拦截）').toBeTruthy();
  return appPage;
}}

async function getAppScope(page: Page): Promise<ClickScope> {{
  const iframe = page.locator('iframe#systemIframe').first();
  if (await iframe.count()) {{
    await iframe.waitFor({{ state: 'attached', timeout: 30000 }}).catch(() => undefined);
    return page.frameLocator('#systemIframe').first();
  }}
  return page;
}}

async function loginToConsole(page: Page) {{
  // 与储值模板同一登录实现：显式判定登录完成 + 有界重试，
  // 避免"页面停在登录页、密码框被清空"被误报成后续菜单缺失。
  for (let attempt = 0; attempt < 3; attempt += 1) {{
    const user = page.locator('input[placeholder*="用户名"], input#login_username').first();
    const pass = page.locator('input[type="password"], input#login_password').first();
    await user.waitFor({{ state: 'visible', timeout: 20000 }});
    await user.fill(USERNAME);
    await pass.fill(PASSWORD);
    if ((await pass.inputValue()) !== PASSWORD) {{
      await page.waitForTimeout(500);
      continue;
    }}
    await page.locator('button:has-text("登 录"), button[type="submit"]').first().click();
    try {{
      await page.waitForFunction(
        () => !document.querySelector('input[type="password"], input#login_password'),
        null,
        {{ timeout: 20000 }},
      );
      return true;
    }} catch {{
      await page.waitForTimeout(1000);
    }}
  }}
  return false;
}}

//: 进入「会员限购商品管理」列表页并返回应用作用域。
async function enterPurchaseLimitList(pg: Page, ctx: BrowserContext) {{
  const loginResponse = await pg.goto(LOGIN_URL, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});
  const loginStatus = loginResponse ? loginResponse.status() : 0;
  if (loginStatus === 403 || loginStatus === 503 || loginStatus >= 500) {{
    throw new Error(
      `ENVIRONMENT_BLOCKED ${{JSON.stringify({{ reason: 'login_http_status', status: loginStatus }})}} `
      + `登录页返回 HTTP ${{loginStatus}}（疑似 WAF/网关拦截，非产品缺陷）`,
    );
  }}
  expect(await loginToConsole(pg), '登录未完成（3 次尝试后仍停留在登录页：账号密码未通过或被页面清空）').toBeTruthy();
  await waitForPortal(pg);
  const appPage = await openTradeCloud(pg, ctx);
  const scope = await getAppScope(appPage);
  // 菜单导航完全由路径模板驱动（如：运营 → 会员限购商品管理）
  // 执行器机器负载高时 UAT 菜单渲染明显更慢：单步预算放宽到 45s，
  // 并在外壳就绪后留出 settle 时间，避免"外壳已渲染但菜单尚未可点"的竞态。
  await appPage.waitForTimeout(2000);
  for (const step of NAV_STEPS) {{
    try {{
      await requiredClick(scope, step, [step], 45000);
    }} catch (e) {{
      // 失败现场取证：执行器环境的快照页归属不可靠（实测 url 与内容错位），
      // 以运行期实测为准，避免把环境问题误判成产品缺陷。
      const dbg = await appPage.evaluate(() => {{
        const iframes = Array.from(document.querySelectorAll('iframe')).map(f => f.src || 'about:blank');
        const resources = performance.getEntriesByType('resource').map((r: any) => r.name).slice(-12);
        const appRoot = document.querySelector('#app');
        return {{
          body: document.body.innerText.slice(0, 300),
          iframes: iframes.slice(0, 5),
          readyState: document.readyState,
          appHtmlLen: appRoot ? appRoot.innerHTML.length : -1,
          appHtmlSnippet: appRoot ? appRoot.innerHTML.slice(0, 1500) : '',
          perfNow: Math.round(performance.now()),
          resourcesTail: resources.map((u: string) => u.split('/').slice(-1)[0]).slice(0, 12),
        }};
      }}).catch((err) => String(err));
      console.log('[nav] FAILED step=' + step + ' appPage.url=' + appPage.url() + ' dbg=' + JSON.stringify(dbg));
      throw e;
    }}
    await appPage.waitForTimeout(1500);
  }}
  return scope;
}}

test('会员限购商品管理入口与列表冒烟 - {test_case.identifier}', async ({{ page, context }}) => {{
  // rev116：不得超执行器下发的进程预算（未注入时用本模板原定的 4 分钟），
  // 否则进程先被杀、Playwright 来不及产出报告。
  test.setTimeout(Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS || 0) || 240000));
  console.log(`TEST_BUDGET ${{JSON.stringify({{ ms: Math.min(240000, Number(process.env.WEB_TEST_BUDGET_MS || 0) || 240000), injected: !!process.env.WEB_TEST_BUDGET_MS, cap: 240000 }})}}`);
  test.skip(!USERNAME || !PASSWORD, '缺少 WEB_TEST_USERNAME/WEB_TEST_PASSWORD，跳过外部 B端 Web 冒烟执行');

  const appScope = await enterPurchaseLimitList(page, context);

  // 需求字段表 R33：列表必须展示 5 个业务字段 + 新建入口
  await expect(appScope.getByText('限购商品名称', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('限购标签', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('限购周期', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByText('状态', {{ exact: true }}).first()).toBeVisible({{ timeout: 15000 }});
  await expect(appScope.getByRole('button', {{ name: /新\\s*建/ }}).first()).toBeVisible({{ timeout: 15000 }});

  // 本版不提交：只读探针（不进入新建表单、不做任何写操作）；
  // 表单字段级检查由编译清单驱动（见下方 form_executor）。
  console.log('WEB_VALIDATION_PATH =', JSON.stringify(WEB_VALIDATION_PATH));
{form_executor}{coverage_snippet}}});
"""
