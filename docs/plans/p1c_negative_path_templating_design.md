# 负路径断言模板化设计（P1-3）

> 状态：**阶段 A ✅ / 阶段 B ✅ 已落地并验收；阶段 C / D 未开始**
> 日期：2026-09-30（阶段 B 实施记录见 §三「阶段 B」）
> 范围：Web 侧负路径提交层（`web_case_compiler.py` 的表单校验执行器 + 负路径提交层）
> 关联：DEFECT-001（modify 不校验重复商品）暴露的断言能力缺口
>
> 注：本文案 §一~§二 是**设计原稿**（写于动手之前，正文里的"不改代码"等措辞
> 按当时的约束成立）。实现与设计不一致之处，已在对应阶段下用
> "实施记录 / 有意偏离"小节逐条说明，**不要以原稿措辞为准**。

---

## 一、问题陈述（基于实测）

### 1.1 现有负路径能力（已相当成熟，勿重复造）

平台**已有**负路径提交层，实现在 `render_form_executor()` 生成的 TypeScript 中（rev67/rev69，口径经 2026-09-25 用户确认）：

| 能力 | 实现位置 | 说明 |
|---|---|---|
| 负路径入队 | `submitQueue.push({check, name, kind})` | `kind` 取 `required_empty` / `probe` |
| 客户端层拦截优先 | `web_case_compiler.py:1839-1990` | 先看页面是否已弹校验错误，有则不入队 |
| 真实提交 | `web_case_compiler.py:2052+` 的 `clickSubmit()` | antd v3/v4 兼容，含整页表单兜底 |
| 网络响应捕获 | `:2068` 的 `onNetResp` / `netLog` | 按 `tk` 令牌隔离窗口，防迟到响应污染 |
| request 级诊断 | `:2078` 的 `reqLog` | 区分"请求未发出" vs "发出无响应" |
| JS 报错捕获 | `:2084` 的 `errLog` | `pageerror` + `console.error` |
| 兜底填充 | `:2111` 的 `fillEmptyErrored()` | 非目标字段空必填填占位值，保证"仅目标字段违规" |
| 写隔离 | `:1421` 的 `non_upload_api_blocked` 等三类计数 | 非白名单写请求一律 `route.abort()` |

**结论：P1-3 不是"从零建负路径"，而是"补齐现有负路径的覆盖盲区"。**

### 1.2 入队条件实测（关键约束）

抓取全部 `submitQueue.push` 的触发路径：

| 触发点 | 行号 | `kind` | 驱动断言 |
|---|---|---|---|
| 单选组/上传控件必填 | 1848 | `required_empty` | `required` |
| 下拉"展开/取消未触发提示" | 1947 | `required_empty` | `required` |
| 日期"面板关闭无提示" | 1958 | `required_empty` | `required` |
| 通用输入框空必填 | 1990 | `required_empty` | `required` |
| 边界/范围/精度等未拦截 | 2036 | `probe`（带非法探针值） | `boundary` / `range` / `precision` / `max_length` / `max_count` |

**全部入队条件都是"单个字段的值非法"**。

### 1.3 盲区：业务规则级负路径

DEFECT-001 的形态是：

> 编辑（modify）时追加一个**已被其他活动占用的商品**，期望服务端拒绝「该商品已新建」，
> 实际返回 `resultCode: "0"`（success）。

它的特征与现有负路径**根本不同**：

| 维度 | 现有负路径 | DEFECT-001 这类 |
|---|---|---|
| 违规主体 | 单个字段的值 | **字段值与外部状态的冲突** |
| 前置条件 | 无（清空即可） | **需要一个已存在的冲突对象**（被占用的商品） |
| 判定期望 | 服务端拒绝该值 | 服务端拒绝**跨记录唯一性冲突** |
| 入口 | 新建表单 | **编辑入口**（不同 API：`/modify` vs `/add`） |
| 触发时机 | 表单单次提交 | **追加操作**（先选再追加，多步交互） |

