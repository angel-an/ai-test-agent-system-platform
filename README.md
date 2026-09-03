# AI Test Agent System Platform

基于 **DeepAgents / DeepSeek** 的多模态测试 Agent 平台：以 LangGraph 编排、Playwright/webwright 执行，
覆盖 **API / Web / Android / iOS / 安全测试 / 用例生成** 的测试 Agent 闭环，并内置**执行治理层**
（脚本来源授权、进程资源隔离、工具安全策略、自愈审批、断言编译与治理指标）。

> 当前冻结版本：**v1.0.0-exec-governance**（rev54-58 执行治理收口，2026-09-03，commit `df0f491`）。
> 冻结说明与逐轮验收归档在仓库外工作区文档 `RELEASE_exec_governance_v1.0.0.md` 与
> `受限执行器方案.md`（未纳入本仓库提交；如需入库请另行申请）。

---

## 核心能力

| 领域 | 说明 |
|---|---|
| API 测试 | 接口契约生成、断言编译、闭环用例链接（account/finance/ES 深分页回归等） |
| Web 测试 | Playwright / webwright 双轨：登录态复用、菜单遍历、CRUD 全面测试、报告链 |
| Android / iOS | 移动 App 子功能测试（JSONB 断言模型） |
| 安全测试 | 漏洞登记（IDP 缺陷联动）、导航 origin 白名单 |
| 测试用例 | Agent 生成（禁止模糊断言词）、UAT 节点可验证结果 |

## 执行治理层（安全 → 审批 → 断言 → 指标）

- **来源授权（2a）**：脚本必须经 `web_script_registry` 登记（真实项目 + 附件 + 内容哈希），执行前三要素校验，未登记终局拒绝。
- **进程隔离（B3）**：Windows 经 Job Object（内存/活动进程上限 + KILL_ON_JOB_CLOSE + 超时杀进程树），生产 fail-closed。
- **工具安全策略（rev54）**：高危工具（`browser_run_code_unsafe`/`browser_evaluate`）黑名单 + 工具级 final 拒绝（注册过滤"看不到"+ 调用守卫）；导航 scheme/origin 白名单（`file:/javascript:` 拒绝、协议相对 URL 校验、loopback 放行）。
- **自愈审批流（rev55）**：执行失败入队 → LLM 修复 → proposed **不自动覆盖** → `pending_approval`（版本号 + proposed 附件锚点）→ 人工 approve/reject 才发布 effective。
- **断言编译（rev56）**：`expected_result` 保存时编译为可机检 `compiled_assertions`（field/contains/numeric/status）；无法编译标 `human_oracle`，执行只校验编译产物、不现场发明断言。
- **只读指标 + 执行证据闭环（rev57/58）**：`executable_rate` / `heal_adoption_rate` / `human_oracle_rate` / `flaky_rate`；报告含 `self_reflect_result.json` + `assertion_result.json`。

## 技术栈

Python ≥3.13 · FastAPI · PostgreSQL（asyncpg）· Redis · MinIO · MongoDB（审计，可选）·
LangGraph / LangChain · deepagents ≥0.6.1 · Playwright · APScheduler · Alembic

## 目录结构（核心子集）

```
backend/
  app/                 # 应用代码
    agents/            # 各域 Agent 与工具（api/web/android/ios/security/testcase）
      tools/web/       # Web 治理：process_guard / script_provenance / script_review /
                       #   script_repair_agent / assertion_compiler / artifacts_tools
    api/v2/            # REST API（含 governance-metrics、web-script-reviews 审批）
    models/            # SQLAlchemy 模型
    services/          # 业务服务（web_test_service / execution/* / metrics）
    auth/  config/  middleware/
  alembic/versions/    # 迁移（head=0016_web_compiled_assertions）
  tests/               # pytest 回归（323 passed, 1 skipped）
```

## 快速开始

```bash
# 1. 依赖（Windows 开发环境）
python -m venv .venv
.venv\Scripts\activate
pip install -e .            # 或 pip install -r backend/requirements.txt

# 2. 配置环境（密钥不进 git）
cp backend/app/.env.example backend/app/.env
# 必填：POSTGRES_* / MINIO_* / DEEPSEEK_API_KEY
# 可选：NAVIGATION_ORIGIN_ALLOWLIST（导航 origin 白名单，生产建议 REQUIRED=1）

# 3. 迁移（本地 PG）
cd backend && python -m alembic upgrade head

# 4. 启动
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 前端（可选）：ui/ 目录 Next.js 应用

# 5. 测试
python -m pytest backend/tests -p no:cacheprovider -q
```

## 测试与迁移基线

- 全量回归：**323 passed, 1 skipped**（pytest 原始摘要；跳过项 = POSIX 降级用例）
- Alembic head：`0016_web_compiled_assertions`（0015 审批流、0013/0014 idp 缺陷表修复、0011 超管权限等）

## 安全说明

- `.env` 已 gitignore；生产密钥请走环境变量/密钥管理
- 默认超管仅 `ENABLE_DEV_DEFAULT_SUPERUSER=1` 开发模式自动提升；生产用
  `python -m app.cli grant-superuser <username>` 受控授予
- 管理 API（审批、指标、flaky 采样）均要求超管
- 第三方密钥（DeepSeek 等）建议定期轮换

## 文档

- 执行治理方案与各轮验收：`受限执行器方案.md`（本仓库外工作区归档）
- 冻结交付说明与保留项：`RELEASE_exec_governance_v1.0.0.md`
- 前端文档见 `docs/`
