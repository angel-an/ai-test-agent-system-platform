/**
 * API 测试相关的 API 客户端函数
 */
// eslint-disable  MC80OmFIVnBZMlhscm9ua3VMazZjR3QyUlE9PToyYzIxZWM0OQ==

import { apiClient } from "./client";
import { ApiError } from "./client";
import { t } from "@/lib/translations";

// ==================== 类型定义 ====================

export interface APITest {
  id: string;
  identifier: string;
  name: string;
  description?: string;
  schema_url?: string;
  schema_path?: string;
  schema_type: string;
  script_path: string;
  script_format: string;
  script_language: string;
  test_config?: Record<string, unknown>;
  total_endpoints: number;
  total_scenarios: number;
  created_at: string;
  updated_at?: string;
}

export interface APITestRun {
  id: string;
  identifier: string;
  status: string;
  total_tests: number;
  passed_tests: number;
  failed_tests: number;
  skipped_tests: number;
  duration_ms?: number;
  progress?: number;
  error_message?: string;
  report_path?: string;
  created_at: string;
  updated_at?: string;
}

export interface APITestResult {
  id: string;
  scenario_name: string;
  endpoint: string;
  method: string;
  status: string;
  duration_ms?: number;
  retry_count: number;
  error_message?: string;
  created_at: string;
}

export interface APITestListResponse {
  items: APITest[];
  total: number;
  page: number;
  page_size: number;
}

export interface APITestRunListResponse {
  items: APITestRun[];
  total: number;
  page: number;
  page_size: number;
}

export interface APITestResultListResponse {
  items: APITestResult[];
  total: number;
  page: number;
  page_size: number;
}

export interface CreateAPITestRequest {
  name: string;
  schema_path: string;
  script_path: string;
  script_format?: string;
  script_language?: string;
  schema_url?: string;
  description?: string;
  test_config?: Record<string, unknown>;
  total_endpoints?: number;
  total_scenarios?: number;
  generation_params?: Record<string, unknown>;
}

// ==================== API 函数 ====================
// eslint-disable  MS80OmFIVnBZMlhscm9ua3VMazZjR3QyUlE9PToyYzIxZWM0OQ==

/**
 * 获取 API 测试列表
 */
export async function listAPITests(
  projectIdentifier: string,
  params?: {
    page?: number;
    page_size?: number;
    search?: string;
    script_format?: string;
  }
): Promise<APITestListResponse> {
  return apiClient.get<APITestListResponse>(
    `/projects/${projectIdentifier}/api-tests`,
    { params }
  );
}

/**
 * 获取文件夹下的 API 测试列表
 */
export async function getFolderAPITests(
  projectIdentifier: string,
  folderId: string | null,
  params?: {
    page?: number;
    page_size?: number;
    search?: string;
  }
): Promise<APITestListResponse> {
  const queryParams = params ? new URLSearchParams() : undefined;
  if (params?.page) queryParams?.set('page', String(params.page));
  if (params?.page_size) queryParams?.set('page_size', String(params.page_size));
  if (params?.search) queryParams?.set('search', params.search);

  const url = folderId
    ? `/projects/${projectIdentifier}/folders/${folderId}/api-tests`
    : `/projects/${projectIdentifier}/api-tests`;

  return apiClient.get<APITestListResponse>(
    url,
    queryParams ? { params: Object.fromEntries(queryParams) } : undefined
  );
}

/**
 * 获取 API 测试详情
 */
export async function getAPITest(
  projectIdentifier: string,
  apiTestId: string
): Promise<APITest> {
  const response = await apiClient.get<{ data: APITest }>(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}`
  );
  return response.data;
}

/**
 * 创建 API 测试
 */
export async function createAPITest(
  projectIdentifier: string,
  data: CreateAPITestRequest
): Promise<APITest> {
  const formData = new FormData();
  formData.append("name", data.name);
  formData.append("schema_path", data.schema_path);
  formData.append("script_path", data.script_path);
  formData.append("script_format", data.script_format || "playwright");
  formData.append("script_language", data.script_language || "typescript");

  if (data.schema_url) {
    formData.append("schema_url", data.schema_url);
  }
  if (data.description) {
    formData.append("description", data.description);
  }
  if (data.test_config) {
    formData.append("test_config", JSON.stringify(data.test_config));
  }
  if (data.generation_params) {
    formData.append("generation_params", JSON.stringify(data.generation_params));
  }

  const response = await fetch(`/api/v2/projects/${projectIdentifier}/api-tests`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json();
    throw new ApiError(error.detail || t("apiTests.createFailed"), response.status, error);
  }

  const result = await response.json();
  return result.data;
}
// FIXME  Mi80OmFIVnBZMlhscm9ua3VMazZjR3QyUlE9PToyYzIxZWM0OQ==

/**
 * 更新 API 测试
 */
export async function updateAPITest(
  projectIdentifier: string,
  apiTestId: string,
  data: {
    name?: string;
    description?: string;
    test_config?: Record<string, unknown>;
  }
): Promise<APITest> {
  const response = await apiClient.patch<{ data: APITest }>(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}`,
    data
  );
  return response.data;
}