**这类断言在现有框架中完全没有入口**，因此：

- DEFECT-001 这类"同一业务规则在不同入口实现不一致"的缺陷，**自动执行必然发现不了**；
- 本次靠工作区临时脚本（`demo2_script.ts`，9046 B）手工完成，**无法沉淀为平台能力**。

### 1.4 实测佐证：手写脚本已证明可行

TR-160 / TR-161 的实跑结果（来自 `runs_detail.json`）：

```
error_message: "Error: [待确认] modify 接受了冲突商品 11281:
  {\"data\":null,...,\"resultCode\":\"0\",\"resultMsg\":\"success\"}"
```

证明：**负路径真实提交 + 服务端响应捕获 + 期望拒绝断言，这条链路技术上完全跑得通**。
缺的只是**把它模板化**，让平台自动生成，而不是每次手写。

手写脚本（`demo2_script.ts`）中的可复用原语：

| 原语 | 作用 | 现状 |
|---|---|---|
| 写隔离 route 白名单 | 只放行目标写请求，其余 abort | 平台已有（`non_upload_api_blocked`） |
| `submitResp` 响应捕获 | 抓 `requestId` + 响应体 | 平台已有（`netLog`），但**未提取 requestId** |
| 多步交互（选择 → 确认 → 追加） | 构造冲突前置条件 | **平台无** |
| 期望拒绝断言 | `code !== '0'` 且 `msg` 含关键词 | **平台无**（只判字段级） |
| 还原（改动后恢复数据） | 避免污染 UAT | **平台无** |

---

## 二、设计方案

### 2.1 核心思路：把"负路径"从字段级扩展到规则级

引入**负路径场景（`NegativeScenario`）**概念——一个声明式的场景描述，由渲染器展开成可执行的 TS 步骤。

```python
@dataclass(frozen=True)
class NegativeScenario:
    """一个业务规则级负路径场景（声明式）。"""

    scenario_id: str                    # 场景标识，用于断言归属与去重
    rule_ref: str                       # 对应的需求规则（如「同一商品仅能属于一条活动」）
    target_entry: str                   # 目标入口：edit | create | add | custom
    # --- 前置条件构造 ---
    setup: list[SetupStep] = field(default_factory=list)
    # --- 提交与期望 ---
    submit_label: list[str] = field(default_factory=list)   # 提交按钮文本候选
    expect_reject: RejectExpect = ...   # 期望拒绝的判定条件
    # --- 还原 ---
    restore: RestorePolicy = ...
```

```python
@dataclass(frozen=True)
class SetupStep:
    """构造前置条件的一步（业务无关的动作原语）。"""
    action: str                         # click | select_row | append | fill
    labels: list[str] = field(default_factory=list)   # 元素文本候选
    selector: str = ""                  # 可选精确选择器
    row_index: int = 1                  # select_row 用（0 通常是全选框）
```

```python
@dataclass(frozen=True)
class RejectExpect:
    """期望服务端拒绝的判定条件。"""
    http_ok: bool = True                # HTTP 应为 200（信封式响应）
    result_code_not: str = "0"          # resultCode 不得等于该值
    message_contains: list[str] = field(default_factory=list)   # 响应文案候选关键词
    api_path_hint: str = ""             # 捕获响应的 URL 子串（如 "/v1/purchaseLimit/modify"）
```

### 2.2 与现有负路径层的关系

**不新建第二套提交机制**。`NegativeScenario` 在渲染时被展开为现有的 `submitQueue` 条目，复用已有的：

- `clickSubmit()` 按钮定位逻辑；
- `onNetResp` / `netLog` 响应捕获；
- 写隔离网络层。

新增的只是：

