# Web 业务脚本配置化设计（P0-2）

> 状态：**设计文档（不改代码）**
> 日期：2026-09-30
> 范围：平台 Web 侧脚本生成链路（`web_case_link_service.py` / `web_case_compiler.py`）
> 前置已落地：P6 数据化注册（`web_form_templates` 表 + 迁移 0022 + API 层）

---

## 一、问题陈述（基于实测）

### 1.1 现状量化

`backend/app/services/web_case_link_service.py` 中，每个业务模板对应一个**独立的 TypeScript 脚本生成器函数**，脚本内容以 f-string 全量内联在 Python 源码里：

| 生成器函数 | 行数 | 字符数 | 对应模板键 |
|---|---:|---:|---|
| `_generate_cs_module_script` | 315 | 12,549 | `cs_module` |
| `_generate_purchase_limit_script` | 256 | 10,789 | `purchase_limit` |
| `_generate_storage_writeoff_script` | 195 | 7,673 | `storage_writeoff` |
| `_generate_enterprise_storage_recharge_script` | 160 | 6,438 | `enterprise_storage_recharge` |
| **合计** | **926** | **37,449** | 4 个业务 |

该文件当前体量 **124 KB**，`web_case_compiler.py` **142 KB**。

### 1.2 结构性代价

新增一个业务方的成本路径（实测确认）：

1. 在 `_WEB_TEMPLATE_LABELS` 登记模板键与中文标签；
2. 新增业务正向识别关键词常量（如 `_PURCHASE_LIMIT_KEYWORDS`）；
3. 新增 `_is_xxx_case()` 判定函数；
4. 在 `_resolve_web_template_key()` 增加分支；
5. 在 `_generate_web_script()` 的 `if template_key == ...` 链增加分支；
6. **手写 150~320 行 TypeScript 注入脚本**（登录、门户入口、外壳判定、菜单导航、列表断言、表单检查）。

其中 1~5 步合计约 30 行配置性代码；**第 6 步占了整体工作量的 90%**，且是纯复制-粘贴-改文本的机械劳动。

### 1.3 与 API 侧的方向矛盾

API 侧已有 `api_contract_profiles.py`（来源契约配置化起点，commit `baf20b3`），走的是"数据描述契约 → 生成脚本"的路线。
Web 侧仍是"代码内联脚本"，两侧架构方向不一致——**同一平台内存在两套心智模型**，维护者需要切换思维方式。

### 1.4 已具备的配置化基础（重要）

P6 已经把**表单定位器**这一层数据化了：

- 模型：`backend/app/models/web_form_template.py`（`fields` JSONB + `extra_config` JSONB + `source_ref` + `status`）
- 接口：`backend/app/api/v2/web_form_templates.py`（GET / POST / PUT / DELETE / `resolve-preview`）
- 迁移：`backend/alembic/versions/0022_web_form_templates.py`（幂等建表/补列）
- 解析：`web_case_compiler.resolve_form_locators()`，优先级链
  `sub_function_override → project_registration → template_default → none`
- 编译器：`build_form_plan()` / `build_form_plan_for_template()`——**已经是参数化编译器**，定位器表是纯数据字典

**结论：P0-2 不是从零设计。要配置化的是"脚本骨架"，不是"表单定位器"（后者已经完成了）。**

---

## 二、现状解剖：4 个模板的共通与特异

### 2.1 实测提取的函数结构

对 4 个生成器逐一抽取内部定义的 TypeScript 函数：

| 模板 | 内部函数 |
|---|---|
| `cs_module` | clickByText, clickMenuItem, expandSubmenu, findMenuItem, getCrmScope, loginToConsole, ocrCaptcha, openCrm, requiredClick, submenuTitle, visibleMenuTexts |
| `enterprise_storage_recharge` | clickByText, enterStorageAccountList, getMarketingScope, loginToConsole, openMarketingCloud, requiredClick, waitForPortal |
| `purchase_limit` | clickByText, enterPurchaseLimitList, getAppScope, loginToConsole, openTradeCloud, requiredClick, waitForPortal |
| `storage_writeoff` | chooseActivityType, clickByText, clickClickableAncestorByText, getMarketingScope, loginToConsole, openMarketingCloud, requiredClick |

**交集（4 个模板都有）**：

```
clickByText      —— 带超时预算的重试点击（含 DOM 直点兜底）
loginToConsole   —— 控制台登录（显式判定登录完成 + 有界重试）
requiredClick    —— clickByText 的断言包装
```

**这也是唯一真正的"共通骨架"，只有 3 个函数。**

