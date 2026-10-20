# 测试用例 Agent Skill 核对与维护目录统一

本次动作有两件：**① 把测试用例 Agent 的 skill 维护目录统一为一处**；
**② 以参考仓库 `D:\code\Pyproject\ai-test-agent-system` 为基准核对本框架的 testcase skill 集合与内容**。

> 背景：上一轮 API 覆盖度评审实现时，为了解决「testcase Agent 运行时不加载任何 skill」，
> 曾把它的 skills 根目录指向 `.claude/skills/testcase`。本轮复核发现该目录与
> `backend/workspace/testcase/skills/` 是**两份副本且已漂移**，因此重新按"单一维护目录"
> 处理，确认对 testcase 生成链路是**增强而非削弱**。

---

## 一、统一后的唯一约定

已写入 `backend/app/config/settings.py` 顶部（改动前请先读该注释）：

```
1. 唯一维护目录：<repo>/.claude/skills/<agent>/<skill>/SKILL.md
   不要在 backend/workspace/** 下另存一份 skill。

2. 接线约定：所有 *_skills_root 一律 = .claude/skills
   agent 侧 SkillsMiddleware(sources=["/skills/<agent目录>/"])

3. 自检：app/config/skill_assembly_check.py 启动时校验装配，失败记 ERROR
```

统一后 9 个 agent 的接线（实测）：

| Agent | skills_root | sources | 装配 skill 数 |
|---|---|---|---|
| api | `.claude/skills` | `/skills/api/` | 8 |
| testcase | `.claude/skills` | `/skills/testcase/` | 8 |
| testcase-reference | `.claude/skills` | `/skills/testcase/` | 8 |
| security | `.claude/skills` | `/skills/security/` | 10 |
| web_mcp / web_cli / webwright | `.claude/skills` | `/skills/<同名>/` | 8 / 9 / 9 |
| android | `.claude/skills` | `/skills/android/` | 5 |
| ios | `.claude/skills` | `/skills/ios/` | 5 |

**注意**：本次**只调整了 skill 的目录与接线，没有改动任何一份 testcase skill 的正文内容**
（除 `uat-scenario-design` 是把更新的那份提升上来，见下）。

---

## 二、统一前的实际状态：两份副本已漂移

平台里 testcase skill 存在两处（逐文件 SHA256 对比）：

| skill | `.claude/skills/testcase` | `backend/workspace/testcase/skills` | 结论 |
|---|---|---|---|
| output-formatter | 6267 `A6B1A4A3F6B0` | 6267 `A6B1A4A3F6B0` | 一致 |
| quality-review | 24548 `8F3915E001B9` | 24548 `8F3915E001B9` | 一致 |
| requirement-analysis | 5590 `1B31CF4EC927` | 5590 `1B31CF4EC927` | 一致 |
| sit-testcase-generation-v2 | 33262 `70DD974DA8A8` | 33262 `70DD974DA8A8` | 一致 |
| test-case-design | 6535 `8B15505B2007` | 6535 `8B15505B2007` | 一致 |
| test-data-generator | 10301 `1AC3CF97E66D` | 10301 `1AC3CF97E66D` | 一致 |
| test-strategy | 4956 `55A3380B8F51` | 4956 `55A3380B8F51` | 一致 |
| **uat-scenario-design** | 7218 `EDB95BAFFFE4`（117 行） | **10876 `418958C4BAEB`（194 行）** | **已漂移** |

`uat-scenario-design` 两份是**超集关系**：workspace 版 194 行 ⊃ `.claude` 版 117 行
（仅 workspace 版有 87 行、仅 `.claude` 版有 10 行），workspace 版时间戳更新（2026-07-18 vs 2026-06-13）。
即：**有人后来只在 workspace 那份上继续迭代，`.claude` 那份停在了旧版。**

### 处置

1. 把 workspace 的 194 行新版**提升**到 `.claude/skills/testcase/uat-scenario-design/`；
2. 逐文件校验两处**全部 8 个文件字节一致**（不一致文件数 = 0）；
3. 确认冗余后删除 `backend/workspace/testcase/skills/`（该目录无任何代码引用，
   且 `/skills/` 路由会遮蔽它，属纯冗余）。

> 收益：修掉了上一轮"指向 `.claude` 旧版"的隐患 —— 现在 Agent 拿到的是**更新**的 194 行版本。

---

## 三、与参考仓库的核对结果

参考仓库 `ai-test-agent-system` 把同一套 skill 维护在 `src/workspace/testcase/skills/`（8 个文件，**已被 git 追踪**；
该仓库没有 `.claude` 目录）。本框架统一到 `.claude/skills/testcase/`。逐项核对：

| skill | 本框架 | 参考仓库 | 判定 |
|---|---|---|---|
| output-formatter | 6267 `A6B1A4A3F6B0` | 6267 `A6B1A4A3F6B0` | ✅ 一致 |
| quality-review | 24548 `8F3915E001B9` | 24548 `8F3915E001B9` | ✅ 一致 |
| sit-testcase-generation-v2 | 33262 `70DD974DA8A8` | 33262 `70DD974DA8A8` | ✅ 一致 |
| test-case-design | 6535 `8B15505B2007` | 6535 `8B15505B2007` | ✅ 一致 |
| test-data-generator | 10301 `1AC3CF97E66D` | 10301 `1AC3CF97E66D` | ✅ 一致 |
| test-strategy | 4956 `55A3380B8F51` | 4956 `55A3380B8F51` | ✅ 一致 |
| uat-scenario-design | 10876 `418958C4BAEB`（194 行） | 7218 `EDB95BAFFFE4`（117 行） | ⬆️ **本框架更新**（保留本框架版，不回退） |
| **requirement-analysis** | 5590 `1B31CF4EC927`（81 行） | **21162 `7E5B950D2E98`（185 行）** | ⚠️ **参考仓库更新**（见 4.1，暂未合并） |