1. **`kind: "rule_reject"`** 的队列条目类型；
2. **`setup` 步骤的执行序列**（在提交前）；
3. **`restore` 还原序列**（在提交后）；
4. **`requestId` 提取**（现有 `netLog` 只存 url/status/body，未提 header）。

### 2.3 配置位置

建议放在 **P6 已建好的注册体系**里，作为 `extra_config` 的一个键：

```json
{
  "template_key": "purchase_limit",
  "fields": { ... },
  "extra_config": {
    "form_button": ["新建"],
    "modal": ".ant-drawer",
    "negative_scenarios": [
      {
        "scenario_id": "NEG-DUP-GOODS-EDIT",
        "rule_ref": "同一商品仅能属于一条活动",
        "target_entry": "edit",
        "setup": [
          {"action": "click", "labels": ["编辑"]},
          {"action": "click", "labels": ["选择限购商品"]},
          {"action": "select_row", "row_index": 1},
          {"action": "click", "labels": ["确 认", "确认"]}
        ],
        "submit_label": ["提 交", "提交"],
        "expect_reject": {
          "result_code_not": "0",
          "message_contains": ["已新建", "已存在", "重复"],
          "api_path_hint": "/v1/purchaseLimit/modify"
        },
        "restore": {"policy": "reload_without_submit"}
      }
    ]
  }
}
```

**这样做的三个好处**：

1. 复用 P6 的注册表、API、优先级链——**不新增表**；
2. 场景随模板走，新业务接入时一并配置；
3. 与 P0-2 的 `WebScriptProfile` 天然衔接（后者管"骨架"，前者管"负路径场景"）。

> ★ 上面这段 JSON 是**设计原稿**。实施时补上了 `allow`（写隔离放行规则）——
> 原稿把 `api_path_hint` 当成"响应匹配 + 写隔离放行"两用，实测发现两者语义不同
> （详见 §三 阶段 B 的实施记录）。可直接使用的配置见下面的"配置样例（可直接注册）"。

**配置样例（可直接注册到 `web_form_templates.extra_config`）**：

```json
{
  "form_button": ["新建"],
  "modal": ".ant-drawer",
  "negative_scenarios": [
    {
      "scenario_id": "NEG-DUP-GOODS-EDIT",
      "rule_ref": "同一商品仅能属于一条活动",
      "target_entry": "edit",
      "case_id": "TC-200998",
      "rule_id": "FR-PL-004",
      "setup": [
        {"action": "click", "labels": ["编辑"]},
        {"action": "wait", "ms": 3000},
        {"action": "click", "labels": ["选择限购商品"]},
        {"action": "select_row", "row_index": 1},
        {"action": "click", "labels": ["确 认", "确认"]}
      ],
      "submit_label": ["提 交", "提交"],
      "expect_reject": {
        "http_ok": true,
        "result_code_not": "0",
        "message_contains": ["已新建", "已存在", "重复"],
        "api_path_hint": "/v1/purchaseLimit/modify",
        "allow": {
          "host_suffix": "imarketing-uat.xysjg.com",
          "path_prefix": "/yundt-saas-gateway/api/mini-mgmt-app/v1/purchaseLimit/modify",
          "methods": ["PUT"]
        }
      }
    }
  ]
}
```

字段速查：

