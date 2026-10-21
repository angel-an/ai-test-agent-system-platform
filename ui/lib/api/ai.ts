import { apiClient } from "./client";
import type { SuccessResponse, TestCaseInfo } from "./types";
// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZNRkJ1YUE9PTpjYTE5YmM4Yw==

// AI生成测试用例的请求参数
export interface AIGenerateTestCasesRequest {
  prompt: string;
  folder_id?: string | null;
  count?: number;
  template?: "test_case" | "test_case_bdd";
}

// AI生成测试用例的响应
export interface AIGenerateTestCasesResponse {
  success: boolean;
  test_cases: TestCaseInfo[];
  message?: string;
}
// TODO  MS80OmFIVnBZMlhscm9ua3VMazZNRkJ1YUE9PTpjYTE5YmM4Yw==

// 从文档/图片生成测试用例的请求参数
export interface AIGenerateFromDocumentRequest {
  file: File;
  folder_id?: string | null;
  additional_prompt?: string;
  template?: "test_case" | "test_case_bdd";
  knowledge_space_id?: string;
  /** 输出类型：test_case 生成用例 / requirement_analysis 只输出需求分析报告 */
  output_type?: "test_case" | "requirement_analysis";
  test_mode?: "sit" | "uat" | "smoke" | "api";
  /** 需求编号，写入用例追踪字段（阶段二） */
  requirement_id?: string;
  /** 生成会话标识，用于门控状态跟踪 */
  session_key?: string;
  /** 结构化需求分析结果（LLM 输出）。解析失败时后端自动降级 Incomplete 并阻断生成 */
  analysis_payload?: unknown;
  override_token?: string;
  override_reason?: string;
}

/** 需求质量门控返回片段（后端在 generate-from-document 响应中附加） */
export interface AIGenerateQualityGateInfo {
  prd_quality?: {
    status: "Complete" | "Incomplete" | "Blocked";
    status_label: string;
    can_generate_testcases: boolean;
    missing_count: number;
    ambiguous_count: number;
    blocked_count: number;
  };
  analysis_report?: {
    status: "Complete" | "Incomplete" | "Blocked";
    status_label: string;
    can_generate_testcases: boolean;
    blocked_reasons: string[];
    missing_items: Array<{ item: string; kind: string; detail: string }>;
    ambiguous_items: Array<{ item: string; kind: string; detail: string }>;
    flagged_items: Array<{ item: string; kind: string; detail: string }>;
    affected_features: string[];
    parse_error?: string | null;
  };
  testcase_generation?: {
    status: "not_started" | "blocked" | "generated" | "rejected";
    reasons: string[];
    created_count: number;
  };
  analysis_report_markdown?: string;
  /** 生成期断言缺口告警：哪些需求点提取不出可判定断言（null = 无缺口） */
  assertion_gap_warning?: string | null;
  /** 断言缺口明细：需求点 → 受影响用例 → 原因与补充建议 */
  assertion_gap_cases?: Array<{
    point: string;
    titles: string[];
    reason: string;
    suggestion: string;
  }>;
  /** 逐需求点可断言性预览（true = 可关联字段规则、能产出可判定断言） */
  assertability_preview?: Array<{
    point: string;
    assertable: boolean;
    matched_field?: string | null;
  }>;
}

// AI辅助填充测试用例字段的请求参数
export interface AIAssistFieldRequest {
  field: "description" | "preconditions" | "steps" | "feature" | "scenario" | "background";
  context: {
    title?: string;
    description?: string;
    preconditions?: string;
    existing_steps?: Array<{ step: string; result?: string }>;
  };
  prompt?: string;
}

// AI辅助填充字段的响应
export interface AIAssistFieldResponse {
  success: boolean;
  content: string | Array<{ step: string; result: string }>;
  message?: string;
}
// TODO  Mi80OmFIVnBZMlhscm9ua3VMazZNRkJ1YUE9PTpjYTE5YmM4Yw==

export interface ParsedAPIEndpointInfo {
  endpoint_id?: string;
  id?: string;
  method?: string;
  path?: string;
  display_name?: string;
}

export interface APIDocParseFileResponse {
  draft?: {
    base_url?: string | null;
    endpoints?: Array<Record<string, unknown>>;
  };
  openapi?: Record<string, unknown>;
  created?: {
    endpoints?: ParsedAPIEndpointInfo[];
    total_endpoints?: number;
  } | null;
  file?: {
    filename?: string;
    content_type?: string;
    size?: number;
  };
}

export interface ParseAPIDocFileRequest {
  file: File;
  title?: string;
  parent_folder_id?: string | null;
  create_structure?: boolean;
}

export interface MinimalAPILoopRequest {
  endpoint_ids: string[];
  folder_id?: string | null;
  case_kind?: "sit" | "uat" | "smoke" | "api";
  base_url?: string | null;
  create_test_run?: boolean;
  execution_config?: Record<string, unknown>;
}

export interface MinimalAPILoopResponse {
  project_identifier: string;
  test_cases: Array<{
    id: string;
    identifier: string;
    name: string;
    endpoint_id: string;
  }>;
  api_tests: Array<{
    id: string;
    identifier: string;
    name: string;
    script_path?: string;
    endpoint_id: string;
    test_case_id: string;
  }>;
  test_run?: {
    id: string;
    identifier: string;
    script_jobs?: Array<Record<string, unknown>>;
  } | null;
  message?: string;
}