/**
 * 删除 API 测试
 */
export async function deleteAPITest(
  projectIdentifier: string,
  apiTestId: string
): Promise<void> {
  await apiClient.delete(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}`
  );
}

/**
 * 获取测试脚本
 */
export async function getTestScript(
  projectIdentifier: string,
  apiTestId: string
): Promise<string> {
  const response = await fetch(
    `/api/v2/projects/${projectIdentifier}/api-tests/${apiTestId}/script`
  );

  if (!response.ok) {
    throw new ApiError(t("apiTests.getScriptFailed"), response.status);
  }

  return await response.text();
}

/**
 * 更新测试脚本
 */
export async function updateTestScript(
  projectIdentifier: string,
  apiTestId: string,
  scriptContent: string
): Promise<void> {
  const formData = new FormData();
  formData.append("script_content", scriptContent);

  await apiClient.put(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}/script`,
    formData as unknown as Record<string, unknown>
  );
}

/**
 * 执行 API 测试
 */
export async function runAPITest(
  projectIdentifier: string,
  apiTestId: string,
  executionConfig?: Record<string, unknown>
): Promise<{ run_id: string; status: string }> {
  const response = await apiClient.post<{ data: { run_id: string; status: string } }>(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}/run`,
    { execution_config: executionConfig }
  );
  return response.data;
}

/**
 * 获取测试运行历史
 */
export async function getTestRuns(
  projectIdentifier: string,
  apiTestId: string,
  params?: {
    page?: number;
    page_size?: number;
  }
): Promise<APITestRunListResponse> {
  return apiClient.get<APITestRunListResponse>(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}/runs`,
    { params }
  );
}

/**
 * 获取测试运行详情
 */
export async function getTestRun(
  projectIdentifier: string,
  apiTestId: string,
  runId: string
): Promise<APITestRun> {
  const response = await apiClient.get<{ data: APITestRun }>(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}/runs/${runId}`
  );
  return response.data;
}
// eslint-disable  My80OmFIVnBZMlhscm9ua3VMazZjR3QyUlE9PToyYzIxZWM0OQ==

/**
 * 获取测试结果
 */
export async function getTestResults(
  projectIdentifier: string,
  apiTestId: string,
  runId: string,
  params?: {
    page?: number;
    page_size?: number;
  }
): Promise<APITestResultListResponse> {
  return apiClient.get<APITestResultListResponse>(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}/runs/${runId}/results`,
    { params }
  );
}

/**
 * 上传 Schema 文件
 */
export async function uploadSchema(
  projectIdentifier: string,
  file: File
): Promise<{ schema_path: string; schema_url?: string }> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(
    `/api/v2/projects/${projectIdentifier}/api-tests/upload-schema`,
    {
      method: "POST",
      body: formData,
    }
  );

  if (!response.ok) {
    const error = await response.json();
    throw new ApiError(error.detail || t("apiTests.uploadFailed"), response.status, error);
  }

  const result = await response.json();
  return result.data;
}

/**
 * 从 Schema 生成 API 测试（AI 生成）
 */
export async function generateFromSchema(
  projectIdentifier: string,
  params: {
    schema_url?: string;
    schema_path?: string;
    script_format?: string;
    script_language?: string;
    include_auth?: boolean;
    include_security?: boolean;
    include_error_handling?: boolean;
  }
): Promise<{
  test_plan: string;
  test_script: string;
  statistics: {
    total_endpoints: number;
    total_scenarios: number;
  };
}> {
  const formData = new FormData();
  if (params.schema_url) {
    formData.append("schema_url", params.schema_url);
  }
  if (params.schema_path) {
    formData.append("schema_path", params.schema_path);
  }
  formData.append("script_format", params.script_format || "playwright");
  formData.append("script_language", params.script_language || "typescript");
  formData.append("include_auth", String(params.include_auth ?? true));
  formData.append("include_security", String(params.include_security ?? false));
  formData.append("include_error_handling", String(params.include_error_handling ?? true));

  const response = await fetch(
    `/api/v2/projects/${projectIdentifier}/api-tests/generate-from-schema`,
    {
      method: "POST",
      body: formData,
    }
  );

  if (!response.ok) {
    const error = await response.json();
    throw new ApiError(error.detail || t("apiTests.aiGenerateFailed"), response.status, error);
  }

  const result = await response.json();
  return result.data;
}