### 2.2 差异分类

剩余全部是业务特异性，可归为 4 类：

| 类别 | 说明 | 示例 |
|---|---|---|
| **A. 门户入口** | 从控制台首页进入目标应用（可能新开标签页/iframe） | `openTradeCloud`（新标签页）、`openMarketingCloud`、`openCrm`（iframe + 验证码 OCR） |
| **B. 外壳判定** | 确认应用外壳已渲染（防止后续菜单点击白等） | 交易云判 `/门店|产品|订单|运营/`；营销云另有判定文本 |
| **C. 菜单导航** | 沿 `navigation_path` 逐级点击 | 交易云是 Element `el-menu`（`li[role=menuitem]`），CS Module 是 CRM 子菜单展开 |
| **D. 列表/表单断言** | 列表页展示列 + 新建入口 + 表单检查 | 限购 5 列（R33）、储值 6 列、免单活动类型 |

### 2.3 设计含义

**可以配置化的**：A（入口配置）、B（判定文本配置）、C（导航路径——已由 `navigation_path` 参数化）、D 的表单部分（**已由 P6 完成**）。

**难以完全配置化的**：登录流程的差异（验证码 OCR vs 普通登录）、iframe 嵌套结构、特殊交互（`chooseActivityType`、`clickClickableAncestorByText`）。

**因此正确的设计目标不是"消灭代码"，而是"把 90% 的机械复制变成配置，把真正特殊的 10% 保留为可插拔的 hook"。**

---

## 三、设计方案

### 3.1 分层模型

```
┌─────────────────────────────────────────────────────────┐
│ L4  项目级注册（数据，可在线维护）                        │
│     web_form_templates        表单定位器  ← P6 已完成     │
│     web_script_profiles (新)  脚本骨架配置  ← P0-2 本文案  │
├─────────────────────────────────────────────────────────┤
│ L3  平台内置模板（代码，随版本发布）                      │
│     TEMPLATE_FORM_LOCATORS    表单兜底表  ← 已有          │
│     BUILTIN_SCRIPT_PROFILES   脚本兜底配置 ← P0-2 新增    │
├─────────────────────────────────────────────────────────┤
│ L2  脚本骨架渲染器（代码，业务无关）                      │
│     render_web_script(profile, form_plan, case) ← P0-2    │
│     内部固化 clickByText / loginToConsole / requiredClick │
├─────────────────────────────────────────────────────────┤
│ L1  能力原语（代码，业务无关）                            │
│     登录策略 / 入口策略 / 外壳判定 / 导航 / 断言            │
└─────────────────────────────────────────────────────────┘
```

**关键原则：L2 及以下完全业务无关；L3 是随版本发布的兜底；L4 是项目级覆盖。**

### 3.2 脚本骨架配置（`WebScriptProfile`）

建议新增数据类，字段对应 2.2 的差异分类：

```python
@dataclass(frozen=True)
class WebScriptProfile:
    """一个业务脚本模板的**骨架配置**（业务无关渲染器的输入）。"""

    template_key: str                  # 业务模板键，与 _WEB_TEMPLATE_LABELS 一致
    label: str                         # 中文标签（资产命名用）

    # --- A. 门户入口 ---
    portal_entry: PortalEntry          # 见下
    # --- B. 外壳判定 ---
    shell_probe: ShellProbe            # 见下
    # --- C. 菜单导航 ---
    nav_strategy: str = "el_menu"      # el_menu | crm_submenu | plain_text
    # --- D. 列表断言 ---
    list_assertions: list[str] = field(default_factory=list)   # 期望可见的列名/入口文本
    # --- 登录 ---
    login_strategy: str = "console"    # console | console_captcha
    # --- 钩子（保留逃生舱）---
    hooks: dict[str, str] = field(default_factory=dict)
    #   {"before_nav": "...", "after_entry": "..."}  注入的自定义 TS 片段
```

```python
@dataclass(frozen=True)
class PortalEntry:
    """门户卡片入口配置。"""
    labels: list[str]                  # 卡片文本候选，如 ["数盈·交易云", "数盈交易云"]
    open_mode: str                     # new_tab（新开标签页）| same_page | iframe
    iframe_selector: str = ""          # open_mode=iframe 时使用，如 "iframe#systemIframe"
    coordinate_fallback: bool = True   # 定位器点不中时降级为真实鼠标坐标点击（实测需要）
    shell_keywords: list[str] = field(default_factory=list)   # 外壳判定文本
    shell_timeout_ms: int = 45000
```

### 3.3 现有 4 个模板的配置化映射（示例）