| 字段 | 必填 | 说明 |
|---|---|---|
| `scenario_id` | ✔ | 场景唯一标识（同模板内不得重复） |
| `rule_ref` | | 被验证的业务规则名，会进证据文案 |
| `target_entry` | | `edit` / `create` / `add` / `custom`（审计标注，平台不据此猜步骤） |
| `case_id` / `rule_id` | | 结论归属到哪条用例 / 需求规则；留空则只输出证据 |
| `setup[].action` | ✔ | `click` / `select_row` / `append` / `fill` / `wait`（闭集，其它一律拒绝） |
| `setup[].labels` | | `click` / `append` 的文本候选（按序尝试：菜单项 → 按钮 → 链接 → 任意文本） |
| `setup[].row_index` | | `select_row` 用；第 0 个通常是"全选"，**由配置自己数**，平台不加一 |
| `setup[].field` / `value` | | `fill` 用；`field` 必须能在定位器表里找到 |
| `expect_reject.api_path_hint` | ✔ | 匹配服务端响应用的 URL **子串** |
| `expect_reject.result_code_not` | | 信封式响应的"成功码"，默认 `"0"` |
| `expect_reject.message_contains` | | 驳回文案候选关键词；给定时**必须命中**才算 `pass` |
| `expect_reject.allow.path_prefix` | | 写隔离放行用的**绝对路径前缀**，默认取 `api_path_hint` |
| `expect_reject.allow.host_suffix` | | 写隔离放行用的主机后缀，默认当前页面 host |
| `expect_reject.allow.methods` | | 默认 `["POST","PUT","PATCH"]` |

### 2.4 与 P0-2 的边界

| 关注点 | P0-2 `WebScriptProfile` | P1-3 `NegativeScenario` |
|---|---|---|
| 层次 | 脚本骨架（登录/入口/导航/列表断言） | 表单内的负路径场景 |
| 载体 | `web_script_profiles` 表（建议新增） | `web_form_templates.extra_config`（已有表） |
| 变更频率 | 新业务接入时 | 发现新缺陷模式时 |
| 是否可独立实施 | 是 | **是**（不依赖 P0-2） |

**P1-3 可以独立于 P0-2 实施**——因为它挂在表单定位器注册表上，而那张表已经建好了。

---

## 三、实施步骤

### 阶段 A：补齐 requestId 提取（最小改动，立即有价值）—— ✅ 已完成

现有 `netLog` 记录了 `{tk, url, status, body}`，但**没有 requestId**。而 requestId 是缺陷单必须的字段（DEFECT-001 的工单里就有 `requestId: 0A000578-MUM4VRA6-5657`）。

- 在 `onNetResp` 里增加 header 读取：`r.headers()['requestid'] || r.headers()['x-request-id']`；
- 落进 `netLog` 条目；
- **验收标准：负路径失败时，证据里能直接看到 requestId。**

**实施记录（2026-09-30）：**

改动点三处（均在 `web_case_compiler.py` 的 `_FORM_EXECUTOR_TS` 常量内）：

| 位置 | 改动 |
|---|---|
| 采集层 | `netLog` 类型增 `requestId: string`；新增 `pickRequestId(resp)` 按 `requestid / x-request-id / request-id / traceid / x-trace-id` 顺序取第一个非空值；两处 `push` 均带 `requestId` |
| 消费点 1 | `summarize()` 输出 `requestId:${n.requestId \|\| '-'}`（证据主链路） |
| 消费点 2 | 「坐实缺陷」分支额外追加 `定位凭据：requestId=${accRid}`，取不到时显式写 `未在响应头暴露`（不伪造） |
| 消费点 3 | `NET_DEBUG` 的 `allNet` 映射增 `r: n.requestId`（零捕获时的地面真相） |

**★ 重要踩坑（务必记住）：`_FORM_EXECUTOR_TS` 是 raw string 常量，不是 f-string。**

该常量的渲染方式是 `_FORM_EXECUTOR_TS.replace("@@FORM_PLAN@@", payload)`，**没有经过 f-string 求值**。
因此：

| 写法 | 在 f-string 模板中 | 在 `_FORM_EXECUTOR_TS`（raw string + replace）中 |
|---|---|---|
| `{{` | 渲染为 `{` ✅ | **原样输出 `{{` → TS 语法错误** ❌ |
| `{` | 被当作占位符 → `KeyError`/`ValueError` ❌ | 原样输出 `{` ✅ |

本次初版误按 f-string 规则写了 `{{`，导致产物出现 **6 处 TS1xxx 语法错误**
（TS1131 / TS1005 / TS1109×2 / TS1128），**整个脚本无法解析**。已全部改为单花括号。

