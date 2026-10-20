# API 覆盖度评审（设计后 / 执行前）— 交付说明

本次交付把《三层覆盖度模型方案》的**第一阶段**真正落到 `ai-test-agent-system-platform` 的代码上，
并顺带修掉了复核中发现的 4 个**阻断级**既有缺陷。

> 说明：交付前对方案做了客观复核，发现方案的"现有框架已具备能力"前提有 3 项事实错误
> （`api-test-quality`、`test-scenario-design` 属另一个仓库 `ai-test-agent-system`；
> `testcase_quality_metrics` 两个仓库都不存在）。因此本次**不是**照搬方案，而是按本仓库实际情况重排优先级实现。

---

## 一、交付内容

### P0：先修装配链路（不修这些，覆盖度评审全是空转）

| 编号 | 问题 | 修复 |
|---|---|---|
| P0-1 | **Testcase Agent 运行时加载 0 个 skill**：`testcase_skills_root` 是相对路径且与 `sources` 约定不符，`cd backend` 启动时被解析成 `<repo>/backend/backend/workspace/testcase`（不存在） | 按统一约定修正：`testcase_skills_root = .claude/skills`，`sources = ["/skills/testcase/"]`；移除恒为空的 `/skills/rag/`（本仓库无 rag skill）。**结果：0 → 8 个 skill（含 `quality-review`、`sit-testcase-generation-v2`）**。后续目录统一细节见《测试用例Agent-Skill核对与目录统一.md》 |
| P0-2 | **配置漂移**：根 `.env` 的 `API_SKILLS_ROOT` 指向不存在的 `backend/app/agents/api/agent_skills`，而 `start_server_postgres.py` / `langgraph.json` 会加载它；且 OS env 优先级高于 `app/.env` | 根 `.env` 改为与 `app/.env` 一致；`settings.py` 的 `env_file` 改为绝对路径（不再随 CWD 漂移），并新增校验器把相对 `*_root(s)` 锚定到仓库根 |
| P0-3 | **鉴权结构化数据不完整**：`openapi_parser` 只读 method 级 `security`，未合并 path 级与全局，导致全局声明鉴权的契约落成 `None`、被误判为公开接口 | 新增 `_resolve_security` 三级回退（method > path > global），保留 `security: []` 的"显式无需鉴权"语义；来源写入 `custom_config.security_source` |
| P0-4 | **skill 装配失败是静默的**：`SkillsMiddleware` 找不到 source 只记 warning，Agent 照常启动但 0 个 skill | 新增 `app/config/skill_assembly_check.py`：确定性自检（不依赖 deepagents），启动时 `lifespan` 调用，失败记 ERROR；也可 `python -m app.config.skill_assembly_check` 手工排查 |

### P1：覆盖度评审能力（方案的第一阶段范围）

| 编号 | 内容 |
|---|---|
| P1-1 | `app/test_platform/api_coverage_review.py`：纯函数、确定性、无 DB/无 LLM 的覆盖度评分服务 |
| P1-2 | `.claude/skills/api/api-coverage-review/SKILL.md`：指导何时调、怎么整理覆盖声明、结果怎么处置 |
| P1-3 | `app/agents/api/tools/coverage_review_tools.py`：`review_api_coverage` 工具（已注册进 API Agent，工具数 33 → 34）；报告存为 `API_TEST_REPORT` 成果物；门禁结论回写端点并接入执行路径 |

---

## 二、口径设计（对应复核结论的 3 条红线）

### 1. 未知 ≠ 未覆盖

- 契约未提供某维度所需数据 → 该维度标记 `unknown`，**从分母剔除并重归一化**，绝不按 0 分计入；
- 契约无需覆盖项（如 GET 无请求体）→ `not_applicable`，同样剔除；
- 已知权重占比 < 60% → 该接口判 `insufficient_data`，**不给分**，进人工确认项；
- 典型例子：契约三级都未声明鉴权时，鉴权维度是 `unknown` 而不是"未覆盖"——
  因为无法区分"确实无需鉴权"与"契约漏写"。

### 2. 不做二次扣分

单维度扣分 = 该维度满分 − 该维度得分，结构上**恒 ≤ 该维度满分**，因此不存在"加权分与扣分项重复计算"。
扣分清单只作解释，不参与总分。该封顶原则沿用既有 `quality-review/SKILL.md` 的「评分一致性原则」。

### 3. 阈值是平台规则

六个维度权重合计 100：

| 维度 | 权重 |
|---|---|
| 状态码覆盖 | 25 |
| 必填参数覆盖 | 20 |
| 枚举覆盖 | 15 |
| 类型与格式覆盖 | 15 |
| 请求体字段覆盖 | 15 |
| 鉴权场景覆盖 | 10 |

门槛（两层叠加生效）：

| 层级 | 门槛 |
|---|---|
| 单接口硬门槛 | 每个接口 ≥ 70 |
| 核心接口硬门槛 | 核心接口 ≥ 85 |
| 集合均值 | 平均分 ≥ 80 |
| 必填参数硬门槛 | 覆盖率 = 100% |

