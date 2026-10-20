# 分页契约断言平台接线设计（P5 / DEFECT-002）

> 状态：**阶段 A/B/C/E 已实施并验收（2026-09-30）；阶段 D 待业务确认**
> 日期：2026-09-30
> 范围：API 侧用例→脚本链路（`api_case_link_service.py`）+ P5 契约断言接入
> 关联：DEFECT-002（分页接口越界返回 `data:null`）

---

## 一、问题陈述（基于实测）

### 1.1 DEFECT-002 现状

`backend/workspace/api/tests/p5_pagination_contract_matrix.spec.ts`（6993 B，2026-09-30 14:55）已实测 9 个接口：

| 接口 | provenance | 越界页期望 | 实测结论 |
|---|---|---|---|
| `GET /v1/purchaseLimit/list` | `ui_observed` | `known_issue` | 正常页 list=9/total=9/pages=1；**越界返回 `data:null`** |
| `GET /v1/purchase/list` | `ui_observed` | `known_issue` | 正常页 list=8/total=8/pages=1；**越界 `data:null`** |
| `GET /v1/common/getCloudGoods` | `generic_params` | `known_issue` | 正常页 list=10/total=134/pages=14；**越界 `data:null`** |
| `GET /v1/common/getPayMember` | `generic_params` | `known_issue` | 正常页 list=2/total=2/pages=1；**越界 `data:null`** |
| `GET /v1/common/getRecommendGoods` | `generic_params` | `known_issue` | 正常页 list=10/total=123/pages=13；**越界 `data:null`** |
| `GET /v1/store/binding/list` | `generic_params` | `structure` | **参照组：越界仍返回 `list=[]` 且 total=413/pages=42 正确** |
| `GET /v1/notice/page` | `generic_params` | `structure` | **参照组：越界仍返回 `list=[]`** |
| `GET /v1/recommend/list` | `unverified` | `structure` | **`pageNum`/`pageSize` 未生效（响应 `pageNum=0/pageSize=0`）** |
| `GET /v1/slide/page` | `unverified` | `structure` | **同上，参数未生效** |

**三个独立结论**：

1. **5 个接口确认 `data:null`**（DEFECT-002 主体）；
2. **2 个参照组接口表现正确** → 反向证明这是 SUT 实现不一致，**不是平台断言能力不足**；
3. **2 个接口分页参数完全未生效** —— 比 `data:null` 更隐蔽（用户提的"其余 4 个分页接口"应含此类）。

### 1.2 断言能力的核心缺口（实测）

`api_case_link_service._generate_playwright_script()`（`:511`）生成的脚本**只断言 HTTP 状态码**：

```typescript
await test.step(`断言：HTTP 响应状态为 2xx（实际：${status}）`, async () => {
  expect(status).toBeGreaterThanOrEqual(200);
  expect(status).toBeLessThan(300);
});
```

**问题**：小杨生煎的 SUT 是**信封式响应设计**——无论业务成功失败，HTTP 恒为 200，靠响应体 `resultCode` 区分（实测：过期令牌也返回 `{"resultCode":401,...}` + HTTP 200）。

因此这个断言**对业务结果恒真**，等于没有断言。DEFECT-002 这类"HTTP 200 但数据结构错误"的缺陷，在平台自动执行下**必然漏检**。

### 1.3 P5 spec 游离在平台体系外（实测）

两个证据：

1. 平台执行器只生成**按运行 ID 命名**的脚本：
   - `api_endpoint_script_executor.py:195` → `sr_{run_id}_{uuid4().hex[:8]}.spec.ts`
   - `web_case_link_service.py:1044/1150/1241` → `{uuid4().hex}/test-script.spec.ts`
2. **全仓库无任何代码引用** `api/tests/p5_*.spec.ts`

**结论**：P5 的契约断言能力已建成、证据已固化，但**只能靠 CI/命令行手动跑**，平台自动执行发现不了 DEFECT-002 的回归。

### 1.4 接线抓手（已确认）

`api_case_link_service.py` 是 API 侧的"用例 → 脚本"链接服务（与 Web 侧 `web_case_link_service.py` 对称）：