**验证方法与结果（实测，非推断）：**

1. 渲染层：`render_form_executor(plan)` 产出 85,494 字符
2. 语法层：产物包入 stub 后过 `tsc --noEmit`
   - 修复前：**TS1xxx = 6 处**（脚本不可解析）
   - 修复后：**TS1xxx = 0 处**
3. 回归层：`pytest backend/tests/test_web_case_compiler.py` 等 6 个文件 → **149 passed**
4. 证据层：产物中出现
   `疑似缺陷坐实：服务端受理非法请求 HTTP 200 /v1/purchaseLimit/modify requestId:a1b2c3d4-… body:{"resultCode":0,…}（约束「range[0,9999]」未生效）；…定位凭据：requestId=a1b2c3d4-…`

> 教训：**改这个文件前先确认目标常量是 f-string 还是 raw string**。
> 判断方法：看它被谁渲染——`.replace(...)` 或直接返回 → raw string，用单花括号；
> 若在 f-string 内部（`f"""...{var}..."""`）→ 用双花括号。同文件内两种写法并存，容易搞混。


### 阶段 B：引入 `rule_reject` 队列类型 —— ✅ 已完成

- 定义 `NegativeScenario` / `SetupStep` / `RejectExpect`；
- 渲染器支持在 `submitQueue` 中插入 `kind: "rule_reject"` 条目；
- 执行器按 `setup` → `submit` → `assert` 顺序执行；
- **验收标准：用手写的 DEFECT-001 场景配置，跑出与 `demo2_script.ts` 等价的结论。**

**实施记录（2026-09-30）：**

#### 与本文案的一处**有意偏离**（先记录，避免后来者以为是漏做）

文案写的是"在 `submitQueue` 中插入 `kind: "rule_reject"` 条目"。实现改为**独立阶段**
`runNegativeScenarios()`，理由：现有那个 `for` 循环的每一步都围绕 `check.field`
（复位该字段、定位它的输入框、兜底填充时跳过它）；规则级场景**没有单一字段**，
硬塞进去要在循环内加约十处 `if (item.kind === 'rule_reject')` 分叉，
很容易破坏已覆盖 149 个用例的既有路径。

改为**复用它的原语**（`clickSubmit` 的按钮定位思路 / `netLog` 式响应捕获 /
`isolationDecision` 写隔离），但不复用它的循环体。功能等价，回归面更小。

#### 改动点

| 位置 | 改动 |
|---|---|
| `web_case_compiler.py` 计划层 | 新增 `SETUP_ACTIONS` / `NEGATIVE_TARGET_ENTRIES` / `SetupStep` / `RejectExpect` / `WriteAllow` / `NegativeScenario` / `parse_negative_scenarios` |
| `build_form_plan` | 解析 `locators["negative_scenarios"]`；计划新增 `negative_scenarios` + `negative_scenario_rejects`；**只有场景、没有字段级检查时也返回 `executable=True`**（否则 DEFECT-001 这类"没有字段断言"的规则永远跑不到） |
| `render_form_executor` | payload 下发 `negative_scenarios` / `negative_scenario_rejects` |
| 执行器新增段 | `runNegativeScenarios()`：配置期被拒场景打 `[negative-skip]` → 写隔离（fail-closed）→ 逐场景 `setup` → `submit` → 只按**服务端响应**判定 → `NEGATIVE_PHASE` 计数 |
| `target_entry` 的唯一行为差异 | `create` = "就用平台已打开的新建表单"，执行器**不**在进场景前关掉它；`edit` / `add` / `custom` 会先关（它们的 setup 通常从列表页自己的入口重进） |
| `submitQueue` 排序 | **未改**（`rule_reject` 不进该队列，见上） |
| `summary` | 新增 `negative_scenario_phase`（与字段级 `submit_phase` 分开计数） |
| 预览接口 | `negative_scenarios` / `negative_scenario_rejects` / `negative_scenario_count`；并把 `negative_scenarios` 从 `locator_keys` 剔除（新增 `NON_LOCATOR_CONFIG_KEYS`） |