// ==================== 意图编译 API ====================

/**
 * 编译 API 测试意图（自然语言 → 结构化配置）
 */
export async function compileApiTestIntent(
  projectIdentifier: string,
  naturalLanguage: string,
  endpointId?: string | null,
  baseUrl?: string,
): Promise<{
  success: boolean;
  intent_type: string;
  intent: Record<string, unknown>;
  errors: string[];
  warnings: string[];
}> {
  const result = await apiClient.post<any>(
    `/projects/${projectIdentifier}/api-tests/compile-intent`,
    {
        natural_language: naturalLanguage,
        endpoint_id: endpointId,
        base_url: baseUrl,
    }
  );
  // 后端返回 SuccessResponse { success, data, message }
  // 解包 data 字段
  return result.data || result;
}

/**
 * 预览 API 测试配置
 */
export async function previewApiTestConfig(
  projectIdentifier: string,
  intent: Record<string, unknown>,
): Promise<{
  intent: Record<string, unknown>;
  request_preview?: Record<string, unknown>;
  variable_report: Record<string, unknown>;
  auth_report?: Record<string, unknown>;
  assertion_preview: Array<Record<string, unknown>>;
  execution_plan?: string;
}> {
  const result = await apiClient.post<any>(
    `/projects/${projectIdentifier}/api-tests/preview-config`,
    { intent }
  );
  return result.data || result;
}

/**
 * API 分层执行结果
 */
export interface APILayerResult {
  layer: string;
  layer_name: string;
  status: string;
  duration_ms: number;
  stdout?: string;
  stderr?: string;
  error_message?: string;
  test_count: number;
  passed_count: number;
  failed_count: number;
  skipped_count: number;
}

/**
 * API 分层配置状态
 */
export interface APILayerConfig {
  layer: string;
  layer_name: string;
  enabled: boolean;
  requires_live: boolean;
  is_risky: boolean;
}

/**
 * 门控状态
 */
export interface GateStatus {
  live_api: boolean;
  live_web: boolean;
  run_risky_negative: boolean;
  auth_negative: boolean;
  fail_closed: boolean;
  safe_mode: boolean;
}

/**
 * 获取 API 分层配置状态
 */
export async function getAPILayerConfig(
  projectIdentifier: string
): Promise<{
  layers: APILayerConfig[];
  gate_status: GateStatus;
  safe_mode: boolean;
}> {
  const response = await apiClient.get<{
    data: {
      layers: APILayerConfig[];
      gate_status: GateStatus;
      safe_mode: boolean;
    };
  }>(`/projects/${projectIdentifier}/api-layers`);
  return response.data;
}

/**
 * 执行 API 分层测试
 */
export async function executeAPILayers(
  projectIdentifier: string,
  params?: {
    layers?: string[];
    live_api?: boolean;
    run_risky?: boolean;
  }
): Promise<APILayerResult[]> {
  const response = await apiClient.post<{
    data: APILayerResult[];
  }>(`/projects/${projectIdentifier}/api-layers/execute`, params || {});
  return response.data;
}

/**
 * 执行单层 API 测试
 */
export async function executeSingleAPILayer(
  projectIdentifier: string,
  layer: string,
  params?: {
    live_api?: boolean;
    run_risky?: boolean;
  }
): Promise<APILayerResult> {
  const response = await apiClient.post<{
    data: APILayerResult;
  }>(`/projects/${projectIdentifier}/api-layers/${layer}/execute`, params || {});
  return response.data;
}

/**
 * 执行 API 测试意图（直接执行，不走脚本）
 */
export async function executeApiTestIntent(
  projectIdentifier: string,
  intent: Record<string, unknown>,
): Promise<{
  success: boolean;
  run_id?: string;
  status: string;
  duration_ms?: number;
  request?: Record<string, unknown>;
  response?: Record<string, unknown>;
  assertion_results: Array<Record<string, unknown>>;
  extracted_variables: Record<string, unknown>;
  error_message?: string;
  error_type?: string;
}> {
  const response = await fetch(
    `/api/v2/projects/${projectIdentifier}/api-tests/execute-intent`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intent }),
    }
  );

  if (!response.ok) {
    const error = await response.json();
    throw new ApiError(error.detail || "意图执行失败", response.status, error);
  }

  const result = await response.json();
  return result.data || result;
}

/**
 * 更新 API 测试配置
 */
export async function updateAPITestConfig(
  projectIdentifier: string,
  apiTestId: string,
  config: Record<string, unknown>,
): Promise<APITest> {
  const response = await apiClient.patch<{ data: APITest }>(
    `/projects/${projectIdentifier}/api-tests/${apiTestId}/config`,
    { config }
  );
  return response.data;
}