**以上均为 `platform_rule`（本项目自定义），报告中带 `provenance` 字段如实声明。**
ISTQB / OWASP / ISO 25010 / IEEE 829 只作方法学参考方向，不规定这些权重与阈值——
引用时不要表述为"行业标准要求"。

### 4. 不臆造覆盖

声明了契约中不存在的状态码/参数/请求体字段/枚举取值 → 判为**非法声明**，直接阻断并列出。
"凭空声明已覆盖"会被确定性拦住，而不是默默加分。

### 5. 第一阶段明确不自动评分的维度

业务规则、权限矩阵、状态机约束、限流/超时/依赖服务不可用、安全 payload（SQL 注入/XSS 等）、
响应"核心字段"定义——**无结构化来源**，一律进 `manual_review_items`，不参与评分。

---

## 三、执行门禁

评审结论写入端点 `custom_config["coverage_gate"]`，`execute_api_script` 在执行前读取：

| 环境变量 | 行为 |
|---|---|
| 未设置 / `warn`（默认） | 未通过时只记告警，**不阻断**——避免影响既有流水线 |
| `API_COVERAGE_GATE_STRICT=strict` | 未通过时**硬阻断**，返回 `reason_code=COVERAGE_GATE_BLOCKED` |

默认 warn 是为了不在未经验证的情况下改变既有执行行为；确认评估通过后再切 strict。

---

## 四、交付物清单

**新增**

- `backend/app/test_platform/api_coverage_review.py` — 覆盖度评审服务
- `backend/app/agents/api/tools/coverage_review_tools.py` — 工具 + 门禁读写
- `backend/app/config/skill_assembly_check.py` — skill 装配自检
- `.claude/skills/api/api-coverage-review/SKILL.md` — skill 文档
- `backend/tests/platform/test_api_coverage_review.py` — 46 项
- `backend/tests/platform/test_skill_assembly_and_security.py` — 26 项

**修改**

- `backend/app/config/settings.py` — 锚定相对根目录 + `env_file` 绝对化 + skills 根目录统一为 `.claude/skills`（含 testcase / android / ios）+ 顶部写入唯一维护目录约定
- `backend/app/services/openapi_parser.py` — security 三级回退合并
- `backend/app/agents/testcase/agent.py` — skills source 修正
- `backend/app/agents/api/tool_registry.py` — 注册 `review_api_coverage`
- `backend/app/agents/api/tools/script_execution_tools.py` — 执行前接入覆盖度门禁
- `backend/app/main.py` — 启动时 skill 装配自检
- `.env` — `API_SKILLS_ROOT` / `API_WORKSPACE_ROOT` 修正

---

## 五、验证证据（可复跑）

```powershell
cd <repo>\backend
$env:PYTHONPATH = "<repo>\backend"

# 1) 覆盖度评审 + P0 回归
<repo>\.venv\Scripts\python.exe -m pytest `
  tests/platform/test_api_coverage_review.py `
  tests/platform/test_skill_assembly_and_security.py -p no:cacheprovider -q
# 实测：72 passed

# 2) 平台层全量回归（确认 P0 改动无副作用）
<repo>\.venv\Scripts\python.exe -m pytest tests/platform tests/test_testcase_quality_gates.py -p no:cacheprovider -q
# 实测：702 passed

# 3) skill 装配自检
<repo>\.venv\Scripts\python.exe -m app.config.skill_assembly_check
```

skill 装配实测结果（三种启动场景均通过）：

| Agent | source | 装配 skill 数 |
|---|---|---|
| api | `/skills/api/` | **8**（新增 `api-coverage-review`） |
| testcase | `/skills/` | **8**（修复前 0） |
| security | `/skills/security/` | **10**（修复前 0，根目录双重嵌套） |
| web_mcp / web_cli / webwright | — | 8 / 9 / 9 |
| android / ios | `/skills/` | 5 / 5 |

---

## 六、未做 / 建议后续

1. **门禁默认仍为 warn。** 建议先用几个真实项目跑一轮 `review_api_coverage`，确认评分口径合理后再设 `API_COVERAGE_GATE_STRICT=strict`。
2. **复合接口（多接口场景）评分未实现。** 复核确认本仓库 `test_scenarios` / `scenario_steps` / `step_data_mappings` 已具备 Producer/Consumer、数据传递字段、前置后置、正确顺序等结构化元数据；真缺的只有**错误顺序的显式建模、幂等要求、异常补偿策略**。补齐这 3 项元数据后再启用复合接口评分。
3. **业务规则等 6 类维度需人工确认项台账。** 目前只在报告里列出，尚未落库形成可跟踪的确认记录。
4. **`quality-review/SKILL.md` 的行业标准标注治理。** 该文件把自定义权重/等级挂在「行业标准依据」下引用 ISO/IEEE/ISTQB，未区分标注，建议按本次口径补上归属说明。
5. **`web_chrome` 的 skills 根目录**（`backend/workspace/web_chrome`）未纳入自检（当前无对应 Agent），如后续接入需同步补。
