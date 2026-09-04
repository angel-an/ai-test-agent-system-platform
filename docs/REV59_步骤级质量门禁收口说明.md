# rev59 收口：测试用例生成「可稳定复用/可交付」改造说明

> 日期：2026-09-04 · 范围：测试用例生成与执行闭环（SIT 普通模板优先，UAT 既有节点级 expected 规则不变）
> 评审驱动目标：**可执行、可审、可解释**——按 ①补 step.result ②收紧提示词 ③扩展预审 ④统一统计口径 ⑤保留降级 五步收口。

## 1. 改动清单

| 文件 | 改动 |
|---|---|
| `backend/app/agents/tools/testcase/quality_gates.py`（新增） | 纯函数门禁：模糊词表、引号剥离、步骤归一化（`{step,result}` 别名兼容 + “步骤 —— 预期结果：x”自动拆分）、步骤文本/结果双查、拒绝原因生成 |
| `backend/app/agents/tools/testcase/testcase_tools.py` | ① `_create_test_case_impl` 落库前强制门禁：普通模板每步 result 必填、步骤文本/结果模糊词双查，违规在 HTTP 前以可读原因拒绝；`TESTCASE_ALLOW_QUALITY_DEGRADE=1` 时降级落库并打 `custom_fields.quality_grade=degraded` + tag `质量降级` ② `stage_testcases` 预审扩展：用例级预期结果 + 步骤文本（去引号）/字典步骤 result 双查，rejected 带 reasons 回给 Agent 重写 |
| `backend/app/agents/testcase/agent.py` | SYSTEM_PROMPT：用例格式改为“步骤与预期结果成对”，给出落库字段契约（test_case_steps=[{step,result}]，result 必填）；模糊词拦截从“仅预期结果”扩展到“每步 result + 步骤文本”；新增「rev59 步骤级质量门禁」自检节与红线要求（被拒按 reasons 重写，禁止静默降级，二次仍难量化须向用户说明） |
| `backend/app/services/execution/engine.py` | 执行收尾计数由“作业数回写（update_counts_from_jobs）”改为“**用例级回写（repo.update_counts）**”：test_cases_count=关联用例数；passed/failed/not_executed 由 `_sync_test_run_case_status`（作业→其绑定用例）维护的用例状态汇总 |

## 2. 门禁规则（入库前强制，确定性、可解释）

1. **每步 result 必填**：普通模板 `test_case_steps` 每步必须含 `result`（预期结果）；允许键别名（step/action/操作/步骤；result/expected/预期/预期结果），允许“步骤 —— 预期结果：x”自动拆字段，禁止把结果写进步骤文本。
2. **模糊断言词零容忍**：`result` / 用例级 `expected_results` 严格扫描（不去引号）；步骤文本在**去除「」“”引号内容后**同样扫描（避免误伤需求原文文案如“提示「绑定成功」”），命中即拒绝。
3. 词表：验证成功/功能正常/页面正确/查询正确/保存成功/提交成功/删除成功/更新成功/创建成功/绑定成功/正确/成功/正常/通过/无误/无异常/符合预期。
4. 拒绝信息按 第N步/字段/命中词 组织，工具回给 Agent 逐条重写（现有重写循环不变）。

## 3. 统计口径（统一为“用例级”，可解释）

- `test_cases_count` = 运行关联用例数（TestRunTestCase 行数）——与“生成 45 条”一致；
- `passed/failed/skipped/not_executed` = 关联用例 `latest_status` 汇总；作业结果经 `web_tests.test_case_id` → 运行内用例映射回写（rev59 起绑定用例的作业才回写 passed/failed）；
- 未绑定具体用例的作业：执行结果体现在**作业列表/报告**，不再冒充“用例通过数”。

验证数据（库内 test_runs）：

| 运行 | 代码 | 关联 | passed | not_executed | 说明 |
|---|---|---|---|---|---|
| TR-20 | 旧 | 45→1(作业数) | 1 | 0 | 口径问题样本 |
| TR-21 / TR-22 | rev59 | **45** | **1**（绑定 TC-313362 的作业） | **44** | 用例级口径生效 |