以限购为例，配置后：

```python
BUILTIN_SCRIPT_PROFILES["purchase_limit"] = WebScriptProfile(
    template_key="purchase_limit",
    label="B端会员限购商品管理",
    portal_entry=PortalEntry(
        labels=["数盈·交易云", "数盈交易云", "交易云", "Trading cloud"],
        open_mode="new_tab",
        coordinate_fallback=True,
        shell_keywords=["门店", "产品", "订单", "运营"],
        shell_timeout_ms=30000,
    ),
    nav_strategy="el_menu",
    list_assertions=["限购商品名称", "限购标签", "限购周期", "状态", "限购门槛"],
    login_strategy="console",
)
```

对照：**约 15 行配置，替代原来 256 行内联 TypeScript。**

`cs_module` 因有验证码 OCR，`login_strategy="console_captcha"`，其余同理。`storage_writeoff` 的 `chooseActivityType` 走 `hooks`。

### 3.4 渲染器职责边界

`render_web_script(profile, form_plan, case)` 需要保证：

1. **必注入的三件套**：`clickByText` / `loginToConsole` / `requiredClick` 由渲染器统一生成，**不允许各 profile 自定义**（消除 4 份重复实现）；
2. **业务文本零硬编码校验**：渲染器内部不得出现任何中文业务词，全部来自 `profile` 或 `navigation_path`；
3. **`form_plan` 注入方式不变**：沿用现有 `render_form_executor(form_plan, ...)` 的 `@@FORM_PLAN@@` 占位符替换机制（`web_case_compiler.py:866+`），**不引入第二套注入协议**；
4. **`hooks` 是唯一逃生舱**：特殊交互（验证码 OCR、活动类型选择）通过 hook 注入，并在配置中显式登记，便于审计。

### 3.5 与 P6 的衔接

| 关注点 | P6（已落地） | P0-2（本文案） |
|---|---|---|
| 数据表 | `web_form_templates` | `web_script_profiles`（**建议新表，不与前者混用**） |
| 唯一键 | `project_id + template_key` | `project_id + template_key`（同构） |
| 解析优先级 | sub_function_override → project_registration → template_default → none | 建议**同构**，保持心智一致 |
| 在线维护 | 已有 API | 建议同构提供 API |

**建议**：两张表**结构同构、优先级链同构**，但**不合并**。理由：`web_form_templates` 的 `fields` 是字段定位器（DOM 级），`web_script_profiles` 的是流程骨架（导航级），二者的变更频率、责任人、审计颗粒度都不同。合并会让"改一个字段定位器"和"改整个业务流程"走同一条审批路径。

---

## 四、实施步骤（分阶段，可独立验收）

### 阶段 A：抽公共骨架（不改行为）

**⚠️ 实施前关键发现（2026-09-30 实测，直接修订了本阶段做法）**

逐字对比 4 个模板的三件套实现，发现它们**同源但已分叉**，差异有明确语义：

| 差异点 | 分布 | 语义 | 处理 |
|---|---|---|---|
| `clickByText` 含 3 个 Element 菜单定位器（`li[role=menuitem]` / `.el-menu-item` / `.el-submenu__title`）+ 文本兜底取 `last()` | 仅 `purchase_limit` | 交易云是 el-menu，通用 `first()` 会命中面包屑导致点不中「运营」（实测踩过） | **必须保留为配置项**（`nav_strategy="el_menu"`） |
| `clickByText` 含 DOM 直点兜底（`el.click()` 绕过命中测试） | 除 `storage_writeoff` 外都有 | 应对全屏 loading/遮罩挡住页面 | 统一补上（能力增强，需验证） |
| `requiredClick` 带 `timeoutMs` 参数 | 仅 `purchase_limit` | 支持自定义超时 | 统一带上（向后兼容：默认值 20000） |
| `requiredClick` 写法（`expect(await f(),...)` vs `const c=await f(); expect(c,...)`） | 两种都有 | **语义等价** | 安全统一 |

**结论：阶段 A 不能做"简单替换式抽取"**——那会让 `purchase_limit` 失去 el-menu 定位能力（交易云菜单全部点不中），也会让 `storage_writeoff` 意外获得 DOM 直兜底。

**修订后的阶段 A 做法：参数化统一。**

- 抽取三件套为**带配置参数**的统一实现：
  - `clickByText(scope, candidates, timeoutMs, {menuSelectors, textFallback})`
  - `loginToConsole(page, {strategy})`
  - `requiredClick(scope, stepName, candidates, timeoutMs)`