#### ★ 实施中发现并修掉的**真实设计缺陷**：`api_path_hint` ≠ 放行规则

文案 §2.1 把 `api_path_hint` 描述成"捕获响应的 URL 子串"，同时又期望它充当
写隔离白名单。两者**语义不同**：

| 用途 | 匹配语义 | DEFECT-001 的真实值 |
|---|---|---|
| 响应匹配 | URL **子串** | `/v1/purchaseLimit/modify` |
| 写隔离放行 | host **后缀** + path **前缀** | host `imarketing-uat.xysjg.com`、path `/yundt-saas-gateway/api/mini-mgmt-app/v1/purchaseLimit/modify` |

真实请求打在**网关主机**上（不是页面 host），路径还带网关前缀 ——
早先把 `hint` 直接当路径前缀，会让真实提交**永远被阻断**（场景只能产出 `unverified`）。
现改为显式声明 `expect_reject.allow{host_suffix, path_prefix, methods}`，
缺省才退化为"同源直连、路径等于 hint"；`path_prefix` 不是绝对路径时**计划期即拒绝**
（fail-loud，而不是产出一个永远跑不出结论的场景）。

#### 判定口径（三条出口，只看服务端响应）

| 服务端行为 | 结论 | 文案 |
|---|---|---|
| `resultCode == result_code_not`（默认 `0`）且 HTTP 2xx | `blocked` | `疑似缺陷坐实：服务端受理了应被拒绝的请求 —— 规则「…」未生效；HTTP … requestId:… body:…；定位凭据：requestId=…` |
| 已拒绝，且 `message_contains` 命中 | `pass` | `服务端按规则拒绝：判据 resultCode=1≠0；文案命中「…」` |
| 已拒绝，但文案未命中（如"无权限"） | `unverified` | `服务端已拒绝，但驳回文案未命中期望关键词 … 无法确认为本规则生效` |
| 未捕获到目标接口响应 | `unverified` | 区分"请求已发出无响应" / "写隔离阻断" / "窗口内无请求" |
| 前置条件构造失败 | `unverified` | 带具体失败步骤（哪个 setup 步骤、哪个 label 找不到） |

`blocked` 沿用既有口径 → 末尾 `expect(summary.blocked).toBe(0)` **硬失败**，
与 `demo2_script.ts` 的 `expect.soft(false, '[待确认] …')` 等价（且不会静默）。

#### 验收结果（实测，非推断）

1. **配置层**：`backend/tests/test_web_negative_scenarios_plan.py` → **27 passed**
2. **浏览器级**：`backend/tests/test_web_negative_scenario_browser_local.py` → **11 passed**
   （两轮：5 条出口一轮 + `target_entry="create"` 单独一轮）
   - 用受控页面复刻"编辑 → 追加被占用商品 → 提交"，把**平台渲染的执行器产物原样**放进去跑；
   - 5 条出口全部走到明确结论：`blocked 1 / pass 1 / unverified 3`；
   - `NEG-ACCEPT` 的实测输出（= `demo2_script.ts` 的等价结论）：
     `疑似缺陷坐实：服务端受理了应被拒绝的请求 —— 规则「同一商品仅能属于一条活动」未生效；
      HTTP 200 http://127.0.0.1:…/v1/purchaseLimit/modify requestId:0A000578-MUM4VRA6-5657
      body:{"resultCode": "0", "resultMsg": "success"}；…定位凭据：requestId=0A000578-MUM4VRA6-5657`
   - **写隔离收窄的硬证据**：故意让页面去 PUT 一个**未被任何场景声明**的接口，
     断言服务端 `received` 里**零请求**；整轮只到 3 个写请求（modify/add/approve）。
   - `create` 语义：场景 `setup` 为空、直接就用平台已打开的新建表单 → 必须 `pass`；
     一旦执行器多此一举把它关掉，就会退化成"未找到提交按钮"（实测变异验证如此）。
