/**
 * API 端点相关的 API 客户端函数
 */
// NOTE  MC80OmFIVnBZMlhscm9ua3VMazZOV3BYYkE9PTplNWViYTIyMA==

import { apiClient } from "./client";

// ==================== 类型定义 ====================
// eslint-disable  MS80OmFIVnBZMlhscm9ua3VMazZOV3BYYkE9PTplNWViYTIyMA==

export interface APIEndpoint {
  id: string;
  display_name: string;
  path: string;
  method: string;
  summary: string | null;
  description: string | null;
  tag_group: string | null;
  parameters: any[] | null;
  request_body: any | null;
  responses: any | null;
  folder_id: string | null;
  project_id: number;
  sort_order: number;
  total_test_cases: number;
  total_test_runs: number;
  last_run_status: string | null;
  api_test_ids: string[] | null;
  created_at: string;
  updated_at: string | null;
}

export interface APIEndpointListResponse {
  success: boolean;
  data: APIEndpoint[];
}

// ==================== 统一导入类型（D1-D2）====================

export type ImportSourceType =
  | "swagger_online"
  | "swagger_offline"
  | "apifox_online"
  | "apifox_offline"
  | "manual_text"
  | "manual_file";

export type ImportInputMode = "url" | "file" | "text";

export interface TagFolderSummary {
  folder_id: string;
  folder_name: string;
  endpoint_count: number;
}

export interface EndpointSummary {
  endpoint_id: string | null;
  display_name: string;
  method: string;
  path: string;
  summary: string | null;
  tag_group: string | null;
  has_auth: boolean;
  auth_type: string | null;
  has_request_body: boolean;
  has_path_params: boolean;
  has_query_params: boolean;
}

export interface ImportPreview {
  auth_summary: Record<string, number>;
  method_summary: Record<string, number>;
  path_prefixes: string[];
  warnings: string[];
}

export interface UnifiedImportResult {
  source_type: ImportSourceType;
  schema_title: string;
  schema_version: string;
  total_endpoints: number;
  total_tags: number;
  tag_folders: TagFolderSummary[];
  endpoints: EndpointSummary[];
  warnings: string[];
  errors: string[];
  preview: ImportPreview;
  created: Record<string, unknown> | null;
}

export interface UnifiedImportRequest {
  project_identifier: string;
  parent_folder_id?: string | null;
  source_type: ImportSourceType;
  url?: string;
  file_content?: Record<string, unknown>;
  text_content?: string;
  create_structure?: boolean;
}

// ==================== API 函数 ====================

// 统一响应包装（后端 SuccessResponse[T]）
interface SuccessResponse<T> {
  success: boolean;
  data: T;
}

/**
 * 统一 API 文档导入（D1-D2）
 *
 * 支持六种来源类型：
 * - swagger_online / swagger_offline
 * - apifox_online / apifox_offline
 * - manual_text / manual_file
 *
 * 注意：后端返回 SuccessResponse[UnifiedImportResult]，此处自动解包
 */
export async function unifiedImportAPI(
  request: UnifiedImportRequest
): Promise<UnifiedImportResult> {
  const response = await apiClient.post<SuccessResponse<UnifiedImportResult>>(
    "/unified-import",
    request
  );
  return response.data;
}

/**
 * 统一文件上传导入
 *
 * 使用 apiClient.form 确保继承统一超时和错误处理
 */
export async function unifiedImportFileAPI(
  formData: FormData
): Promise<UnifiedImportResult> {
  const response = await apiClient.form<SuccessResponse<UnifiedImportResult>>(
    "/unified-import-file",
    formData
  );
  return response.data;
}

/**
 * 获取项目的 API 端点列表
 */
export async function listAPIEndpoints(
  projectIdentifier: string,
  params?: {
    folder_id?: string;
    tag_group?: string;
  }
): Promise<APIEndpoint[]> {
  return apiClient.get<APIEndpoint[]>(
    `/projects/${projectIdentifier}/api-endpoints`,
    { params }
  );
}

/**
 * 获取 API 端点详情
 */
export async function getAPIEndpoint(
  endpointId: string
): Promise<APIEndpoint> {
  return apiClient.get<APIEndpoint>(
    `/api-endpoints/${endpointId}`
  );
}
// TODO  Mi80OmFIVnBZMlhscm9ua3VMazZOV3BYYkE9PTplNWViYTIyMA==

/**
 * 获取 API 端点关联的测试脚本
 */
export async function getEndpointTestScripts(
  endpointId: string
): Promise<{
  endpoint_id: string;
  test_scripts: Array<{
    id: string;
    name: string;
    identifier: string;
    script_format: string;
    script_language: string;
    total_endpoints: number;
    total_scenarios: number;
    created_at: string;
    updated_at: string;
  }>;
}> {
  return apiClient.get<{
    endpoint_id: string;
    test_scripts: Array<any>;
  }>(
    `/api-endpoints/${endpointId}/test-scripts`
  );
}

/**
 * 获取 API 端点的测试执行报告
 */
export async function getEndpointTestRuns(
  endpointId: string,
  limit: number = 10
): Promise<{
  endpoint_id: string;
  test_runs: Array<{
    id: string;
    status: string;
    total_scenarios: number;
    passed_scenarios: number;
    failed_scenarios: number;
    skipped_scenarios: number;
    duration: number;
    created_at: string;
  }>;
  total_runs: number;
  last_run_status: string | null;
}> {
  return apiClient.get<{
    endpoint_id: string;
    test_runs: Array<any>;
    total_runs: number;
    last_run_status: string | null;
  }>(
    `/api-endpoints/${endpointId}/test-runs`,
    { params: { limit } }
  );
}

/**
 * 更新 API 端点
 */