export interface MinimalWebLoopRequest {
  test_case_ids: string[];
  folder_id?: string | null;
  base_url?: string | null;
  test_run_identifier?: string | null;
  create_test_run?: boolean;
  execution_config?: Record<string, unknown>;
  /** Web 子功能 ID（执行三要素绑定目标）；不传时后端按用例文本自动匹配 */
  sub_function_id?: string | null;
}

export interface MinimalWebLoopResponse {
  project_identifier: string;
  test_cases: Array<{
    id: string;
    identifier: string;
    name: string;
  }>;
  web_tests: Array<{
    id: string;
    identifier: string;
    name: string;
    script_path?: string;
    test_case_id: string;
  }>;
  test_run?: {
    id: string;
    identifier: string;
    script_jobs?: Array<Record<string, unknown>>;
  } | null;
  message?: string;
  /** 编译回执（A2/rev97）：本次生成了几条可执行检查、丢了什么、为什么。
   *  warnings 非空或 executable=false 时 UI 必须持久展示——只看"已生成 N 条 Web 冒烟"
   *  会把"零覆盖骨架"误读成"已覆盖"（资产状态仍显示 completed）。 */
  compile_receipt?: WebCompileReceipt | null;
}

export interface WebCompileReceipt {
  form_locator_source?: string;
  executable?: boolean;
  form_executable_checks?: number;
  negative_scenario_count?: number;
  assertion_check_count?: number;
  skipped_assertion_count?: number;
  locator_depended_assertion_count?: number;
  allow_upload_checks?: boolean;
  warnings?: string[];
}

// 从提示词生成测试用例
export function generateTestCasesFromPrompt(
  projectId: string,
  data: AIGenerateTestCasesRequest
) {
  return apiClient.post<AIGenerateTestCasesResponse>(
    `/projects/${projectId}/ai/generate-test-cases`,
    data
  );
}

/**
 * 构造「从文档生成」的表单字段。
 *
 * 抽成纯函数以便脱离浏览器环境验证字段是否真正提交（阶段一/二门控字段容易漏传）。
 */
export function buildGenerateFromDocumentForm(
  data: AIGenerateFromDocumentRequest
): Record<string, string | Blob> {
  const fields: Record<string, string | Blob> = { file: data.file };

  if (data.folder_id) fields.folder_id = data.folder_id;
  if (data.additional_prompt) fields.additional_prompt = data.additional_prompt;
  if (data.template) fields.template = data.template;
  if (data.test_mode) fields.test_mode = data.test_mode;
  if (data.knowledge_space_id) fields.knowledge_space_id = data.knowledge_space_id;

  // 需求质量门控 + 追踪字段（阶段一/二）
  if (data.output_type) fields.output_type = data.output_type;
  if (data.requirement_id) fields.requirement_id = data.requirement_id;
  if (data.session_key) fields.session_key = data.session_key;
  if (data.analysis_payload !== undefined && data.analysis_payload !== null) {
    fields.analysis_payload =
      typeof data.analysis_payload === "string"
        ? data.analysis_payload
        : JSON.stringify(data.analysis_payload);
  }

  return fields;
}

// 从文档/图片生成测试用例
export async function generateTestCasesFromDocument(
  projectId: string,
  data: AIGenerateFromDocumentRequest
) {
  const formData = new FormData();
  for (const [key, value] of Object.entries(buildGenerateFromDocumentForm(data))) {
    formData.append(key, value as string | Blob);
  }

  if (data.override_token) formData.append("override_token", data.override_token);
  if (data.override_reason) formData.append("override_reason", data.override_reason);

  return apiClient.form<AIGenerateTestCasesResponse & AIGenerateQualityGateInfo>(
    `/projects/${projectId}/ai/generate-from-document`,
    formData
  );
}

export async function parseAPIDocFile(
  projectId: string,
  data: ParseAPIDocFileRequest
) {
  const formData = new FormData();
  formData.append("file", data.file);
  if (data.title) {
    formData.append("title", data.title);
  }
  if (data.parent_folder_id) {
    formData.append("parent_folder_id", data.parent_folder_id);
  }
  formData.append("create_structure", String(data.create_structure ?? true));

  return apiClient.form<SuccessResponse<APIDocParseFileResponse>>(
    `/projects/${projectId}/api-docs/parse-file`,
    formData
  );
}

export function createMinimalAPILoop(
  projectId: string,
  data: MinimalAPILoopRequest
) {
  return apiClient.post<SuccessResponse<MinimalAPILoopResponse>>(
    `/projects/${projectId}/api-case-links/minimal-loop`,
    data
  );
}

export function createMinimalWebLoop(
  projectId: string,
  data: MinimalWebLoopRequest
) {
  return apiClient.post<SuccessResponse<MinimalWebLoopResponse>>(
    `/projects/${projectId}/web-case-links/minimal-loop`,
    data
  );
}

// AI辅助填充测试用例字段
export function aiAssistField(
  projectId: string,
  data: AIAssistFieldRequest
) {
  return apiClient.post<AIAssistFieldResponse>(
    `/projects/${projectId}/ai/assist-field`,
    data
  );
}
// TODO  My80OmFIVnBZMlhscm9ua3VMazZNRkJ1YUE9PTpjYTE5YmM4Yw==