| 要素 | 位置 | 说明 |
|---|---|---|
| 脚本生成 | `_generate_playwright_script(endpoint)` :511 | **唯一**的脚本生成函数 |
| 资产创建 | `_create_api_test_asset()` :331 | 创建 `APITest` + 写 MinIO + 建 Attachment |
| 模型关联 | `APITest.test_case_id` :60 | 脚本可关联用例 |
| 脚本存储 | `APITest.script_path`（MinIO）+ `script_format: playwright` | |
| 执行 | `api_endpoint_script_executor.py:64` 从 MinIO 下载后 `npx playwright test` | |

**结论：接线的唯一改动点是 `_generate_playwright_script`——把契约断言注入生成的脚本。**

---

## 二、设计方案

### 2.1 核心改动：从"HTTP 状态断言"升级为"信封 + 契约断言"

生成的脚本断言分三层（对齐 `response_contract.ts` 已有的三态语义）：

```
第 1 层 信封断言（ENVELOPE）  —— 防"认证问题被误报成业务缺陷"
   HTTP 200 且 resultCode 字段存在
   resultCode !== '0' → 抛 AUTH_OR_BUSINESS_ENVELOPE（不判定为契约失败）

第 2 层 正常页契约断言（CONTRACT）
   data 是对象 && list 是数组 && total/pages 是数字

第 3 层 越界页行为断言（OUT_OF_RANGE）
   越界时仍须是完整空结构（list=[]、total/pages 为数字）
   若非期望 → 绑 known_issue（不静默通过，也不谎报失败）
```

**关键：第 1 层是本次必须补的**。P5 的 `assertEnvelope()` 已经实现了这个逻辑（`sample.spec.ts:47-60`），且注释明确记录了动机——**这是已被验证的正确设计，应直接复用到生成器**。

### 2.2 契约配置数据化

分页契约的差异（哪些接口要求 `structure`、哪些是 `known_issue`、参数出处）**不应写死在生成器里**，而应数据化——与 P6 的思路一致。

建议新增配置（可复用 `api_contract_profiles.py` 的既有模式）：

```python
@dataclass(frozen=True)
class PaginationContract:
    """一个接口的分页契约声明。"""

    endpoint_path: str                  # 如 "/v1/purchaseLimit/list"
    provenance: str                     # ui_observed | generic_params | unverified
    expect_out_of_range: str            # structure | known_issue
    known_issue_id: str = ""            # known_issue 时必填（如 "DEFECT-002"）
    known_issue_until: str = ""         # 有效期 YYYY-MM-DD
    page_param_names: tuple[str, str] = ("pageNum", "pageSize")
    note: str = ""
```

`provenance="unverified"` 的接口（`recommend/list`、`slide/page`）应**显式 skip**，不静默通过——这是 P5 矩阵已有的正确做法（`matrix.spec.ts:88`）。

### 2.3 known_issue 三态语义（直接复用）

`response_contract.ts` 已实现严格语义，**接线时应原样复用**：

- `ok` / `knownIssue` / `failures` 三态，`knownIssue` **既不是通过也不是普通失败**；
- 票据为空则例外失效（`knownIssueActive` 检查 `String(known.id||'').trim()`）；
- `until` 过期后自动转普通失败；
- **例外也 `throw`**（`reportContractResult`），保证失败不被静默吞掉。

### 2.4 接线方式

**方案 A（推荐）：在生成器内注入契约断言**

`_generate_playwright_script(endpoint)` 按 `endpoint.path` 查 `PaginationContract` 配置：

- 查到 → 生成含三层断言的脚本；
- 查不到 → 保持现状（HTTP 状态断言）**但增加 `resultCode` 信封检查**（防止"认证问题误报成通过"）。

优点：改动集中在一个函数，与现有链路零冲突。
缺点：生成器复杂度上升（需引入契约断言模板）。

**方案 B（备选）：把 P5 spec 注册为 APITest 资产**

把 `p5_pagination_contract_matrix.spec.ts` 的内容通过 `_create_api_test_asset` 类似路径写入 MinIO，`test_case_id` 关联到分页相关用例。

优点：P5 spec 已在本地验证过，风险低。
缺点：`spec.ts` 依赖 `_lib/response_contract.ts`（Playwright 项目内相对导入），而执行器把脚本写到 `workspace/tests/` 根下——**相对路径会断**。需一并处理 `_lib` 的分发。

**建议：先做方案 A（把契约逻辑内联进生成脚本，不依赖外部 `_lib`），再评估方案 B。**

---

## 三、实施步骤

### 阶段 A：补齐信封断言（最小、最高价值）