## 4. 降级方案（增强不破坏主流程）

- Agent/工具层：默认拒绝不合规 payload（返回原因→重写循环）；
- 紧急降级：`TESTCASE_ALLOW_QUALITY_DEGRADE=1` 时允许缺 result/模糊步骤落库，但**显式标识** `custom_fields.quality_grade="degraded"`、`quality_reasons`、tags 增加「质量降级」，前端/报表可筛可查；
- Prompt 约束 Agent 不允许静默降级：连续两次同一原因被拒仍无法量化时，必须向用户说明量化难点并给人工确认要点。

## 5. 验证结果

- 新增单元测试 `backend/tests/test_testcase_quality_gates.py`：**15/15 passed**（引号剥离/模糊命中/别名键/自动拆分/缺 result/空 steps 等）；
- 全量回归：受限沙箱 301 passed（37 个失败全部为子进程/写 D:\code 类**环境敏感用例**，已在 full-access 下复跑通过 53/53，非本次回归）；
- 集成复验（127.0.0.1:8011 rev59 实例 + 库内核对）：
  - 门禁 C：缺 result payload 在 HTTP 前被拒（“第 1 步缺少预期结果（result 必填…）”）；✅
  - 预审 B：步骤文本含“保存成功”→ rejected 且带 reasons；合规条目正常 staged；✅
  - 计数 A：TR-21/TR-22 → run_state=DONE，45 关联 / 1 passed（绑定 TC-313362）/ 44 not_executed；✅

## 6. 生效与遗留

- 代码已提交（见 git log）；**生效需重启**：langgraph(2026，agent 提示词/工具)、backend(8000/8011，引擎/API)。8000 实例为外部受限会话进程不可重启，UI 直连链路在对应服务重启后生效；
- 遗留建议：完整 LLM 端到端重跑（WhatsApp 23 + 八達通&yuu 22 两套件在新提示词下重新生成并复核 step.result 覆盖率=100%）；如需要，把门禁规则镜像到后端创建 API（硬性）并给 UI 步骤编辑器加行内校验。


## 7. rev59 闭环补丁（二轮评审「发现1」收口，2026-09-04）

评审指出：stage_testcases / export_all_testcases 仍走旧逻辑（6 词表、dict 步骤未强制 result 非空），
与 DB 创建轨判定不一致。已收口（commit bc7bec1）：

1. **统一门禁**：quality_gates 新增 alidate_staged_case(case)（normalize_steps 每步 result 必填 +
   完整词表双查 + 用例级 expected_results 模糊），stage_testcases 与 export_all_testcases 均改调它，
   **与 _create_test_case_impl（DB 轨）逐字一致**（拒绝原因同源）。
2. **stage**：不合规拒绝并带 reasons；合规入区统一为每步 {step, result}（导出/回看与 DB 轨一致）。
3. **export**：结构性问题（缺 result 等）**硬性拦截**（历史脏暂存数据的兜底）；模糊词按完整词表、
   ≤10% 阈值统计；导出前归一化步骤，预期结果列缺省时由每步 result 推导（_case_expected_text），
   _flatten_steps 兼容归一化 {step,result}（step 文本不再被误当序号）。
4. 旧 6 词表硬编码清零（uzzy_words = [...] 残留 0 处）。

验证：门禁单测 **25/25 passed**（新增跨轨一致与渲染助手 8 项）；运行时三轨一致：
同一缺 result 用例 → stage 拒绝 / DB 创建拒绝 / export 硬拦，拒绝原因逐字相同；
合规 8 条套件整包导出并读回 Excel：步骤列 = 归一化步骤，预期列 = 每步 result 推导，渲染正确。


## 8. 最小运行时复验（服务重启后，2026-09-04 12:2x）

用户侧已重启 3000/8000/2026（新 PID）；本会话停用自建的 8011 过期实例。复验 3 条最小样本 + UI 入口：

