// API 通用类型定义

// 分页信息
export interface PaginationInfo {
  page: number;
  page_size: number;
  count?: number;
  total: number;
  prev?: string | null;
  next?: string | null;
}

// 通用分页响应
export interface PaginatedResponse<T> {
  success: boolean;
  data: T[];
  info?: PaginationInfo;       // legacy alias (后端可能不返回此字段)
  pagination?: PaginationInfo; // backend actual field
}

// 通用成功响应
export interface SuccessResponse<T> {
  success: boolean;
  data: T;
}

// 通用消息响应
export interface MessageResponse {
  success: boolean;
  message: string;
}
// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZhelJMVkE9PTpiMTU5ZGMwNA==

// 链接信息
export interface LinkInfo {
  self?: string;
  project?: string;
  folder?: string;
  parent?: string;
  sub_folders?: string;
  test_cases?: string;
}

// 项目信息
export interface ProjectInfo {
  identifier: string;
  name: string;
  description?: string | null;
  created_at: string;
  created_by: string;
  updated_at?: string | null;
  team_id?: number[] | null;
  test_cases_count: number;
  folders_count: number;
  links?: LinkInfo | null;
}

// 创建项目请求
export interface ProjectCreate {
  name: string;
  description?: string;
  team_id?: number[];
}

// 更新项目请求
export interface ProjectUpdate {
  name?: string;
  description?: string;
  team_id?: number[];
}
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZhelJMVkE9PTpiMTU5ZGMwNA==

// 文件夹类型
export type FolderType = "test_case" | "api_test" | "web_test" | "android_test" | "ios_test";

// API 端点简要信息（用于文件夹树展示）
export interface APIEndpointSummary {
  id: string;
  display_name: string;
  method: string;
  path: string;
  tag_group?: string | null;
  total_test_cases?: number;  // 该接口的测试用例数量
  total_test_runs?: number;   // 该接口被执行的次数
}

// 文件夹信息
// 显示格式: 直接用例数(总用例数)
// 例如: 2(10) 表示该目录直接有2条用例，该目录及所有子目录共10条用例
export interface FolderInfo {
  id: string;
  name: string;
  description?: string | null;
  parent_id?: string | null;
  project_identifier: string;
  folder_type?: FolderType;  // 文件夹类型：test_case 或 api_test
  created_at: string;
  updated_at?: string | null;
  direct_cases_count: number;  // 直接在该文件夹中的用例数量
  cases_count: number;         // 该文件夹及所有子文件夹的总用例数
  sub_folders_count: number;   // 直接子文件夹数量
  hierarchy_index: number;
  links?: LinkInfo | null;
  api_endpoints?: APIEndpointSummary[];  // API 端点列表（仅用于 API_TEST 类型文件夹）
  web_functions?: WebFunctionSummary[];  // Web 功能列表（仅用于 WEB_TEST 类型文件夹）
  total_sub_functions?: number;  // 该文件夹下所有功能的子功能总数（仅用于 WEB_TEST 类型文件夹）
  total_endpoints_count?: number;  // 该文件夹及所有子文件夹的 API 端点总数（递归统计）
}

// 创建文件夹请求
export interface FolderCreate {
  name: string;
  description?: string;
  folder_type?: FolderType;  // 文件夹类型
  parent_id?: string;
}

// 更新文件夹请求
export interface FolderUpdate {
  name?: string;
  description?: string;
}

// 测试用例优先级
export type Priority = "critical" | "high" | "medium" | "low";

// 测试用例状态（基于生命周期）
export type TestCaseState =
  // 设计阶段
  | "new"              // 新建
  | "review_pending"   // 待评审
  | "reviewed"         // 已评审
  // 执行阶段
  | "not_run"          // 未执行
  | "passed"           // 通过
  | "failed"           // 失败
  | "blocked"          // 阻塞
  | "skipped";         // 跳过

// 测试用例类型
export type TestCaseType =
  | "functional"
  | "smoke_sanity"
  | "regression"
  | "security"
  | "performance"
  | "usability"
  | "acceptance"
  | "compatibility"
  | "integration"
  | "exploratory"
  | "other";

// 自动化状态
export type AutomationStatus =
  | "not_automated"
  | "automated"
  | "in_progress";