- `_generate_playwright_script` 生成的脚本增加 `resultCode` 存在性 + 非 0 判定；
- **验收标准：用过期令牌跑，脚本报 `AUTH_OR_BUSINESS_ENVELOPE` 而非"通过"或"契约失败"。**

这一步独立成立，**即使不做契约断言也有价值**——它堵住了"认证失败被误报为通过"的漏洞。

**实施（2026-09-30）**：已落地。三条断言（信封可解析 → 含 `resultCode` → `resultCode === '0'`）。
验收：`test_api_pagination_contract_e2e.py::TestExpiredCredential`（本地信封式服务端 + 平台渲染脚本原样跑，
过期令牌下退出码非 0、报 `AUTH_OR_BUSINESS_ENVELOPE`、且**不**出现 `CONTRACT_FAILURE`）。

### 阶段 B：分页契约断言（对齐 DEFECT-002）

- 新增 `PaginationContract` 配置 + 内联断言模板（含三层）；
- 先只覆盖 5 个已确认接口；
- **验收标准：跑出的结论与 `p5_pagination_contract_matrix.spec.ts` 手跑一致**（`data:null` 被标为 `known_issue` 而非通过）。

**实施（2026-09-30）**：已落地。

| 要素 | 落点 |
|---|---|
| 契约配置 | `app/services/api_pagination_contracts.py`（9 条，与手跑矩阵逐行同源） |
| 判定逻辑 | **按标记抽取** `workspace/api/_lib/response_contract.ts` 的 `RESPONSE_CONTRACT_CORE` 段内联，**不另写一份** |
| 注入点 | `api_case_link_service._render_pagination_block`（`_generate_playwright_script` 内唯一改动点，即方案 A） |
| 出口 | 脚本打 `P5_CONTRACT {...}` 机读行（`stage=normal_page / out_of_range`） |

- 验收：`test_api_pagination_contract_wiring.py` **27 passed**（含"配置 vs 手跑矩阵逐行比对"、
  产物过 `tsc --noEmit`、抽取段在 node 里执行结果一致）；`test_api_pagination_contract_e2e.py` **11 passed**；
  执行器环境映射另有 4 条落在 `test_endpoint_executor_env.py`（7 → 11）。
- 变异验证 **5/5** 如期被捕获（关掉注入 / 忽略例外 / 越界页不判信封 / unverified 不跳过 / 正常页不断言）。

### 阶段 C：known_issue 生命周期

- 实现 `until` 过期 → 转普通失败；
- 缺陷修复后自动转绿（无需人工清理例外标记）；
- **验收标准：模拟 `until` 过期，断言从 `known_issue` 转为失败。**

**实施（2026-09-30）**：随阶段 B 一并达成（复用 `response_contract.ts` 的 `knownIssueActive`，
不重写）。配置期强制 `known_issue` 必须带 `known_issue_id` **且** `known_issue_until`，
否则导入即报错——避免产生"没人知道何时撤掉"的永久豁免。

验收两条：`TestKnownIssueExpiry`（例外的 `until` 改为过去日期 → 转普通失败、不再出现 `KNOWN_ISSUE`）、
`TestFixTurnsGreenAutomatically`（越界返回完整空结构 → 直接通过，无需动例外标记）。

### 阶段 D：未生效参数独立立项

- `recommend/list`、`slide/page` 的"参数未生效"应作为**独立缺陷候选**确认；
- 确认后登记 `known_issue` 或新缺陷单；
- **验收标准：这两个接口从 `unverified`（skip）转为有明确结论。**

**状态（2026-09-30）**：**未做**。这两个接口仍为 `provenance="unverified"` → 生成脚本显式
`test.skip(true, ...)`，且**不发出任何请求**（有测试锁住"服务端零请求"）。
定性为独立缺陷需要业务确认，不在本次范围。

### 阶段 E：平台回归接线

- 确认方案 A/B；
- 把契约断言纳入平台自动执行（`APITestRun`）；
- **验收标准：DEFECT-002 在平台自动执行中被发现/标记，无需手工跑 spec。**

**实施（2026-09-30）**：方案 **A**（生成器内注入，不依赖外部 `_lib` 分发）。
链路本已贯通（`_create_api_test_asset` → MinIO → `api_endpoint_script_executor`），
但发现一处**必要缺口**并补齐：