即：跨仓库差异只有 `requirement-analysis` 一个文件，其余 7 个完全同源。

---

## 四、核对中发现的 3 个问题

### 4.1 `requirement-analysis` 陈旧于**本框架自己的** V1.2 需求质量门控

- 参考版的 185 行是平台版 81 行的**严格超集**：15 个章节标题与顺序完全相同，
  仅多出「PRD 质量门控（"分析不断，生成才拦"）」与「完成分析后的固定输出顺序」两节。
- 平台**确实实现了**这套门控契约 —— `backend/app/test_platform/requirements/` 中
  `prd_quality` / `can_generate_testcases` / `missing_items` / `ambiguous_items` / `affected_features`
  全部落地（83 处引用），并由 `api/v2/ai.py`、`api/v2/requirement_quality.py` 调用。
- 但平台的 `requirement-analysis` skill **完全没写**这些要求，导致：
  模型侧没有被要求输出 `prd_quality` JSON，而门控解析不到就 fail-closed（`can_generate_testcases` 默认 False）。
  **建议优先处理**，否则门控可能长期停在 Incomplete。

**为什么不直接整份搬运**：参考版还规定了安全合规子门控
（`security_compliance_applicable` / `security_compliance_complete` / `security_data_categories` / `security_evidence`），
而**本框架全仓库 0 处实现**该字段（参考仓库则在 `src/app/agents/testcase/quality_gate.py` 与
`src/app/middleware/prd_quality_gate.py` 中有代码级处理）。整份搬运会让 skill 承诺一个平台并不存在的代码级拦截。
=> 合并前需要先决定：**是补平台的安全子门控，还是只搬门控契约、把安全部分标注为人工确认项。**

### 4.2 提示词引用了不存在的 skill（已修）

`backend/app/agents/testcase/agent.py` Step 1 原写：

```
**激活 Skill**: `rag-query` → `requirement-analysis`
```

本框架**不存在** `rag-query` skill（`.claude/skills` 下无 `rag` 目录，grep 全仓库无命中）——
平台的检索能力由 KB 工具提供（`get_knowledge_spaces_tool` / `query_knowledge_base_tool`）。
已改为激活 `requirement-analysis`，并显式说明用 KB 工具做检索、不要尝试激活 `rag-query`。

（同一原因，上一轮已移除 testcase Agent 那个恒为空的 `/skills/rag/` source。）

### 4.3 两份副本本身（已消除）

见第二节。已加守护测试防止回潮（见下）。

---

## 五、验证证据

```powershell
cd <repo>\backend
$env:PYTHONPATH = "<repo>\backend"

# 1) skill 装配自检
<repo>\.venv\Scripts\python.exe -m app.config.skill_assembly_check
#  → 全部 9 个 source 装配正常；testcase / testcase-reference 各 8 个 skill

# 2) 新增/回归测试
<repo>\.venv\Scripts\python.exe -m pytest `
  tests/platform/test_skill_assembly_and_security.py `
  tests/platform/test_api_coverage_review.py -p no:cacheprovider -q
#  → 79 passed

# 3) 平台层全量回归（含既有 prompt/skill 守卫）
<repo>\.venv\Scripts\python.exe -m pytest tests/platform tests/test_testcase_quality_gates.py `
  tests/test_prompt_skill_guard.py -p no:cacheprovider -q
#  → 761 passed
```

新增的守护测试（`backend/tests/platform/test_skill_assembly_and_security.py::TestSkillsMaintenanceDirectory`）：

| 测试 | 防的是什么 |
|---|---|
| `test_all_agents_share_the_same_skills_root` | 某 agent 的 skills 根又跑偏成别的目录 |
| `test_agent_sources_follow_unified_convention` | sources 写法漂移（如回到 `["/skills/"]`） |
| `test_source_dirs_exist_under_canonical_root` | source 指向不存在的目录 |
| `test_no_duplicate_skill_copies_outside_canonical_root` | `backend/workspace/**` 下再出现 SKILL.md（双份维护） |
| `test_testcase_skill_set_matches_reference_set` | 迁移时漏 skill（对照参考仓库 8 个） |
| `test_uat_scenario_design_is_the_evolved_version` | `uat-scenario-design` 回退到 117 行旧版 |

---

## 六、本次未做 / 需你决定

1. **`requirement-analysis` 是否合并参考版（185 行）** —— 需先决定安全合规子门控的处置方式（见 4.1）。
   我没有擅自合并，因为这会给 testcase 生成链路引入平台未实现的判定承诺。
2. **参考仓库把 testcase skill 纳入 git 追踪，本框架的 `.claude/` 整体未被追踪**
   （`git status` 显示 `?? .claude/`）。若希望 skill 变更可审计，建议把 `.claude/skills/` 纳入版本控制 —— 这属于仓库策略，需你确认。
3. **`web_chrome_skills_root`（`backend/workspace/web_chrome`）** 未纳入统一与自检：当前无对应 Agent。若后续接入，请按第一节约定改为 `.claude/skills`。