| # | 样本 | 结果 |
|---|---|---|
| A | 合规用例（每步含 result）经真实 HTTP(:8000) 创建 → TC-608193，读回 2/2 步含 result | ✅ |
| B | 缺 result 用例：stage 拒绝 / DB 创建轨拒绝（原因逐字相同：第 1 步缺少预期结果…）；原始 HTTP API 按设计宽松（201，手动/降级路径，门禁在生成边界） | ✅ |
| C | stage 合规 8 条 → export_all_testcases 导出 8 条 → 读回 Excel：测试步骤列=归一化步骤、预期结果列=每步 result 推导 | ✅ |
| UI | 重启后 :3000：模块加载（45 条可见）、AI 生成/从文档生成/导出对话框均打开；导出对话框显示「已选择 0/45」→ 勾选后真实下载 Excel（含用例编号…预期结果 列） | ✅ |

产物：
untime_minimal_result.json、ui_runtime_log.json、截图 shots/30-33*.png、ui_export_full_*.xlsx。
结论：新门禁与提示词在真实链路无回弹，主流程（生成→暂存→导出→落库）与口径一致。可进入冻结阶段。


## 9. 验收结论（评审方确认，2026-09-04）

**结论：功能已稳定复用，可进入冻结（rev59 步骤级质量门禁闭环验收通过）。**

依据（服务重启后运行时复验，证据闭合）：
- 合规用例真实 HTTP 创建后可读回 2/2 步均带 result；
- 缺 result 样本在 stage 与 DB 创建轨被同一理由拒绝；
- export_all_testcases 导出 Excel 的 步骤/预期结果 列均按新规则落；
- 3000/8000/2026 重启后 UI 入口与导出下载正常。

保留的边界说明（非回弹，属当前降级设计）：
- 原始 HTTP 创建 API（POST /folders/{id}/test-cases）按设计保持宽松，真正强门禁在
  生成（_create_test_case_impl / batch_create）、暂存（stage_testcases）、导出
  （export_all_testcases）边界；
- 若后续要把“手动建用例 API”也改为硬拦，为独立工作项（会触及手动建用例入口，
  冻结期单独评估）。

冻结基线（git）：
- eb4a09c rev59 步骤级质量门禁与执行计数口径收口
- 1570fde 预期结果导出序号守卫 backport
- bc7bec1 rev59 闭环——stage/export 统一走 quality_gates 门禁

保留回归测试：backend/tests/test_testcase_quality_gates.py（25 passed，纯函数，CI 固定跑）。


## 10. 冻结收尾执行记录（2026-09-04，主线不再改动）

按验收顺序执行：
1. 清理临时验证产物：删除平台仓库根目录 pr9-verification-20260904/ 归档镜像；
   删除临时导出 xlsx（ui_export_full_*.xlsx、uploads/rev59-*.xlsx）；
   删除运行残留日志/控制台转储（*.log、*_run.txt、*_view.txt、*_out/err.txt 及中间 dump）。
2. 固化验收记录：本文件为最终留档（冻结基线 §9/§10、边界说明 §9、回归清单）；仓库根目录留未跟踪副本。
3. 必要回归：backend/tests/test_testcase_quality_gates.py（25 passed）为长期守门；CI 接入时固定跑该文件即可（仓库当前无 CI 配置）。
4. 独立项排期：手动建用例 API 硬拦另开任务，不并入本次冻结。

保留清单（工作区 pr9_testcases/）：REV59 收口说明、PR9 验证报告(初稿/定稿)、对比分析、
quality_gate_audit md/json、cases_index csv/md、case_details_all/folder_cases_all/run_evidence 等 json、
evidence_*.json、两次 Agent 运行结果、复验结果 json、复现脚本 *.py、需求文档提取文本、shots/ 31 张截图。
数据库：验证用例/运行（PR-9 45 条用例、TR-19/20/21/22 等）按既有约定保留供复核，不属本次清理范围。