export async function updateAPIEndpoint(
  endpointId: string,
  data: {
    display_name?: string;
    path?: string;
    method?: string;
    summary?: string;
    description?: string;
    tag_group?: string;
    parameters?: any[] | null;
    request_body?: any | null;
    responses?: any | null;
    custom_config?: any;
    total_test_cases?: number;
    total_test_runs?: number;
    last_run_status?: string;
    api_test_ids?: string[];
  }
): Promise<APIEndpoint> {
  return apiClient.patch<APIEndpoint>(
    `/api-endpoints/${endpointId}`,
    data
  );
}

/**
 * 删除 API 端点
 */
export async function deleteAPIEndpoint(
  endpointId: string
): Promise<void> {
  return apiClient.delete(
    `/api-endpoints/${endpointId}`
  );
}

// ==================== Excel API 文档解析（美心模板） ====================

export interface ExcelParseInterfaceSummary {
  index: number;
  name: string | null;
  method_name: string | null;
  http_method: string | null;
  path: string | null;
  pending_count: number;
}

export interface ExcelParseTaskSummary {
  task_id: string;
  status: string;
  doc_type: string;
  error: string | null;
  attachment_id: string | null;
  interface_count: number;
  interfaces: ExcelParseInterfaceSummary[];
  created_at: string | null;
}

export interface PendingConfirmation {
  type: string;
  field: string | null;
  message: string;
  source: any;
}

export interface UnifiedField {
  name: string;
  required: boolean;
  type: string | null;
  length: number | null;
  example: string | null;
  description: string | null;
  level: string | null;
  match: string | null;
  diff: string | null;
  source: { sheet: string; row: number; col?: number; cell?: string };
}

export interface UnifiedInterface {
  name: string;
  method_name: string | null;
  http_method: string | null;
  path: string | null;
  call_url: string | null;
  title: string | null;
  summary: string | null;
  scenario: string | null;
  request: { fields: UnifiedField[] };
  response: { fields: UnifiedField[]; error_codes: { code: string; meaning: string }[] };
  auth: { type: string; sign_formula?: string; sign_fields?: string[]; ts_format?: string } | null;
  business_rules: { field: string; text: string }[];
  source: { type: string; sheet: string; attachment_id?: string; rows: number[] };
  pending_confirmations: PendingConfirmation[];
}

export interface ExcelInterfaceDetail {
  index: number;
  interface: UnifiedInterface;
}

/**
 * 上传并解析 Excel API 文档（美心模板）
 */
export async function parseExcelApiDoc(
  projectIdentifier: string,
  formData: FormData
): Promise<ExcelParseTaskSummary> {
  const response = await apiClient.form<SuccessResponse<ExcelParseTaskSummary>>(
    `/projects/${projectIdentifier}/api-docs/parse-excel`,
    formData
  );
  return response.data;
}

/**
 * 查询 Excel 解析状态与接口列表
 */
export async function getExcelParseStatus(
  projectIdentifier: string,
  taskId: string
): Promise<ExcelParseTaskSummary> {
  const response = await apiClient.get<SuccessResponse<ExcelParseTaskSummary>>(
    `/projects/${projectIdentifier}/api-docs/parse-excel/${taskId}`
  );
  return response.data;
}

/**
 * 获取单个接口的统一模型详情
 */
export async function getExcelInterfaceDetail(
  projectIdentifier: string,
  taskId: string,
  index: number
): Promise<ExcelInterfaceDetail> {
  const response = await apiClient.get<SuccessResponse<ExcelInterfaceDetail>>(
    `/projects/${projectIdentifier}/api-docs/parse-excel/${taskId}/interfaces/${index}`
  );
  return response.data;
}

/**
 * 确认导入单个接口为 APIEndpoint
 */
export async function confirmExcelInterface(
  projectIdentifier: string,
  taskId: string,
  index: number,
  body: { edited_spec?: any; parent_folder_id?: string | null }
): Promise<{ endpoint_id: string; display_name: string; method: string; path: string; folder_id: string }> {
  const response = await apiClient.post<
    SuccessResponse<{ endpoint_id: string; display_name: string; method: string; path: string; folder_id: string }>
  >(
    `/projects/${projectIdentifier}/api-docs/parse-excel/${taskId}/interfaces/${index}/confirm`,
    body
  );
  return response.data;
}

/**
 * 契约驱动：对端点生成需求分析/测试计划/脚本（不执行）
 */
export async function generateContractArtifacts(
  endpointId: string,
  envConfig: Record<string, any>
): Promise<{ analysis: any; plan: any; script: string; script_attachment_id: string; endpoint_id: string }> {
  const response = await apiClient.post<
    SuccessResponse<{ analysis: any; plan: any; script: string; script_attachment_id: string; endpoint_id: string }>
  >(
    `/api-endpoints/${endpointId}/contract-artifacts`,
    { env_config: envConfig }
  );
  return response.data;
}

/**
 * 契约驱动闭环：生成脚本并执行（缺配置返回 blocked）
 */
export async function generateAndRunEndpoint(
  projectIdentifier: string,
  endpointId: string,
  envConfig: Record<string, any>
): Promise<{ run_id: string; status: string; message?: string; artifacts?: any }> {
  const response = await apiClient.post<
    SuccessResponse<{ run_id: string; status: string; message?: string; artifacts?: any }>
  >(
    `/projects/${projectIdentifier}/api-endpoints/${endpointId}/generate-run`,
    { env_config: envConfig },
    // 该接口同步执行（生成脚本 + 真实 Playwright 调用），实测约 1 分钟，
    // 默认 30 秒超时会过早 abort。放宽到 180 秒，避免"前端已超时、后端仍在跑"的不一致。
    { timeoutMs: 180000 }
  );
  return response.data;
}
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZOV3BYYkE9PTplNWViYTIyMA==