3. **语法层**：渲染产物包入 stub 过 `tsc --noEmit` → **TS1xxx = 0**（无语法错误）
4. **变异验证**：关掉 6 处核心判定（含 `create` 语义）→ 新测试如期失败、既有测试全绿

    | 变异 | 被哪些新测试捕获 |
    |---|---|
    | M1 永不坐实缺陷（`blocked` 出口失效） | `test_server_accepted_conflict_is_pinned_as_a_defect` 等 3 条 |
    | M2 写隔离全放行（收窄失效） | `test_undeclared_endpoint_never_reaches_the_server` 等 3 条 |
    | M3 不再校验 `api_path_hint` 必填 | `test_missing_api_path_hint_is_rejected` |
    | M4 文案不匹配也判 `pass` | `test_rejected_for_a_different_reason_is_not_a_pass` 等 2 条 |
    | M5 `setup` 失败仍继续提交 | `test_setup_failure_does_not_submit` |
    | M6 `closeAnyForm` 无条件调用 | `test_create_entry_scenario_uses_the_already_open_form` |

    每次变异下**既有测试（88 条）保持全绿** → 旧套件对这些行为确实无感，
    新测试是唯一防线。

### 阶段 C：还原策略

- 实现 `restore` 策略（至少支持 `reload_without_submit`：提交后不保存直接刷新）；
- 记录还原结果到证据区；
- **验收标准：负路径场景执行后，UAT 数据无残留改动。**

### 阶段 D：场景化验证

- 用 DEFECT-001 的真实场景端到端验证；
- 补充 1~2 个其他业务规则级场景，验证 schema 通用性。

---

## 四、风险与对策

| 风险 | 说明 | 对策 |
|---|---|---|
| **污染 UAT 数据** | 规则级负路径需要真实提交，若服务端未拒绝则会写入 | `restore` 策略 + 写隔离白名单收窄到单接口；参照现有 `isolation_blocked` 三类计数 |
| **前置条件不稳定** | "被占用的商品"可能被其他测试改动 | setup 步骤支持断言前置状态；前置不成立时显式 `blocked`，不静默通过 |
| **场景配置表达能力不足** | 复杂多步交互无法用 `SetupStep` 表达 | 与 P0-2 一致的 `hooks` 逃生舱思路 |
| **误判为缺陷** | 服务端返回非 0 但原因不同（如权限） | `expect_reject` 必须同时校验 `result_code_not` **和** `message_contains`；参照 P5 `assertEnvelope` 的思路 |
| **执行时长** | 多步交互 + 真实提交远超只读探针 | 沿用现有提交层限时预算机制（`:2142` 已有 5 分钟预算） |

---

## 五、边界声明

1. **不重做现有负路径层**——字段级负路径已成熟，本文案只做**扩展**；
2. **不新增数据表**——复用 `web_form_templates.extra_config`；
3. **不阻塞 P0-2**——可独立实施；
4. **不含小程序**——小程序无执行链路（`ExecutorRegistry` 仅 `api_test`/`scenario`/`web_test`），暂缓；
5. **本文案不改代码**。

---

## 六、预期收益

按 2026-09-30 的 7 个缺陷归因分析，"执行形态无法发现"（B 类）占 2~3 条。其中：

- **D2944 编辑追加重复商品**（= DEFECT-001）→ 本文案**直接覆盖**；
- **D2957 门槛小数**（TC-424003 存在但只读探针）→ 本文案**间接改善**（`probe` 探针值机制可复用）。

即：**P1-3 落地后，B 类缺陷中至少 1 条（最严重的入口不一致）可以被自动执行发现。**