- 各模板通过**参数**表达自己的差异（如 `menuSelectors: 'el_menu' | 'generic'`）；
- **验收标准：4 个模板产出的脚本与现状 `diff` 仅含注释/空白差异**；若某处 `diff` 超出注释范围，必须在 PR 描述里逐条说明"这是有意的能力统一"。

这一步消除 ~600 行重复，**风险可控且不改变现有行为**。

### 阶段 B：引入 `WebScriptProfile` 内置配置

- 新增 `WebScriptProfile` / `PortalEntry` / `ShellProbe` 数据类；
- 把 4 个业务的门户入口、外壳判定、列表断言**从代码字面量搬到配置**；
- `_generate_web_script()` 的 `if template_key == ...` 链替换为「查配置 → 渲染」；
- **验收标准：4 个模板的脚本内容与阶段 A 结果语义等价**，且限购/储值两条链路在 UAT 实跑通过。

### 阶段 C：项目级注册表 + API

- 新增 `web_script_profiles` 表 + 迁移 `0023`；
- 提供与 `web_form_templates` 同构的 API（含 `resolve-preview`）；
- **验收标准：新业务方可以不改代码完成接入**（用一个真实新业务验证）。

### 阶段 D：新业务接入验证

- 选一个尚未接入的业务，走"只写配置"路径接入；
- 记录实际耗时与遇到的问题，反哺配置 schema。

---

## 五、风险与对策

| 风险 | 说明 | 对策 |
|---|---|---|
| **配置表达能力不足** | 新业务的特殊交互无法用配置表达，被迫加字段 | `hooks` 逃生舱 + 阶段 D 用真实业务验证 schema |
| **渲染器成为新上帝对象** | 所有业务都挤进一个渲染器，变成另一种硬编码 | 严格分层：渲染器只做骨架拼接，业务差异一律外置；新增字段需评审 |
| **回归风险** | 4 个模板正在跑真实业务，重构可能引入行为漂移 | 阶段 A 用 `diff` 空验证；阶段 B 用 UAT 实跑验证 |
| **与 P6 概念混淆** | 两个"模板"表，维护者分不清 | 命名明确区分：`form_templates`（字段定位器）vs `script_profiles`（流程骨架）；文档交叉引用 |
| **过早抽象** | 只有 4 个业务就做配置化，可能抽象错 | 4 个样本已足够识别骨架（实测共通仅 3 函数）；且阶段 A/B 可独立回退 |

---

## 六、本文案不涉及的部分（明确边界）

1. **表单定位器的配置化**——已由 P6 完成，本文案只做衔接设计；
2. **断言强度提升（P1-3 负路径模板化）**——独立议题，本文案只保证渲染器不阻碍它；
3. **小程序执行通道**——`ExecutorRegistry` 只注册 `api_test` / `scenario` / `web_test`（`executors.py:749-758`），小程序无执行链路，**暂缓**；
4. **具体代码改动**——本文案按约定只出设计，不动代码。阶段 A 起才开始改。

---

## 七、附：一个待修的真实缺陷（设计评审时需一并处理）

**位置**：`backend/app/api/v2/web_form_templates.py:275-276`

```python
row = await _load_registration(db, project.id, key)
registered = None
if row is not None and row.status == "active" and row.fields:
    registered = {"fields": row.fields}      # ← 只传 fields，丢掉 extra_config
```

**问题**：`form_button` / `modal` / `field_modes` 等**打开表单必需的键**都存在 `extra_config` 中（见 `models/web_form_template.py:60-69` 的注释）。这里构造 `registered` 时只带了 `fields`，导致：

- `resolve-preview` 接口**谎报**可用性——它说"字段可用"，但执行时拿不到 `form_button`；
- 与 TR-163 崩溃（`FORM_PLAN.locators.form_button is not iterable`）**同根因**。

**对照**：执行链路里的 `web_case_link_service._merge_registered_locators()` 是**正确**的（显式合并 `extra_config` 并忽略其中的 `fields` 键）。

**影响面**：仅 `resolve-preview` 这一处（执行链路不受影响），但它是**排查问题的第一入口**，谎报会误导排查方向。

**建议**：在做 P0-2 阶段 C（同构 API）时，一并修正——并且**新 API 不复制这个错误**。

---

## 八、下一步

按用户决策，P0-2 本文案完成后进入：
- **P1-3 负路径模板化**（提升 Web 断言强度，让自动执行能发现入口不一致类缺陷）
- **DEFECT-002 其余 4 个分页接口 + 平台回归接线**（让 P5 契约断言真正进入自动执行体系）