> `_build_subprocess_env` 没有把 `execution_config["base_url"]` 映射成脚本读的
> `API_BASE_URL`。无 `data_profile` 时脚本会回退到模板里的 `http://localhost:8000` 占位 ——
> 断言打的是本机，报告里的"契约失败"看着像 SUT 问题，实为归因错向。
> `runner.py` / `script_execution_tools.py` 早已补了这一手，端点执行器此前遗漏。
> 见 `test_endpoint_executor_env.py` 的 4 条新增用例。

**边界**：链路与结论已在**本地受控服务端**端到端验证；真实 UAT 执行（需 `TRADE_TICKET` 等凭据）
尚未跑，留待环境就绪。

---

## 四、风险与对策

| 风险 | 说明 | 对策 |
|---|---|---|
| **相对导入断裂** | P5 spec 依赖 `_lib/response_contract.ts`，执行器写到 `tests/` 根下 | 优先方案 A（内联，无外部依赖）；若走方案 B，需一并分发 `_lib` |
| **契约配置漂移** | 接口新增/变更后配置未更新 | `provenance="unverified"` 强制 skip；未配置接口保留基础信封断言 |
| **误报为契约失败** | 认证/权限问题被当成契约问题 | 第 1 层信封断言（已有正确实现可复用） |
| **越界页信封判定过弱**（实测踩到） | 初版只判 `resultCode` **是否存在**（`not.toBe('')`）。越界页返回 401 时该字段是存在的，于是照样落到 `data:null`，被 `known_issue` 吸收成"已知问题" —— **用 DEFECT-002 掩盖另一个问题** | 越界页必须判 `resultCode === '0'`；由变异验证揪出（关掉该判定时测试**没有**失败，说明覆盖不到），补 `TestOutOfRangeEnvelope` 后复验通过 |
| **环境变量语义分叉**（实测踩到） | 执行配置存的键是 `base_url`，脚本读的是 `API_BASE_URL`；不映射则回退 `http://localhost:8000` 占位，断言打本机 → 归因错向 | 在 `_build_subprocess_env` 补齐映射（与 `runner.py` / `script_execution_tools.py` 一致） |
| **known_issue 变成永久豁免** | 例外标记加了不清理 | `until` 必填 + 到期自动失效（`response_contract.ts` 已实现） |
| **接口清单不全** | 只测了 9 个，SUT 可能有更多分页接口 | 矩阵设计为**数据表**，新增接口 = 加一行；`generic_params` 已覆盖"未从 UI 观察到的" |

---

## 五、边界声明

1. **不重写 `response_contract.ts`**——其逻辑已验证正确，复用它；
2. **不改 `web_*` 链路**——P5 是 API 侧议题；
3. **不含小程序**——`ExecutorRegistry` 无小程序执行器，暂缓；
4. **本文案原为"不改代码"的设计稿；阶段 A/B/C/E 已按其方案实施**（见各阶段"实施"小节）。
   实现与本文案的两处**有意收紧**（越界页判 `resultCode === '0'`、配置期强制 `known_issue` 必须带
   票据与有效期）已记入 §四风险表——它们是实施中被测试/变异验证逼出来的，不是原设计。
5. **`recommend/list`、`slide/page` 的参数问题**先确认为独立缺陷，不在本文案内定性。

---

## 六、预期收益

| 收益 | 说明 |
|---|---|
| **堵住恒真断言** | 现状"HTTP 2xx"对信封式 SUT 恒真，改后能识别业务失败 |
| **DEFECT-002 可回归** | 5 个接口的越界行为纳入自动执行，修复后自动转绿 |
| **认证问题不再误判** | 信封断言防止 401 被当成契约缺陷或错误通过 |
| **参照组佐证可复用** | 2 个正确接口作为对照，证明是 SUT 不一致而非平台能力问题 |
| **未生效参数浮出水面** | `recommend/list`、`slide/page` 从"未测"变为有明确结论 |

---

## 七、与 P0-2 / P1-3 的关系

| 项 | 关注点 | 依赖 |
|---|---|---|
| **P0-2** | Web 脚本骨架配置化 | 无（独立） |
| **P1-3** | Web 负路径规则级扩展 | 无（独立，挂 P6 表） |
| **P5（本文案）** | API 契约断言接入平台 | 无（独立） |

**三项可并行实施，互不阻塞。** 共用同一条设计原则：**能力数据化 + 断言可证伪 + 不静默通过**。