// 测试用例模板
export type TestCaseTemplate = "test_case" | "test_case_bdd";

// 测试步骤
export interface TestStepInfo {
  id: string;
  order: number;
  step: string;
  result?: string;
}
// NOTE  Mi80OmFIVnBZMlhscm9ua3VMazZhelJMVkE9PTpiMTU5ZGMwNA==

// 创建测试步骤
export interface TestStepCreate {
  step: string;
  result?: string;
}

// 测试用例信息
export interface TestCaseInfo {
  id: string;
  identifier: string;
  name: string;
  description?: string;
  preconditions?: string;
  priority: Priority;
  status: TestCaseState;
  case_type: TestCaseType;
  template: TestCaseTemplate;
  automation_status: AutomationStatus;
  project_id: string;
  folder_id?: string | null;
  owner?: string;
  created_by: string;
  created_at: string;
  updated_at?: string;
  version: number;
  tags: string[];
  issues: string[];
  custom_fields?: Record<string, unknown>;
  test_case_steps: TestStepInfo[];
  feature?: string;
  scenario?: string;
  background?: string;
  // 从 ai-test-agent-system 迁移的字段
  module?: string;
  keyword?: string;
  tp_code?: string;
  risk_level?: string;
  case_kind?: string;
}

// 创建测试用例请求
export interface TestCaseCreate {
  name: string;
  description?: string;
  preconditions?: string;
  priority?: Priority;
  status?: TestCaseState;
  case_type?: TestCaseType;
  owner?: string;
  tags?: string[];
  issues?: string[];
  automation_status?: AutomationStatus;
  custom_fields?: Record<string, unknown>;
  test_case_steps?: TestStepCreate[];
  template?: TestCaseTemplate;
  feature?: string;
  scenario?: string;
  background?: string;
  // 从 ai-test-agent-system 迁移的字段
  module?: string;
  keyword?: string;
  tp_code?: string;
  risk_level?: string;
  case_kind?: string;
}

