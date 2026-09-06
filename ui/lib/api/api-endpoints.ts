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

/**
 * 统一 API 文档导入（D1-D2）
 *
 * 支持六种来源类型：
 * - swagger_online / swagger_offline
 * - apifox_online / apifox_offline
 * - manual_text / manual_file
 */
export async function unifiedImportAPI(
  request: UnifiedImportRequest
): Promise<UnifiedImportResult> {
  return apiClient.post<UnifiedImportResult>(
    "/unified-import",
    request
  );
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
// NOTE  My80OmFIVnBZMlhscm9ua3VMazZOV3BYYkE9PTplNWViYTIyMA==