// 更新测试用例请求
export interface TestCaseUpdate {
  name?: string;
  description?: string;
  preconditions?: string;
  priority?: Priority;
  status?: TestCaseState;
  case_type?: TestCaseType;
  owner?: string;
  tags?: string[];
  issues?: string[];
  automation_status?: AutomationStatus;
  custom_fields?: Record<string, unknown>;
  test_case_steps?: TestStepCreate[];
  feature?: string;
  scenario?: string;
  background?: string;
  // 从 ai-test-agent-system 迁移的字段
  module?: string;
  keyword?: string;
  tp_code?: string;
  risk_level?: string;
  case_kind?: string;
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZhelJMVkE9PTpiMTU5ZGMwNA==

// 测试计划信息
export interface TestPlanInfo {
  identifier: string;
  name: string;
  description?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  created_at: string;
  updated_at?: string | null;
  test_runs?: TestRunBrief[];
}

// 测试运行简要信息
export interface TestRunBrief {
  identifier: string;
  name: string;
  state?: string;
}

// =========================================================
// 测试运行
// =========================================================

export type TestRunState =
  | "new_run"
  | "in_progress"
  | "under_review"
  | "rejected"
  | "approved"
  | "done"
  | "closed";

export type TestRunActiveState = "active" | "closed";
export type FilterScope = "global" | "within_folders";
export type IssueTrackerName = "jira" | "azure" | "asana";
export type ScriptType = "api_test" | "scenario" | "web_test" | "test_case";
export type ExecutionMode = "sequential" | "parallel";
export type TriggerType = "manual" | "scheduled" | "api";
export type ScheduleTriggerType = "cron" | "interval" | "date";
export type JobStatus = "pending" | "running" | "completed" | "failed" | "skipped" | "cancelled";

export interface IssueTracker { name: IssueTrackerName; host: string; }
export interface TestCaseFilter {
  status?: string[]; priority?: string[]; case_type?: string[];
  owner?: string[]; tags?: string[]; custom_fields?: Record<string, unknown[]>;
}
export interface ConfigurationMapping { test_case_id: string | string[]; configuration_ids: number[]; }
export interface TestRunLinks { self?: string; test_cases?: string; }
export interface OverallProgress {
  untested: number; passed: number; retest: number; failed: number;
  blocked: number; skipped: number; in_progress: number;
}
export interface TestPlanRef { identifier: string; name: string; }
export interface TestStepBrief { id: string; order: number; description: string; result?: string | null; }

export interface TestRunTestCaseInfo {
  id: string; test_run_id: string; test_case_id: string; identifier: string; name: string;
  description?: string | null; case_type?: TestCaseType | null; priority?: Priority | null;
  status?: string | null; folder_id?: string | null; folder_path?: string | null;
  configuration_id?: number | null; assignee?: string | null; latest_status: TestResultStatus;
  latest_result_id?: string | null; dataset?: Array<Record<string, unknown>> | null;
  steps?: TestStepBrief[] | null; created_at?: string | null; last_updated_at?: string | null;
  created_by?: string | null; last_updated_by?: string | null;
}

export interface TestRunTestCaseMinifiedInfo {
  identifier: string; name: string; description?: string | null; latest_status: TestResultStatus;
}

export interface TestRunScriptJobInfo {
  id: string; test_run_id: string; script_type: ScriptType; script_id: string;
  script_identifier?: string; script_name?: string; execution_order: number;
  execution_mode: ExecutionMode; status: JobStatus; started_at?: string; completed_at?: string;
  duration_ms?: number; result_summary?: Record<string, unknown>; error_message?: string;
  report_path?: string; retry_count: number; max_retries: number; created_at: string; updated_at?: string;
}

export interface TestRunScriptJobCreate {
  script_type: ScriptType; script_id: string; script_identifier?: string; script_name?: string;
  execution_order?: number; execution_mode?: ExecutionMode; execution_config?: Record<string, unknown>; max_retries?: number;
}

export interface TestRunScriptJobUpdate {
  execution_order?: number; execution_mode?: ExecutionMode; max_retries?: number; execution_config?: Record<string, unknown>;
}

export interface TestRunScheduleInfo {
  id: string; project_id: string; name: string; description?: string;
  trigger_type: ScheduleTriggerType; trigger_config: Record<string, unknown>;
  is_enabled: boolean; next_run_at?: string; last_run_at?: string;
  test_run_template?: Record<string, unknown>; created_at: string; updated_at?: string;
}

export interface TestRunScheduleCreate {
  name: string; description?: string; test_run_template: Record<string, unknown>;
  trigger_type: ScheduleTriggerType; trigger_config: Record<string, unknown>; is_enabled?: boolean;
}

export interface TestRunScheduleUpdate {
  name?: string; description?: string; test_run_template?: Record<string, unknown>;
  trigger_type?: ScheduleTriggerType; trigger_config?: Record<string, unknown>; is_enabled?: boolean;
}

export interface ScriptSelection {
  script_type: ScriptType; script_id: string; script_identifier?: string; script_name?: string;
  execution_order?: number; execution_mode?: ExecutionMode; execution_config?: Record<string, unknown>;
}

export interface TestRunCreate {
  name: string; description?: string; run_state?: TestRunState; assignee?: string;
  test_case_assignee?: string; tags?: string[]; issues?: string[]; issue_tracker?: IssueTracker;
  configurations?: number[]; configuration_map?: ConfigurationMapping[]; test_plan_id?: string;
  sub_test_plan_id?: string; test_cases?: string[]; folder_ids?: number[]; include_all?: boolean;
  filter_scope?: FilterScope; filter_test_cases?: TestCaseFilter;
  scripts?: ScriptSelection[]; execution_mode?: ExecutionMode; max_concurrency?: number;
}

export interface TestRunPatchUpdate {
  name?: string; run_state?: TestRunState; tags?: string[]; sub_test_plan_id?: string;
  configurations?: number[]; configuration_map?: ConfigurationMapping[];
  folder_ids?: number[]; include_all?: boolean; filter_test_cases?: TestCaseFilter;
}

export type TestRunFullReplace = TestRunCreate;
export interface CloseTestRunRequest { active_state?: TestRunActiveState; }
export interface TestCaseAssignee { test_case_id: string; configuration_id?: number; assignee: string; }
export interface TestRunAssignRequest { assign_to: TestCaseAssignee[]; }
export interface AddTestCasesRequest { test_cases: string[]; configuration_ids?: number[]; assignee?: string; }
export interface RemoveTestCasesRequest { test_cases: string[]; configuration_ids?: number[]; }

export interface TestRunInfo {
  id: string; identifier: string; name: string; description?: string | null;
  run_state: TestRunState; active_state: TestRunActiveState; assignee?: string | null;
  test_case_assignee?: string | null; project_id: string; test_plan?: TestPlanRef | null;
  sub_test_plan?: TestPlanRef | null; test_cases_count: number; passed_count: number;
  failed_count: number; customstatus_count: number; tags: string[]; issues: string[];
  issue_tracker?: IssueTracker | null; configurations: number[];
  configuration_map?: ConfigurationMapping[] | null; folder_ids?: number[] | null;
  include_all: boolean; filter_scope: FilterScope; filter_test_cases?: TestCaseFilter | null;
  overall_progress: OverallProgress; test_cases?: TestRunTestCaseInfo[] | null;
  execution_mode: ExecutionMode; max_concurrency: number; trigger_type: TriggerType;
  script_jobs?: TestRunScriptJobInfo[] | null; created_at: string; updated_at?: string | null;
  closed_at?: string | null; links?: TestRunLinks | null;
}

export interface TestRunMinifiedInfo {
  id: string; identifier: string; name: string; description?: string | null;
  run_state: TestRunState; active_state: TestRunActiveState; assignee?: string | null;
  project_id: string; tags: string[]; configurations: number[];
  overall_progress: OverallProgress; created_at: string; updated_at?: string | null; links?: TestRunLinks | null;
}

export interface TestRunListInfo {
  id: string; identifier: string; name: string; run_state: TestRunState;
  active_state: TestRunActiveState; assignee?: string | null; project_id: string;
  test_cases_count: number; configurations: number[]; overall_progress: OverallProgress;
  created_at: string; closed_at?: string | null;
  execution_mode: ExecutionMode; max_concurrency: number; trigger_type: TriggerType;
}

export interface TestRunListParams {
  p?: number; page_size?: number; run_state?: string; assignee?: string; test_plan_id?: string;
  include_closed?: boolean; closed_before?: string; closed_after?: string;
  created_before?: string; created_after?: string; search?: string;
}

export interface TestRunTestCasesParams {
  p?: number; page_size?: number; status?: TestResultStatus; assignee?: string;
  search?: string; minify?: boolean; fetch_steps?: boolean;
}

export interface TestRunScriptJobsParams {
  p?: number; page_size?: number; script_type?: ScriptType;
}

// 测试结果状态
export type TestResultStatus =
  | "untested"
  | "passed"
  | "retest"
  | "failed"
  | "blocked"
  | "skipped"
  | "in_progress"
  | "not_executed";

// Web 功能简要信息（用于文件夹树展示）
export interface WebFunctionSummary {
  id: string;
  identifier: string;
  display_name: string;
  name: string;
  description?: string | null;
  base_url?: string | null;
  business_module?: string | null;
  folder_id?: string | null;
  total_sub_functions: number;
  total_test_cases: number;
}

// 需求分析报告
export interface RequirementReport {
  id: string;
  filename: string;
  title: string;
  path: string;
  size: number;
  created_at: string;
}

// =========================================================
// 知识库
// =========================================================

export interface KnowledgeSpaceInfo {
  id: string;
  project_id: string;
  name: string;
  description?: string | null;
  business_line?: string | null;
  folder_scope?: string[] | null;
  created_by: string;
  created_at: string;
  updated_at?: string | null;
  document_count: number;
}

export interface KnowledgeSpaceCreate {
  name: string;
  description?: string;
  business_line?: string;
  folder_scope?: string[];
}

export interface KnowledgeDocumentInfo {
  id: string;
  space_id: string;
  title?: string | null;
  file_name: string;
  file_size: number;
  content_type: string;
  status: string;
  chunk_count: number;
  error_message?: string | null;
  created_by: string;
  created_at: string;
  updated_at?: string | null;
}

export interface KnowledgeRetrievalResult {
  chunk_id: string;
  document_id: string;
  document_name: string;
  content: string;
  score: number;
  metadata?: Record<string, unknown> | null;
}

export interface KnowledgeRetrievalResponse {
  success: boolean;
  results: KnowledgeRetrievalResult[];
  total: number;
  degraded: boolean;
  degraded_reason?: string | null;
  query: string;
}
